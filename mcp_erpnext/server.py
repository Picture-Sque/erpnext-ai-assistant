"""
ERPNext MCP Server.

Exposes approved ERPNext operations as Model Context Protocol (MCP) tools
over STDIO and HTTP (Streamable HTTP / SSE) transports.

Enforces:
- Schema validation via mcp_erpnext.schemas
- Security and authorization via mcp_erpnext.policy
- Safe credential masking and error envelope handling
- Identity context propagation via contextvars and transport headers
- Default read-only protection for external clients
"""

import argparse
import asyncio
import contextvars
import json
import logging
import os
import re
import sys
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

import jwt
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings
import mcp.types as types
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from mcp_erpnext import crud
from mcp_erpnext import reports
from mcp_erpnext.client import ERPNextClient
from mcp_erpnext.errors import ERPNextError
from mcp_erpnext.policy import (
    ALL_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    PolicyDecision,
    RequestContext,
    authorize_tool_call,
    denied_envelope,
)
from mcp_erpnext.schemas import (
    TOOL_INPUT_SCHEMAS,
    validate_tool_arguments,
)

# Configure logger to output to stderr (never pollute stdout in stdio mode)
logger = logging.getLogger("mcp_erpnext.server")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Request-scoped context variable for identity propagation
_current_request_context: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "mcp_request_context", default=None
)

# Tool metadata and descriptions
TOOL_METADATA: Dict[str, Dict[str, str]] = {
    "get_list": {
        "description": "Fetch a list of documents for a specified DocType with optional filters, fields, pagination, and sorting.",
    },
    "get_document": {
        "description": "Fetch a single document by DocType and name.",
    },
    "search_document": {
        "description": "Search for documents in a DocType matching a text query.",
    },
    "get_count": {
        "description": "Get the count of documents matching given filters.",
    },
    "aggregate": {
        "description": "Perform database-level aggregation (SUM, COUNT, AVG, MIN, MAX) on a DocType.",
    },
    "run_report": {
        "description": "Run an approved ERPNext report with role verification.",
    },
    "create_document": {
        "description": "Create a new document in ERPNext (requires write authorization).",
    },
    "update_document": {
        "description": "Update an existing document in ERPNext (requires write authorization).",
    },
    "delete_document": {
        "description": "Delete a document in ERPNext (requires write authorization).",
    },
    "cancel_document": {
        "description": "Cancel a submitted document in ERPNext (requires write authorization).",
    },
    "submit_document": {
        "description": "Submit a document in ERPNext (requires write authorization).",
    },
}

SENSITIVE_PATTERNS = [
    re.compile(r"(token\s+)([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+)([a-zA-Z0-9._-]+)", re.IGNORECASE),
    re.compile(r"(api_secret['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
    re.compile(r"(api_key['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
    re.compile(r"(password['\"]?\s*[:=]\s*['\"]?)([^'\";\s]+)", re.IGNORECASE),
]

PROD_ENV_VARS: Tuple[str, ...] = ("ENVIRONMENT", "ENV", "NODE_ENV", "STAGE", "DEPLOYMENT_ENV")
PROD_VALUES: FrozenSet[str] = frozenset({"production", "prod", "live", "staging"})


def is_production_environment() -> bool:
    """Check whether the process is configured to run in a production environment."""
    for var in PROD_ENV_VARS:
        val = os.environ.get(var, "").strip().lower()
        if val in PROD_VALUES:
            return True
    return False


def resolve_jwt_secret(explicit_secret: Optional[str] = None) -> Optional[str]:
    """
    Resolve JWT secret key from explicit argument or environment configuration.

    Resolution order:
    1. Explicit function argument
    2. JWT_SECRET (canonical application secret)
    3. JWT_SECRET_KEY
    4. MCP_JWT_SECRET

    Fails closed: returns None if no non-empty secret is configured (no insecure fallback).
    """
    if explicit_secret and isinstance(explicit_secret, str) and explicit_secret.strip():
        return explicit_secret.strip()
    for env_var in ("JWT_SECRET", "JWT_SECRET_KEY", "MCP_JWT_SECRET"):
        val = os.environ.get(env_var)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


def get_transport_security_settings(
    allowed_hosts: Optional[Iterable[str]] = None,
) -> TransportSecuritySettings:
    """
    Construct TransportSecuritySettings with DNS rebinding protection enabled.
    Configures explicit allowed hosts, including local development hosts and test client hosts.
    """
    hosts: Set[str] = {
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "testserver",
        "testserver:*",
    }

    env_hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if env_hosts:
        for h in env_hosts.split(","):
            cleaned = h.strip()
            if cleaned:
                hosts.add(cleaned)
                if ":" not in cleaned:
                    hosts.add(f"{cleaned}:*")

    if allowed_hosts:
        for h in allowed_hosts:
            cleaned = h.strip()
            if cleaned:
                hosts.add(cleaned)
                if ":" not in cleaned:
                    hosts.add(f"{cleaned}:*")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(list(hosts)),
    )


def mask_sensitive(text: Any) -> str:
    """Mask sensitive tokens, secrets, and credentials in strings."""
    if not isinstance(text, str):
        text = str(text)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


def get_default_context() -> RequestContext:
    """
    Construct default RequestContext from environment variables.
    Defaults to read_only=True for safety.
    """
    user_id = os.environ.get("MCP_USER_ID", "anonymous")
    roles_str = os.environ.get("MCP_USER_ROLES", "")
    roles = tuple(r.strip() for r in roles_str.split(",") if r.strip())
    read_only_env = os.environ.get("MCP_READ_ONLY", "true").strip().lower()
    read_only = read_only_env not in ("false", "0", "no")
    return RequestContext(user_id=user_id, roles=roles, read_only=read_only)


def dispatch_tool(
    tool_name: str,
    args: Dict[str, Any],
    ctx: RequestContext,
    client: Optional[ERPNextClient] = None,
) -> Dict[str, Any]:
    """
    Execute tool invocation through schema validation and policy authorization.

    Returns standard response envelope matching existing contracts.
    """
    if tool_name not in ALL_TOOLS:
        return {
            "status": "error",
            "message": f"Unknown tool '{tool_name}'.",
            "error": f"Unknown tool '{tool_name}'.",
            "success": False,
        }

    # Step 1: Validate input against registered Pydantic schema
    try:
        model = validate_tool_arguments(tool_name, args)
        validated_args = model.model_dump(exclude_unset=True)
    except Exception as exc:
        clean_msg = mask_sensitive(str(exc))
        return {
            "status": "error",
            "message": f"Schema validation error: {clean_msg}",
            "error": clean_msg,
            "success": False,
        }

    # Step 2: Authorize tool call via shared security policy
    decision = authorize_tool_call(ctx, tool_name, validated_args)
    if not decision.allowed:
        return denied_envelope(decision)

    # Step 3: Dispatch to business logic
    try:
        if tool_name == "get_list":
            return crud.get_list(
                doctype=validated_args["doctype"],
                filters=validated_args.get("filters"),
                fields=validated_args.get("fields"),
                limit=validated_args.get("limit"),
                order_by=validated_args.get("order_by"),
                offset=validated_args.get("offset", 0),
                include_total=validated_args.get("include_total", False),
                group_by=validated_args.get("group_by"),
                parent_doctype=validated_args.get("parent_doctype"),
                or_filters=validated_args.get("or_filters"),
                client=client,
            )
        elif tool_name == "get_document":
            return crud.get_document(
                doctype=validated_args["doctype"],
                name=validated_args["name"],
                client=client,
            )
        elif tool_name == "search_document":
            return crud.search_document(
                doctype=validated_args["doctype"],
                query=validated_args["query"],
                limit=validated_args.get("limit", 10),
                client=client,
            )
        elif tool_name == "get_count":
            return crud.get_count(
                doctype=validated_args["doctype"],
                filters=validated_args.get("filters"),
                client=client,
            )
        elif tool_name == "aggregate":
            return crud.aggregate(
                doctype=validated_args["doctype"],
                group_by=validated_args["group_by"],
                metric=validated_args["metric"],
                aggregation_fn=validated_args["aggregation_fn"],
                filters=validated_args.get("filters"),
                sort=validated_args.get("sort"),
                limit=validated_args.get("limit"),
                parent_doctype=validated_args.get("parent_doctype"),
                client=client,
            )
        elif tool_name == "run_report":
            return reports.run_report(
                report_name=validated_args["report_name"],
                filters=validated_args.get("filters"),
                user_roles=ctx.roles,
                row_filters=validated_args.get("row_filters"),
                group_by=validated_args.get("group_by"),
                metric=validated_args.get("metric"),
                aggregation_fn=validated_args.get("aggregation_fn"),
                sort=validated_args.get("sort"),
                limit=validated_args.get("limit"),
                client=client,
            )
        elif tool_name == "create_document":
            return crud.create_document(
                doctype=validated_args["doctype"],
                fields=validated_args["fields"],
                client=client,
            )
        elif tool_name == "update_document":
            return crud.update_document(
                doctype=validated_args["doctype"],
                name=validated_args["name"],
                fields=validated_args["fields"],
                client=client,
            )
        elif tool_name == "delete_document":
            return crud.delete_document(
                doctype=validated_args["doctype"],
                name=validated_args["name"],
                client=client,
            )
        elif tool_name == "cancel_document":
            return crud.cancel_document(
                doctype=validated_args["doctype"],
                name=validated_args["name"],
                client=client,
            )
        elif tool_name == "submit_document":
            return crud.submit_document(
                doctype=validated_args["doctype"],
                name=validated_args["name"],
                client=client,
            )
        else:
            return {
                "status": "error",
                "message": f"Unhandled tool '{tool_name}'.",
                "error": f"Unhandled tool '{tool_name}'.",
                "success": False,
            }
    except Exception as exc:
        clean_msg = mask_sensitive(str(exc))
        logger.error("Execution failed for tool '%s': %s", tool_name, clean_msg)
        return {
            "status": "error",
            "message": "ERPNext request failed",
            "error": clean_msg,
            "success": False,
        }


def create_server(
    server_name: str = "erpnext-mcp-server",
    default_context: Optional[RequestContext] = None,
    client: Optional[ERPNextClient] = None,
) -> Server:
    """
    Create and configure an MCP Server instance with all 11 ERPNext tools.
    """
    async def handle_list_tools(req_ctx, params) -> types.ListToolsResult:
        tool_list: List[types.Tool] = []
        for tool_name in ALL_TOOLS:
            meta = TOOL_METADATA.get(tool_name, {})
            schema_model = TOOL_INPUT_SCHEMAS.get(tool_name)
            input_schema = schema_model.model_json_schema() if schema_model else {"type": "object"}
            tool_list.append(
                types.Tool(
                    name=tool_name,
                    description=meta.get("description", f"ERPNext {tool_name} tool"),
                    inputSchema=input_schema,
                )
            )
        return types.ListToolsResult(tools=tool_list)

    async def handle_call_tool(
        server_ctx, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        tool_name = params.name
        arguments = params.arguments or {}

        ctx = _current_request_context.get() or default_context or get_default_context()

        result = await asyncio.to_thread(dispatch_tool, tool_name, arguments, ctx, client)

        is_error = result.get("status") == "error"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
            is_error=is_error,
        )

    return Server(
        name=server_name,
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )


class BearerAuthMiddleware:
    """
    ASGI middleware enforcing Bearer token authentication on HTTP endpoints.
    Decodes JWT to extract user_id, roles, and read_only status into RequestContext.
    Rejects unauthenticated, invalid, or expired requests with 401 Unauthorized.
    Fails closed with 500 if no JWT secret is configured.
    """

    def __init__(
        self,
        app: Any,
        jwt_secret: Optional[str] = None,
        default_read_only: bool = True,
        exempt_paths: Optional[Iterable[str]] = None,
    ):
        self.app = app
        self.jwt_secret = resolve_jwt_secret(jwt_secret)
        self.default_read_only = default_read_only
        self.exempt_paths = set(exempt_paths or ("/health",))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")

        if not auth_header or not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"detail": "Missing or invalid Authorization header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token = auth_header[7:].strip()
        secret = self.jwt_secret or resolve_jwt_secret()

        if not secret:
            logger.error("No JWT secret key configured for HTTP authentication.")
            response = JSONResponse(
                {"detail": "Server security error: JWT secret key is unconfigured."},
                status_code=500,
            )
            await response(scope, receive, send)
            return

        allow_mock = os.environ.get("ALLOW_DEV_MOCK_AUTH", "false").strip().lower() in ("true", "1")

        if allow_mock and token.startswith("mock_"):
            if is_production_environment():
                logger.error("Rejected mock auth token: production environment detected.")
                response = JSONResponse(
                    {"detail": "Mock authentication is forbidden in production environments."},
                    status_code=403,
                )
                await response(scope, receive, send)
                return

            parts = token.split("_", 2)
            user_id = parts[1] if len(parts) > 1 else "dev_user"
            roles_str = parts[2] if len(parts) > 2 else ""
            roles = tuple(r.strip() for r in roles_str.split(",") if r.strip())
            claims = {"sub": user_id, "roles": roles, "read_only": self.default_read_only}
        else:
            try:
                claims = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256"],
                    options={
                        "require": ["exp", "sub"],
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                    },
                )
                sub_val = claims.get("sub")
                if not isinstance(sub_val, str) or not sub_val.strip():
                    raise jwt.InvalidTokenError("Token 'sub' claim must be a non-empty string.")
            except Exception as exc:
                clean_msg = mask_sensitive(str(exc))
                response = JSONResponse(
                    {"detail": f"Invalid or expired token: {clean_msg}"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

        user_id = claims.get("sub") or claims.get("user_id") or "anonymous"
        roles = tuple(claims.get("roles") or ())
        read_only = claims.get("read_only", self.default_read_only)

        ctx = RequestContext(user_id=user_id, roles=roles, read_only=read_only)
        token_ctx = _current_request_context.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_request_context.reset(token_ctx)


def create_http_app(
    server: Optional[Server] = None,
    default_context: Optional[RequestContext] = None,
    jwt_secret: Optional[str] = None,
    default_read_only: bool = True,
    client: Optional[ERPNextClient] = None,
    allowed_hosts: Optional[Iterable[str]] = None,
) -> Starlette:
    """
    Create a Starlette ASGI application with Streamable HTTP transport and Bearer Auth.
    Fails closed if no JWT secret is configured.
    Enforces DNS rebinding protection with explicit allowed hostnames.
    """
    resolved_secret = resolve_jwt_secret(jwt_secret)
    if not resolved_secret:
        raise RuntimeError(
            "Server security error: JWT secret key is unconfigured. "
            "Set JWT_SECRET environment variable or pass jwt_secret to create_http_app."
        )

    allow_mock = os.environ.get("ALLOW_DEV_MOCK_AUTH", "false").strip().lower() in ("true", "1")
    if allow_mock and is_production_environment():
        raise RuntimeError(
            "Security violation: ALLOW_DEV_MOCK_AUTH cannot be enabled in a production environment."
        )

    if server is None:
        server = create_server(default_context=default_context, client=client)

    transport_sec = get_transport_security_settings(allowed_hosts=allowed_hosts)
    app = server.streamable_http_app(transport_security=transport_sec)

    async def health_endpoint(request: Request) -> Response:
        return JSONResponse({"status": "healthy", "service": "erpnext-mcp-server"})

    app.add_route("/health", health_endpoint, methods=["GET"])
    app.add_middleware(
        BearerAuthMiddleware,
        jwt_secret=resolved_secret,
        default_read_only=default_read_only,
        exempt_paths={"/health"},
    )
    return app


async def run_stdio(
    server: Optional[Server] = None,
    default_context: Optional[RequestContext] = None,
    client: Optional[ERPNextClient] = None,
) -> None:
    """
    Run MCP server using STDIO transport.
    """
    if server is None:
        server = create_server(default_context=default_context, client=client)

    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """
    Standalone entry point for launching the MCP server.
    """
    parser = argparse.ArgumentParser(description="ERPNext Model Context Protocol (MCP) Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport type (stdio, http, sse, streamable-http)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="Host for HTTP transport",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8000")),
        help="Port for HTTP transport",
    )
    parser.add_argument(
        "--read-only",
        dest="read_only",
        action="store_true",
        default=None,
        help="Enforce read-only mode (default: True)",
    )
    parser.add_argument(
        "--read-write",
        dest="read_only",
        action="store_false",
        help="Allow write tools if authorized",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("MCP_USER_ID", "anonymous"),
        help="Default user ID for STDIO context",
    )
    parser.add_argument(
        "--roles",
        default=os.environ.get("MCP_USER_ROLES", ""),
        help="Comma-separated roles for STDIO context",
    )

    args = parser.parse_args()

    # Determine read_only flag
    if args.read_only is not None:
        read_only = args.read_only
    else:
        read_only_env = os.environ.get("MCP_READ_ONLY", "true").strip().lower()
        read_only = read_only_env not in ("false", "0", "no")

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    default_context = RequestContext(
        user_id=args.user_id,
        roles=roles,
        read_only=read_only,
    )

    logger.info(
        "Starting ERPNext MCP server (transport=%s, read_only=%s, user_id=%s)",
        args.transport,
        read_only,
        args.user_id,
    )

    if args.transport == "stdio":
        asyncio.run(run_stdio(default_context=default_context))
    else:
        secret = resolve_jwt_secret()
        if not secret:
            logger.error("Failed to start HTTP server: JWT_SECRET environment variable is not set.")
            sys.exit(1)

        allow_mock = os.environ.get("ALLOW_DEV_MOCK_AUTH", "false").strip().lower() in ("true", "1")
        if allow_mock and is_production_environment():
            logger.error("Failed to start HTTP server: ALLOW_DEV_MOCK_AUTH cannot be enabled in production.")
            sys.exit(1)

        import uvicorn

        app = create_http_app(
            default_context=default_context,
            jwt_secret=secret,
            default_read_only=read_only,
        )
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
