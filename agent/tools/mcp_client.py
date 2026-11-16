"""
ERPNext MCP Client.

Provides a robust, thread-safe, reusable STDIO client to communicate with the
official ERPNext MCP server (mcp_erpnext.server) using the Python MCP SDK.

Manages connection lifecycle, tool discovery, argument invocation, result envelope
preservation, and secure credential masking without leaking sensitive information.
"""

import atexit
import asyncio
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from mcp_erpnext.auth import get_request_context
from mcp_erpnext.errors import ERPNextConfigError

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import mcp.types as types
except ImportError as err:
    raise ImportError(
        f"The official MCP Python SDK is required for ERPNext MCP client: {err}. "
        "Please install 'mcp' in the Python environment."
    ) from err

logger = logging.getLogger("agent.tools.mcp_client")
if not logger.handlers:
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)
logger.setLevel(logging.INFO)

SENSITIVE_PATTERNS = [
    re.compile(r"(token\s+)([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+)([a-zA-Z0-9._-]+)", re.IGNORECASE),
    re.compile(r"(api_secret['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
    re.compile(r"(api_key['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
    re.compile(r"(password['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
]


def mask_sensitive(text: Any) -> str:
    """Mask credentials, tokens, and sensitive patterns in log messages and errors."""
    if not isinstance(text, str):
        text = str(text)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class MCPClientError(RuntimeError):
    """Base error for MCP client operations."""
    pass


class MCPConnectionError(MCPClientError):
    """Raised when connecting to or communicating with the MCP server fails."""
    pass


class MCPTimeoutError(MCPClientError):
    """Raised when an MCP operation times out."""
    pass


class MCPToolError(MCPClientError):
    """Raised when an MCP tool execution fails or returns an invalid response."""
    pass


# Active client registry for clean shutdown at process exit
_ACTIVE_CLIENTS: Set["ERPNextMCPClient"] = set()
_ACTIVE_CLIENTS_LOCK = threading.Lock()


def _cleanup_all_clients() -> None:
    with _ACTIVE_CLIENTS_LOCK:
        clients = list(_ACTIVE_CLIENTS)
    for c in clients:
        try:
            c.close()
        except Exception as exc:
            logger.debug("Error during atexit MCP client cleanup: %s", exc)


atexit.register(_cleanup_all_clients)


class ERPNextMCPClient:
    """
    Reusable, thread-safe STDIO client for ERPNext MCP server.

    Runs an internal background worker thread with its own asyncio event loop
    so that synchronous callers (such as LangGraph nodes or FastAPI workers)
    can execute tool calls safely without event loop conflicts or subprocess leaks.
    """

    def __init__(
        self,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ):
        self._command = command or os.getenv("MCP_SERVER_COMMAND") or sys.executable
        if args is not None:
            self._args = list(args)
        else:
            env_args = os.getenv("MCP_SERVER_ARGS")
            if env_args:
                self._args = env_args.split()
            else:
                self._args = ["-m", "mcp_erpnext.server", "--transport", "stdio"]

        self._custom_env = dict(env) if env is not None else None

        if timeout is not None:
            self._timeout = float(timeout)
        else:
            self._timeout = float(os.getenv("MCP_TIMEOUT", "30.0"))

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._queue: Optional[asyncio.Queue] = None
        self._tools: Dict[str, Any] = {}
        self._connected = False
        self._closed = False
        self._lock = threading.Lock()

        with _ACTIVE_CLIENTS_LOCK:
            _ACTIVE_CLIENTS.add(self)

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._closed

    def _build_env(self) -> Dict[str, str]:
        child_env = dict(os.environ)
        if self._custom_env:
            child_env.update(self._custom_env)

        # Ensure repo root and agent directory are in PYTHONPATH for mcp_erpnext discovery
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        agent_dir = str(Path(__file__).resolve().parent.parent)
        curr_pythonpath = child_env.get("PYTHONPATH", "")
        paths_to_add = [p for p in (repo_root, agent_dir) if p not in curr_pythonpath.split(os.pathsep)]
        if paths_to_add:
            prefix = os.pathsep.join(paths_to_add)
            child_env["PYTHONPATH"] = f"{prefix}{os.pathsep}{curr_pythonpath}" if curr_pythonpath else prefix

        return child_env

    def connect(self) -> List[str]:
        """
        Start the MCP server subprocess and initialize the client session.
        Returns the list of registered tool names.
        """
        with self._lock:
            if self._connected and not self._closed:
                return list(self._tools.keys())
            if self._closed:
                raise MCPConnectionError("MCPClient is closed and cannot be reconnected. Create a new instance.")

            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name="MCPClientWorkerThread",
            )
            self._thread.start()

            ready_fut: Future = Future()
            stopped_fut: Future = Future()

            server_params = StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._build_env(),
            )

            async def _session_runner() -> None:
                """
                Keeps the entire lifecycle of stdio_client and ClientSession inside
                a single async task to prevent AnyIO cancel scope task-mismatch errors.
                """
                try:
                    async with stdio_client(server_params) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            tools_result = await session.list_tools()
                            self._tools = {t.name: t for t in tools_result.tools}
                            self._queue = asyncio.Queue()
                            self._connected = True
                            ready_fut.set_result(list(self._tools.keys()))

                            while True:
                                item = await self._queue.get()
                                if item is None:
                                    # Shutdown sentinel received
                                    break
                                tool_name, args, req_fut = item
                                try:
                                    res = await session.call_tool(tool_name, args)
                                    if not req_fut.done():
                                        req_fut.set_result(res)
                                except Exception as exc:
                                    if not req_fut.done():
                                        req_fut.set_exception(exc)
                except Exception as exc:
                    self._connected = False
                    if not ready_fut.done():
                        ready_fut.set_exception(exc)
                    logger.error("MCP session runner encountered error: %s", mask_sensitive(str(exc)))
                finally:
                    self._connected = False
                    stopped_fut.set_result(True)

            self._stopped_fut = stopped_fut
            asyncio.run_coroutine_threadsafe(_session_runner(), self._loop)

            try:
                tool_names = ready_fut.result(timeout=self._timeout)
                logger.info("Connected to ERPNext MCP server. Available tools: %s", tool_names)
                return tool_names
            except FutureTimeoutError as exc:
                self.close()
                msg = f"Timed out after {self._timeout}s waiting for MCP server initialization."
                logger.error(msg)
                raise MCPTimeoutError(msg) from exc
            except Exception as exc:
                self.close()
                clean_err = mask_sensitive(str(exc))
                msg = f"Failed to connect to ERPNext MCP server: {clean_err}"
                logger.error(msg)
                raise MCPConnectionError(msg) from exc

    def list_tools(self) -> List[str]:
        """Return the list of tool names discovered on the MCP server."""
        if not self._connected:
            return self.connect()
        return list(self._tools.keys())

    def is_tool_registered(self, tool_name: str) -> bool:
        """Check whether a tool name is registered on the MCP server."""
        if not self._connected:
            self.connect()
        return tool_name in self._tools

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute an MCP tool on the server and return the parsed ERPNext result envelope.

        Preserves existing response envelopes ({status, data, error, has_more, success})
        and pagination metadata ({total_count, offset, limit, truncated}).
        """
        # Guard: refuse whenever a user RequestContext is active, regardless of ERPNEXT_STRICT_IDENTITY
        ctx = get_request_context()
        if ctx is not None and not getattr(ctx, "is_service", False) and not getattr(ctx, "dev_mock", False):
            raise ERPNextConfigError(
                "ERPNextMCPClient (STDIO) refuses to execute user request: active user RequestContext "
                "cannot be served by subprocess service credentials."
            )

        if not self._connected:
            self.connect()

        if not self.is_tool_registered(tool_name):
            available = sorted(list(self._tools.keys()))
            raise MCPToolError(
                f"Tool '{tool_name}' is not registered on ERPNext MCP server. Registered tools: {available}"
            )

        args = arguments or {}
        req_fut: Future = Future()

        with self._lock:
            if not self._connected or self._queue is None or self._loop is None:
                raise MCPConnectionError("MCPClient connection is no longer active.")
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (tool_name, args, req_fut))

        try:
            mcp_res = req_fut.result(timeout=self._timeout)
        except FutureTimeoutError as exc:
            msg = f"Invocation of MCP tool '{tool_name}' timed out after {self._timeout}s."
            logger.error(msg)
            raise MCPTimeoutError(msg) from exc
        except Exception as exc:
            clean_err = mask_sensitive(str(exc))
            msg = f"MCP tool '{tool_name}' invocation failed: {clean_err}"
            logger.error(msg)
            raise MCPToolError(msg) from exc

        return self._parse_tool_result(tool_name, mcp_res)

    def _parse_tool_result(self, tool_name: str, result: Any) -> Dict[str, Any]:
        """
        Parse MCP CallToolResult and extract the ERPNext response envelope.
        """
        content_items = getattr(result, "content", None)
        if not content_items:
            raise MCPToolError(f"MCP server returned empty content for tool '{tool_name}'.")

        text_content = ""
        for item in content_items:
            if getattr(item, "type", None) == "text" or hasattr(item, "text"):
                text_content = getattr(item, "text", "")
                break

        if not text_content:
            raise MCPToolError(f"MCP server returned no text content for tool '{tool_name}'.")

        try:
            parsed = json.loads(text_content)
        except Exception as exc:
            clean_text = mask_sensitive(text_content)
            logger.error("Failed to parse JSON response from MCP tool '%s': %s", tool_name, clean_text)
            return {
                "status": "system_error",
                "success": False,
                "data": None,
                "error": f"Invalid JSON response from MCP server: {clean_text}",
                "has_more": False,
            }

        if not isinstance(parsed, dict):
            return {
                "status": "system_error",
                "success": False,
                "data": parsed,
                "error": "Unexpected non-dictionary response envelope from MCP server.",
                "has_more": False,
            }

        # Normalize success boolean if missing
        if "success" not in parsed and "status" in parsed:
            parsed["success"] = parsed["status"] in ("success", "empty")

        return parsed

    def close(self) -> None:
        """Cleanly close session, terminate subprocess, and shut down worker thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connected = False

            if self._loop and self._loop.is_running() and self._queue:
                try:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
                except Exception:
                    pass

            if hasattr(self, "_stopped_fut"):
                try:
                    self._stopped_fut.result(timeout=3.0)
                except Exception:
                    pass

            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3.0)

            if self._loop and not self._loop.is_closed():
                try:
                    self._loop.close()
                except Exception:
                    pass

            with _ACTIVE_CLIENTS_LOCK:
                _ACTIVE_CLIENTS.discard(self)

    def __enter__(self) -> "ERPNextMCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"ERPNextMCPClient(command={self._command!r}, connected={self._connected}, "
            f"timeout={self._timeout}, tools_count={len(self._tools)})"
        )


MCPClient = ERPNextMCPClient
