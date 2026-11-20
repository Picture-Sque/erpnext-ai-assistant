"""
ERPNext Tool Adapter.

Acts as the routing boundary between the LangGraph workflow and the tool execution backends:
- direct: Existing direct ERPNext Python tool implementations in generic_tools.py (default rollback path).
- mcp: ERPNext MCP client communicating with the secure MCP server.

Enforces:
- Backend selection via TOOL_BACKEND (default: 'direct', rejects invalid values).
- Eligible read operations route through MCP when TOOL_BACKEND=mcp.
- All write operations (create, update, delete, cancel, submit) MUST ALWAYS remain direct.
- Write-verification reads in result_validation_node MUST remain direct.
- Preserves exact tool function names, signatures, default parameters, and result envelopes.
- Fails closed with compatible system_error envelopes on MCP transport errors without silent direct fallback.
"""

import atexit
import contextvars
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from mcp_erpnext.auth import get_request_context, RequestContext
from mcp_erpnext.client import is_strict_identity
from mcp_erpnext.errors import ERPNextConfigError
from mcp_erpnext.server import InMemoryMCPClient

try:
    from agent.tools import generic_tools
    from agent.tools.mcp_client import (
        ERPNextMCPClient,
        MCPClientError,
        mask_sensitive,
    )
except ImportError:
    from tools import generic_tools
    from tools.mcp_client import (
        ERPNextMCPClient,
        MCPClientError,
        mask_sensitive,
    )

logger = logging.getLogger("agent.tools.adapter")
if not logger.handlers:
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)
logger.setLevel(logging.INFO)

VALID_BACKENDS = ("direct", "mcp")

# Request-scoped context variable to identify write-verification reads
_write_verification_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "adapter_write_verification", default=False
)


class write_verification_scope:
    """
    Context manager to flag that enclosed get_document calls are performing
    write-verification and MUST remain on the direct backend.
    """

    def __enter__(self) -> "write_verification_scope":
        self._token = _write_verification_ctx.set(True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        _write_verification_ctx.reset(self._token)


def set_write_verification(enabled: bool) -> contextvars.Token:
    """Set the write-verification flag for the current context."""
    return _write_verification_ctx.set(enabled)


def reset_write_verification(token: contextvars.Token) -> None:
    """Reset the write-verification flag."""
    _write_verification_ctx.reset(token)


def resolve_backend(backend: Optional[str] = None) -> str:
    """
    Resolve and validate the configured tool backend.
    Defaults to 'direct' when TOOL_BACKEND is not set or empty.
    Fails closed with RuntimeError on invalid backend values.
    """
    raw_val = backend if backend is not None else os.getenv("TOOL_BACKEND", "direct")
    val = (raw_val or "direct").strip().lower()
    if val not in VALID_BACKENDS:
        raise RuntimeError(
            f"Invalid TOOL_BACKEND configuration: '{raw_val}'. Allowed values are 'direct' or 'mcp'."
        )
    return val


class ToolAdapter:
    """
    Adapter providing uniform access to ERPNext operations across direct and MCP backends.
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        mcp_client: Optional[Any] = None,
    ):
        self._backend = resolve_backend(backend)
        self._mcp_client = mcp_client
        self._mcp_initialized = False
        self._in_memory_client: Optional[InMemoryMCPClient] = None

    @property
    def backend(self) -> str:
        return self._backend

    @backend.setter
    def backend(self, val: str) -> None:
        self._backend = resolve_backend(val)

    @backend.deleter
    def backend(self) -> None:
        self._backend = "direct"

    def set_backend(self, backend: str) -> None:
        """Dynamically update backend for testing or runtime reconfiguration."""
        self._backend = resolve_backend(backend)

    def _get_mcp_client(self) -> Any:
        """
        Lazily initialize MCPClient when backend is 'mcp'.
        If an explicit client was provided, use it.
        Otherwise, default to in-process InMemoryMCPClient preserving user context.
        Fails closed if no active RequestContext exists for chat tool execution.
        """
        if self._mcp_client is not None:
            if not self._mcp_initialized:
                if hasattr(self._mcp_client, "connect"):
                    self._mcp_client.connect()
                self._mcp_initialized = True
            return self._mcp_client

        ctx = get_request_context()
        if ctx is None:
            allow_mock = os.getenv("ALLOW_DEV_MOCK_AUTH", "false").strip().lower() in ("true", "1")
            strict = is_strict_identity(default=False)
            if not strict and allow_mock:
                from mcp_erpnext.server import get_default_context
                ctx = get_default_context()
            else:
                raise ERPNextConfigError(
                    "Strict identity enforcement: no active user RequestContext found for chat tool execution."
                )

        if self._in_memory_client is None:
            self._in_memory_client = InMemoryMCPClient()
        return self._in_memory_client

    def close(self) -> None:
        """Cleanly close any active MCP client resources."""
        if self._mcp_client is not None:
            try:
                self._mcp_client.close()
            except Exception as exc:
                logger.debug("Error closing MCP client in adapter: %s", exc)
            self._mcp_initialized = False
        if self._in_memory_client is not None:
            try:
                self._in_memory_client.close()
            except Exception as exc:
                logger.debug("Error closing in-memory client in adapter: %s", exc)
            self._in_memory_client = None

    def _invoke_mcp(self, tool_name: str, args: Dict[str, Any], doctype: str = "") -> dict:
        """
        Invoke an MCP tool with structured lifecycle logging and typed error envelope handling.
        """
        ctx = get_request_context()
        user_id = ctx.user_id if ctx and ctx.user_id else "unknown"
        transport = "stdio" if self._mcp_client is not None else "in-process"
        logger.info("MCP call: tool=%s, user_id=%s, transport=%s", tool_name, user_id, transport)

        try:
            client = self._get_mcp_client()
            res = client.call_tool(tool_name, args)
            status = res.get("status") if isinstance(res, dict) else "unknown"
            logger.info("MCP call completed: tool=%s, status=%s", tool_name, status)
            if isinstance(res, dict) and status in ("permission_denied", "error"):
                reason = res.get("error") or res.get("message") or "policy_denied"
                logger.warning(
                    "MCP policy denial: tool=%s, user_id=%s, reason=%s",
                    tool_name,
                    user_id,
                    mask_sensitive(str(reason)),
                )
            return res
        except Exception as exc:
            clean_err = mask_sensitive(str(exc))
            logger.error("MCP %s failed for %s: %s", tool_name, doctype, clean_err)
            return {
                "status": "system_error",
                "success": False,
                "data": None,
                "error": f"MCP execution failed: {clean_err}",
                "has_more": False,
            }

    # -------------------------------------------------------------------------
    # Supporting helpers (always direct to preserve local logic and resolution)
    # -------------------------------------------------------------------------

    def is_doctype_allowed(self, doctype_name: str) -> bool:
        return generic_tools.is_doctype_allowed(doctype_name)

    def get_allowed_doctypes(self) -> List[str]:
        return generic_tools.get_allowed_doctypes()

    def get_default_company(self, reload: bool = False) -> str:
        """Preserves existing RuntimeError exception semantics for call sites."""
        return generic_tools.get_default_company(reload=reload)

    def get_default_warehouse(self, company: Optional[str] = None, reload: bool = False) -> str:
        """Preserves existing RuntimeError exception semantics for call sites."""
        return generic_tools.get_default_warehouse(company=company, reload=reload)

    # -------------------------------------------------------------------------
    # Read operations (MCP-eligible when backend=mcp, direct fallback disallowed)
    # -------------------------------------------------------------------------

    def get_list(
        self,
        doctype: str,
        filters: Optional[dict] = None,
        fields: Optional[list] = None,
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        offset: Optional[int] = 0,
        include_total: bool = False,
        group_by: Optional[str] = None,
        parent_doctype: Optional[str] = None,
        or_filters: Optional[list] = None,
    ) -> dict:
        if self._backend == "direct":
            return generic_tools.get_list(
                doctype=doctype,
                filters=filters,
                fields=fields,
                limit=limit,
                order_by=order_by,
                offset=offset,
                include_total=include_total,
            )

        # Route through MCP
        args: Dict[str, Any] = {"doctype": doctype}
        if filters:
            args["filters"] = filters
        if fields is not None:
            args["fields"] = fields
        if limit is not None:
            args["limit"] = limit
        if order_by is not None:
            args["order_by"] = order_by
        if offset is not None:
            args["offset"] = offset
        if include_total:
            args["include_total"] = include_total
        if group_by is not None:
            args["group_by"] = group_by
        if parent_doctype is not None:
            args["parent_doctype"] = parent_doctype
        if or_filters is not None:
            args["or_filters"] = or_filters

        return self._invoke_mcp("get_list", args, doctype)

    def get_document(self, doctype: str, name: str, *, verification: Optional[bool] = None) -> dict:
        """
        Fetch a document by DocType and name.

        CRITICAL WRITE-VERIFICATION RULE:
        Write-verification reads (either via verification=True or inside write_verification_scope)
        MUST ALWAYS remain direct.
        Ordinary user-facing reads route through MCP when backend=mcp.
        """
        is_verification = verification if verification is not None else _write_verification_ctx.get()
        if is_verification or self._backend == "direct":
            return generic_tools.get_document(doctype=doctype, name=name)

        return self._invoke_mcp("get_document", {"doctype": doctype, "name": name}, doctype)

    def get_document_direct(self, doctype: str, name: str) -> dict:
        """Explicitly direct read for write-verification in result_validation_node."""
        return generic_tools.get_document(doctype=doctype, name=name)

    def search_document(self, doctype: str, query: str, limit: int = 10) -> dict:
        if self._backend == "direct":
            return generic_tools.search_document(doctype=doctype, query=query, limit=limit)

        return self._invoke_mcp(
            "search_document",
            {"doctype": doctype, "query": query, "limit": limit},
            doctype,
        )

    def get_count(self, doctype: str, filters: Optional[dict] = None) -> dict:
        if self._backend == "direct":
            return generic_tools.get_count(doctype=doctype, filters=filters)

        args: Dict[str, Any] = {"doctype": doctype}
        if filters is not None:
            args["filters"] = filters

        return self._invoke_mcp("get_count", args, doctype)

    def aggregate(
        self,
        doctype: str,
        group_by: str,
        metric: str,
        aggregation_fn: str,
        filters: Optional[dict] = None,
        sort: Optional[str] = None,
        limit: Optional[int] = None,
        parent_doctype: Optional[str] = None,
    ) -> dict:
        if self._backend == "direct":
            return generic_tools.aggregate(
                doctype=doctype,
                group_by=group_by,
                metric=metric,
                aggregation_fn=aggregation_fn,
                filters=filters,
                sort=sort,
                limit=limit,
                parent_doctype=parent_doctype,
            )

        args: Dict[str, Any] = {
            "doctype": doctype,
            "group_by": group_by,
            "metric": metric,
            "aggregation_fn": aggregation_fn,
        }
        if filters is not None:
            args["filters"] = filters
        if sort is not None:
            args["sort"] = sort
        if limit is not None:
            args["limit"] = limit
        if parent_doctype is not None:
            args["parent_doctype"] = parent_doctype

        return self._invoke_mcp("aggregate", args, doctype)

    # -------------------------------------------------------------------------
    # Write operations (MUST ALWAYS REMAIN DIRECT - NEVER ROUTED TO MCP)
    # -------------------------------------------------------------------------

    def _check_read_only_write(self, tool_name: str) -> Optional[dict]:
        ctx = get_request_context()
        if ctx is not None and getattr(ctx, "read_only", False):
            user_id = ctx.user_id or "unknown"
            logger.warning(
                "MCP read-only write blocked: tool=%s, user_id=%s, reason=read_only_session",
                tool_name,
                user_id,
            )
            return {
                "status": "permission_denied",
                "success": False,
                "data": None,
                "error": f"Write tool '{tool_name}' is denied because request context is read-only.",
                "has_more": False,
            }
        return None

    def create_document(self, doctype: str, fields: dict) -> dict:
        blocked = self._check_read_only_write("create_document")
        if blocked is not None:
            return blocked
        return generic_tools.create_document(doctype=doctype, fields=fields)

    def update_document(self, doctype: str, name: str, fields: dict, expected_modified: Optional[str] = None) -> dict:
        blocked = self._check_read_only_write("update_document")
        if blocked is not None:
            return blocked
        extra = {"expected_modified": expected_modified} if expected_modified else {}
        return generic_tools.update_document(doctype=doctype, name=name, fields=fields, **extra)

    def delete_document(self, doctype: str, name: str) -> dict:
        blocked = self._check_read_only_write("delete_document")
        if blocked is not None:
            return blocked
        return generic_tools.delete_document(doctype=doctype, name=name)

    def cancel_document(self, doctype: str, name: str, expected_modified: Optional[str] = None) -> dict:
        blocked = self._check_read_only_write("cancel_document")
        if blocked is not None:
            return blocked
        extra = {"expected_modified": expected_modified} if expected_modified else {}
        return generic_tools.cancel_document(doctype=doctype, name=name, **extra)

    def submit_document(self, doctype: str, name: str, expected_modified: Optional[str] = None) -> dict:
        blocked = self._check_read_only_write("submit_document")
        if blocked is not None:
            return blocked
        extra = {"expected_modified": expected_modified} if expected_modified else {}
        return generic_tools.submit_document(doctype=doctype, name=name, **extra)


# Default global adapter instance
adapter = ToolAdapter()


def get_tool_adapter() -> ToolAdapter:
    return adapter


def close_adapter() -> None:
    adapter.close()


atexit.register(close_adapter)


# Module-level convenience functions mirroring generic_tools interface
def is_doctype_allowed(doctype_name: str) -> bool:
    return adapter.is_doctype_allowed(doctype_name)


def get_allowed_doctypes() -> List[str]:
    return adapter.get_allowed_doctypes()


def get_default_company(reload: bool = False) -> str:
    return adapter.get_default_company(reload=reload)


def get_default_warehouse(company: Optional[str] = None, reload: bool = False) -> str:
    return adapter.get_default_warehouse(company=company, reload=reload)


def get_list(*args, **kwargs) -> dict:
    return adapter.get_list(*args, **kwargs)


def get_document(doctype: str, name: str, *args, **kwargs) -> dict:
    return adapter.get_document(doctype, name, *args, **kwargs)


def get_document_direct(doctype: str, name: str) -> dict:
    return adapter.get_document_direct(doctype, name)


def search_document(*args, **kwargs) -> dict:
    return adapter.search_document(*args, **kwargs)


def get_count(*args, **kwargs) -> dict:
    return adapter.get_count(*args, **kwargs)


def aggregate(*args, **kwargs) -> dict:
    return adapter.aggregate(*args, **kwargs)


def create_document(*args, **kwargs) -> dict:
    return adapter.create_document(*args, **kwargs)


def update_document(*args, **kwargs) -> dict:
    return adapter.update_document(*args, **kwargs)


def delete_document(*args, **kwargs) -> dict:
    return adapter.delete_document(*args, **kwargs)


def cancel_document(*args, **kwargs) -> dict:
    return adapter.cancel_document(*args, **kwargs)


def submit_document(*args, **kwargs) -> dict:
    return adapter.submit_document(*args, **kwargs)
