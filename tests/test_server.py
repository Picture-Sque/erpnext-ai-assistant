"""
Unit tests for mcp_erpnext.server.

Verifies:
1. Server initialization and capabilities.
2. Tool listing: all 11 tools exposed with correct Pydantic schemas.
3. Unknown tool rejection.
4. Schema validation: missing fields, invalid types, extra forbidden fields.
5. Policy authorization executed before business dispatch.
6. Forbidden doctypes rejected.
7. Unauthorized reports rejected with standard permission_denied envelope.
8. Empty or missing roles handled safely (deny).
9. Read-only mode prevents write tools.
10. Tool argument identity injection rejected/ignored.
11. Verifying CRUD / report functions are NOT called when policy denies.
12. Error masking: credentials and tokens are redacted from error envelopes.
13. HTTP startup without JWT secret fails closed (RuntimeError).
14. JWT secret loaded from standard JWT_SECRET environment variable.
15. HTTP authentication missing header / invalid scheme.
16. HTTP authentication invalid signature.
17. HTTP authentication expired token.
18. HTTP authentication missing required 'exp' claim.
19. HTTP authentication missing required 'sub' claim.
20. HTTP authentication unsigned / alg=none token rejected.
21. Mock authentication rejected in production environments.
22. Mock authentication permitted in development environments.
23. CLI entrypoint no longer accepts --jwt-secret flag.
24. DNS rebinding protection enabled and unauthorized host rejected.
25. Existing valid application JWT compatibility.
26. STDIO transport runner compatibility with memory streams and client session.
"""

import anyio
import datetime
import json
import jwt
import os
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from mcp.client.session import ClientSession
import mcp.types as types

from mcp_erpnext import crud, reports
from mcp_erpnext.errors import ERPNextError
from mcp_erpnext.policy import (
    ALL_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    RequestContext,
    get_allowed_doctypes,
)
from mcp_erpnext.schemas import TOOL_INPUT_SCHEMAS
from mcp_erpnext.server import (
    BearerAuthMiddleware,
    create_http_app,
    create_server,
    dispatch_tool,
    get_default_context,
    get_transport_security_settings,
    is_production_environment,
    mask_sensitive,
    resolve_jwt_secret,
    run_stdio,
)


class TestMCPServer(unittest.TestCase):
    """Test suite for ERPNext MCP Server."""

    def setUp(self):
        # Pick safe whitelisted doctype from crud
        self.whitelisted_doctype = sorted(list(crud.DEFAULT_WHITELIST))[0]
        self.secret = "super_secret_test_key_at_least_32_bytes_long"
        self.read_context = RequestContext(
            user_id="test_user",
            roles=("Stock User",),
            read_only=True,
        )
        self.write_context = RequestContext(
            user_id="test_admin",
            roles=("System Manager",),
            read_only=False,
        )

    def _create_token(
        self,
        sub="test_agent@example.com",
        roles=None,
        secret=None,
        exp_delta=datetime.timedelta(hours=1),
        read_only=True,
        extra_claims=None,
    ):
        if secret is None:
            secret = self.secret
        payload = {}
        if sub is not None:
            payload["sub"] = sub
        if roles is not None:
            payload["roles"] = roles
        if exp_delta is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
            payload["exp"] = now + exp_delta
            payload["iat"] = now
        payload["read_only"] = read_only
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, secret, algorithm="HS256")

    # 1. Server initialization
    def test_server_initialization(self):
        server = create_server("test-erpnext-server")
        self.assertEqual(server.name, "test-erpnext-server")
        init_opts = server.create_initialization_options()
        self.assertIsNotNone(init_opts.capabilities.tools)

    # 2. Tool listing includes all 11 tools with correct schemas
    def test_tool_listing_all_11_tools(self):
        server = create_server()
        req_ctx = MagicMock()
        handler = server.get_request_handler("tools/list").handler
        tools_result = anyio.run(handler, req_ctx, None)
        tool_names = {t.name for t in tools_result.tools}

        self.assertEqual(tool_names, ALL_TOOLS)
        self.assertEqual(len(tool_names), 11)

        for t in tools_result.tools:
            self.assertIn(t.name, TOOL_INPUT_SCHEMAS)
            expected_schema = TOOL_INPUT_SCHEMAS[t.name].model_json_schema()
            self.assertEqual(t.input_schema["type"], expected_schema["type"])
            self.assertEqual(t.input_schema.get("properties"), expected_schema.get("properties"))
            self.assertTrue(len(t.description) > 0)

    # 3. Tool call with unknown tool returns error
    def test_unknown_tool_call(self):
        res = dispatch_tool("unknown_tool_xyz", {}, self.read_context)
        self.assertEqual(res["status"], "error")
        self.assertFalse(res["success"])
        self.assertIn("Unknown tool", res["message"])

    # 4. Tool call with invalid schema arguments rejected
    def test_schema_validation_rejection(self):
        # Missing required field 'doctype'
        res = dispatch_tool("get_list", {}, self.read_context)
        self.assertEqual(res["status"], "error")
        self.assertFalse(res["success"])
        self.assertIn("Field required", res["message"])

        # Extra forbidden field
        res_extra = dispatch_tool(
            "get_list",
            {"doctype": self.whitelisted_doctype, "unexpected_extra": 123},
            self.read_context,
        )
        self.assertEqual(res_extra["status"], "error")
        self.assertIn("Extra inputs are not permitted", res_extra["message"])

        # Invalid type for limit
        res_type = dispatch_tool(
            "get_list",
            {"doctype": self.whitelisted_doctype, "limit": "not_an_int"},
            self.read_context,
        )
        self.assertEqual(res_type["status"], "error")

    # 5. Tool call policy authorization executed before dispatch
    def test_policy_authorization_executed_before_dispatch(self):
        with patch.object(crud, "get_list") as mock_get_list:
            # Dangerous order_by injection rejected by policy
            res = dispatch_tool(
                "get_list",
                {"doctype": self.whitelisted_doctype, "order_by": "name; DROP TABLE tabItem;--"},
                self.read_context,
            )
            self.assertEqual(res["status"], "permission_denied")
            mock_get_list.assert_not_called()

    # 6. Forbidden doctypes rejected
    def test_forbidden_doctype_rejected(self):
        forbidden_dt = "User"
        with patch.object(crud, "get_list") as mock_get_list:
            res = dispatch_tool("get_list", {"doctype": forbidden_dt}, self.read_context)
            self.assertEqual(res["status"], "permission_denied")
            mock_get_list.assert_not_called()

    # 7. Unauthorized reports rejected with permission_denied envelope
    def test_unauthorized_report_rejected(self):
        with patch.object(reports, "run_report") as mock_report:
            # Unlisted financial report
            res = dispatch_tool(
                "run_report",
                {"report_name": "General Ledger"},
                self.read_context,
            )
            self.assertEqual(res["status"], "permission_denied")
            self.assertFalse(res["success"])
            mock_report.assert_not_called()

    # 8. Empty/missing roles handled safely (deny)
    def test_empty_missing_roles_denied(self):
        empty_role_ctx = RequestContext(user_id="user_no_roles", roles=(), read_only=True)
        with patch.object(reports, "run_report") as mock_report:
            res = dispatch_tool(
                "run_report",
                {"report_name": "Stock Projected Qty"},
                empty_role_ctx,
            )
            self.assertEqual(res["status"], "permission_denied")
            mock_report.assert_not_called()

    # 9. Read-only mode prevents write tools
    def test_read_only_mode_blocks_write_tools(self):
        for tool_name in WRITE_TOOLS:
            args = {"doctype": self.whitelisted_doctype, "name": "DOC-001"}
            if tool_name == "create_document":
                args = {"doctype": self.whitelisted_doctype, "fields": {"title": "Test"}}
            elif tool_name == "update_document":
                args = {"doctype": self.whitelisted_doctype, "name": "DOC-001", "fields": {"title": "Test"}}

            res = dispatch_tool(tool_name, args, self.read_context)
            self.assertEqual(
                res["status"],
                "permission_denied",
                f"Expected {tool_name} to be denied in read-only mode",
            )
            self.assertIn("read-only mode", res["error"])

    # 10. Tool argument identity injection attempts rejected/ignored
    def test_identity_injection_rejected(self):
        injected_args = {
            "doctype": self.whitelisted_doctype,
            "roles": ["Administrator"],
            "user_id": "Administrator",
            "read_only": False,
        }
        res = dispatch_tool("get_list", injected_args, self.read_context)
        # Schema extra="forbid" rejects unexpected identity fields
        self.assertEqual(res["status"], "error")
        self.assertIn("Extra inputs are not permitted", res["message"])

    # 11. Verifying CRUD functions are NOT called when policy denies
    def test_crud_functions_not_called_on_policy_denial(self):
        with patch.object(crud, "create_document") as mock_create, \
             patch.object(crud, "delete_document") as mock_delete, \
             patch.object(crud, "submit_document") as mock_submit:

            dispatch_tool("create_document", {"doctype": self.whitelisted_doctype, "fields": {}}, self.read_context)
            dispatch_tool("delete_document", {"doctype": self.whitelisted_doctype, "name": "DOC"}, self.read_context)
            dispatch_tool("submit_document", {"doctype": self.whitelisted_doctype, "name": "DOC"}, self.read_context)

            mock_create.assert_not_called()
            mock_delete.assert_not_called()
            mock_submit.assert_not_called()

    # 12. Error masking: internal errors return clean error envelopes without credential leaks
    def test_error_masking_credential_leak(self):
        with patch.object(
            crud,
            "get_list",
            side_effect=RuntimeError("Auth failed with token secret_api_key_123:secret_token_456 and Bearer jwt_secret_abc"),
        ):
            res = dispatch_tool("get_list", {"doctype": self.whitelisted_doctype}, self.read_context)
            self.assertEqual(res["status"], "error")
            self.assertNotIn("secret_token_456", res["error"])
            self.assertNotIn("jwt_secret_abc", res["error"])
            self.assertIn("token ***", res["error"])
            self.assertIn("Bearer ***", res["error"])

    # 13. HTTP startup without JWT secret fails closed
    def test_http_startup_without_jwt_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure none of the secret env vars are set
            for var in ("JWT_SECRET", "JWT_SECRET_KEY", "MCP_JWT_SECRET"):
                os.environ.pop(var, None)

            with self.assertRaises(RuntimeError) as ctx:
                create_http_app(jwt_secret=None)
            self.assertIn("JWT secret key is unconfigured", str(ctx.exception))

    # 14. JWT secret loaded from standard JWT_SECRET environment variable
    def test_http_secret_loaded_from_jwt_secret_env(self):
        with patch.dict(os.environ, {"JWT_SECRET": self.secret}):
            app = create_http_app(jwt_secret=None)
            token = self._create_token()
            with TestClient(app) as client:
                res = client.get("/health")
                self.assertEqual(res.status_code, 200)

                init_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test_client", "version": "1.0"},
                    },
                }
                res = client.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                    json=init_payload,
                )
                self.assertEqual(res.status_code, 200)

    # 15. HTTP authentication missing header / invalid scheme
    def test_http_auth_missing_header_and_invalid_scheme(self):
        app = create_http_app(jwt_secret=self.secret)
        with TestClient(app) as client:
            # Missing header
            res1 = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(res1.status_code, 401)
            self.assertIn("Authorization", res1.json()["detail"])

            # Invalid scheme (Basic instead of Bearer)
            res2 = client.post(
                "/mcp",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res2.status_code, 401)

    # 16. HTTP authentication invalid signature
    def test_http_auth_invalid_signature(self):
        app = create_http_app(jwt_secret=self.secret)
        wrong_token = self._create_token(secret="wrong_secret_key_at_least_32_bytes_long!")
        with TestClient(app) as client:
            res = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {wrong_token}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res.status_code, 401)
            self.assertIn("Invalid or expired token", res.json()["detail"])

    # 17. HTTP authentication expired token
    def test_http_auth_expired_token(self):
        app = create_http_app(jwt_secret=self.secret)
        expired_token = self._create_token(exp_delta=datetime.timedelta(hours=-2))
        with TestClient(app) as client:
            res = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {expired_token}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res.status_code, 401)
            self.assertIn("expired", res.json()["detail"].lower())

    # 18. HTTP authentication missing required 'exp' claim
    def test_http_auth_missing_exp_claim(self):
        app = create_http_app(jwt_secret=self.secret)
        # Token without exp claim
        token_no_exp = self._create_token(exp_delta=None)
        with TestClient(app) as client:
            res = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token_no_exp}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res.status_code, 401)
            self.assertIn("missing", res.json()["detail"].lower())

    # 19. HTTP authentication missing required 'sub' claim
    def test_http_auth_missing_sub_claim(self):
        app = create_http_app(jwt_secret=self.secret)
        # Token without sub claim
        token_no_sub = self._create_token(sub=None)
        # Token with empty sub string
        token_empty_sub = self._create_token(sub="   ")

        with TestClient(app) as client:
            res1 = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token_no_sub}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res1.status_code, 401)
            self.assertIn("sub", res1.json()["detail"].lower())

            res2 = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token_empty_sub}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res2.status_code, 401)
            self.assertIn("sub", res2.json()["detail"].lower())

    # 20. HTTP authentication unsigned / alg=none token rejected
    def test_http_auth_unsigned_token_rejected(self):
        app = create_http_app(jwt_secret=self.secret)
        # Create an alg=none token
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {"sub": "attacker", "roles": ["System Manager"], "exp": (now + datetime.timedelta(hours=1)).timestamp()}
        raw_header = '{"alg":"none","typ":"JWT"}'
        import base64
        b64_header = base64.urlsafe_b64encode(raw_header.encode()).decode().rstrip("=")
        b64_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        unsigned_token = f"{b64_header}.{b64_payload}."

        with TestClient(app) as client:
            res = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {unsigned_token}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res.status_code, 401)

    # 21. Mock authentication rejected in production environments
    def test_http_mock_auth_in_production_rejected(self):
        with patch.dict(os.environ, {"ALLOW_DEV_MOCK_AUTH": "true", "ENVIRONMENT": "production"}):
            # Startup must fail if mock auth is enabled in production
            with self.assertRaises(RuntimeError) as ctx:
                create_http_app(jwt_secret=self.secret)
            self.assertIn("cannot be enabled in a production environment", str(ctx.exception))

    # 22. Mock authentication permitted in development environments
    def test_http_mock_auth_in_development_allowed(self):
        with patch.dict(os.environ, {"ALLOW_DEV_MOCK_AUTH": "true", "ENVIRONMENT": "development"}):
            app = create_http_app(jwt_secret=self.secret)
            with TestClient(app) as client:
                res = client.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer mock_developer_Stock User",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test_dev_client", "version": "1.0"},
                        },
                    },
                )
                self.assertEqual(res.status_code, 200)

    # 23. CLI entrypoint no longer accepts --jwt-secret flag
    def test_cli_no_longer_accepts_jwt_secret_flag(self):
        from mcp_erpnext.server import main
        # Inspect parser by passing --jwt-secret in sys.argv
        with patch("sys.argv", ["server.py", "--jwt-secret", "some_secret"]):
            with self.assertRaises(SystemExit):
                main()

    # 24. DNS rebinding protection enabled and unauthorized host rejected
    def test_dns_rebinding_protection_blocks_unauthorized_host(self):
        app = create_http_app(jwt_secret=self.secret)
        token = self._create_token()
        with TestClient(app) as client:
            # Authorized host (testserver) succeeds
            res_valid = client.post(
                "/mcp",
                headers={
                    "Host": "testserver",
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test_client", "version": "1.0"},
                    },
                },
            )
            self.assertEqual(res_valid.status_code, 200)

            # Unauthorized evil host is rejected with 421 Misdirected Request
            res_evil = client.post(
                "/mcp",
                headers={
                    "Host": "evil-attacker.com",
                    "Authorization": f"Bearer {token}",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(res_evil.status_code, 421)

    # 25. Existing valid application JWT compatibility
    def test_existing_application_jwt_compatibility(self):
        # Format matching agent/main.py and tests/test_auth.py:
        # sub, full_name, roles, iat, exp
        now = datetime.datetime.now(datetime.timezone.utc)
        app_token = jwt.encode(
            {
                "sub": "jane.doe@example.com",
                "full_name": "Jane Doe",
                "roles": ["Stock User", "Accounts User"],
                "iat": now,
                "exp": now + datetime.timedelta(hours=24),
            },
            self.secret,
            algorithm="HS256",
        )
        app = create_http_app(jwt_secret=self.secret)
        with TestClient(app) as client:
            res = client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {app_token}",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "app_client", "version": "1.0"},
                    },
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn("serverInfo", res.text)

    # 26. STDIO transport runner compatibility
    def test_stdio_runner_compatibility(self):
        server = create_server("test-stdio-server")

        async def run_in_memory():
            client_to_server_send, server_receive = anyio.create_memory_object_stream(10)
            server_to_client_send, client_receive = anyio.create_memory_object_stream(10)

            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    server.run,
                    server_receive,
                    server_to_client_send,
                    server.create_initialization_options(),
                )
                async with ClientSession(client_receive, client_to_server_send) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 11)

                    # Call get_list tool over session
                    with patch.object(
                        crud,
                        "get_list",
                        return_value={"status": "success", "data": [{"name": "DOC-1"}]},
                    ):
                        res = await session.call_tool(
                            "get_list",
                            {"doctype": self.whitelisted_doctype},
                        )
                        self.assertFalse(res.is_error)
                        body = json.loads(res.content[0].text)
                        self.assertEqual(body["status"], "success")
                        self.assertEqual(body["data"][0]["name"], "DOC-1")

                tg.cancel_scope.cancel()

        anyio.run(run_in_memory)


if __name__ == "__main__":
    unittest.main()
