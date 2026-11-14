import asyncio
import datetime
import os
import unittest
from unittest.mock import patch

import jwt
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

import mcp.types as types
from mcp_erpnext.auth import (
    RequestContext,
    current_request_context,
    get_request_context,
)
from mcp_erpnext.server import (
    BearerAuthMiddleware,
    create_http_app,
    create_server,
)


class TestServerContextBinding(unittest.TestCase):
    def setUp(self):
        self.secret = "test-secret-key-that-is-at-least-32-chars-long"
        self.now = datetime.datetime.now(datetime.timezone.utc)

    def _make_token(self, sub: str, roles: tuple = (), read_only: bool = True) -> str:
        payload = {
            "sub": sub,
            "roles": list(roles),
            "read_only": read_only,
            "iat": self.now,
            "exp": self.now + datetime.timedelta(hours=1),
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")

    # a) HTTP middleware sets the unified var for the request and resets it afterwards
    def test_http_middleware_sets_and_resets_var(self):
        token = self._make_token("alice", roles=("Sales User",))
        observed_ctx = None

        app = create_http_app(jwt_secret=self.secret)

        async def capture_endpoint(request: Request):
            nonlocal observed_ctx
            observed_ctx = get_request_context()
            return JSONResponse({"status": "ok"})

        app.add_route("/capture", capture_endpoint, methods=["GET"])

        assert get_request_context() is None

        with TestClient(app) as client:
            resp = client.get("/capture", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)

        self.assertIsNotNone(observed_ctx)
        self.assertEqual(observed_ctx.user_id, "alice")
        self.assertEqual(observed_ctx.roles, ("Sales User",))
        self.assertTrue(observed_ctx.read_only)
        self.assertIsNone(get_request_context())

    # b) No leakage across two sequential requests with different identities
    def test_no_leakage_across_sequential_requests(self):
        token_alice = self._make_token("alice", roles=("Sales User",))
        token_bob = self._make_token("bob", roles=("Stock Manager",))
        observed_identities = []

        app = create_http_app(jwt_secret=self.secret)

        async def capture_endpoint(request: Request):
            ctx = get_request_context()
            observed_identities.append(ctx.user_id if ctx else None)
            return JSONResponse({"status": "ok"})

        app.add_route("/capture_seq", capture_endpoint, methods=["GET"])

        with TestClient(app) as client:
            client.get("/capture_seq", headers={"Authorization": f"Bearer {token_alice}"})
            self.assertIsNone(get_request_context())

            client.get("/capture_seq", headers={"Authorization": f"Bearer {token_bob}"})
            self.assertIsNone(get_request_context())

        self.assertEqual(observed_identities, ["alice", "bob"])
        self.assertIsNone(get_request_context())

    # c) Two concurrent requests (asyncio.gather) each observe their own identity
    def test_concurrent_requests_observe_own_identity(self):
        import httpx

        async def run_concurrent():
            token_1 = self._make_token("user_1", roles=("Role1",))
            token_2 = self._make_token("user_2", roles=("Role2",))
            observed_concurrent = {}

            app = create_http_app(jwt_secret=self.secret)

            async def capture_concurrent(request: Request):
                # delay slightly to ensure overlap
                await asyncio.sleep(0.01)
                ctx = get_request_context()
                observed_concurrent[ctx.user_id] = ctx.roles
                return JSONResponse({"user": ctx.user_id})

            app.add_route("/capture_concurrent", capture_concurrent, methods=["GET"])

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
                r1, r2 = await asyncio.gather(
                    async_client.get("/capture_concurrent", headers={"Authorization": f"Bearer {token_1}"}),
                    async_client.get("/capture_concurrent", headers={"Authorization": f"Bearer {token_2}"}),
                )
                self.assertEqual(r1.status_code, 200)
                self.assertEqual(r2.status_code, 200)

            self.assertEqual(observed_concurrent.get("user_1"), ("Role1",))
            self.assertEqual(observed_concurrent.get("user_2"), ("Role2",))
            self.assertIsNone(get_request_context())

        asyncio.run(run_concurrent())

    # d) An exception raised inside the handler still resets the var
    def test_exception_in_handler_still_resets_var(self):
        token = self._make_token("err_user")

        app = create_http_app(jwt_secret=self.secret)

        async def failing_endpoint(request: Request):
            raise RuntimeError("Forced endpoint failure")

        app.add_route("/failing", failing_endpoint, methods=["GET"])

        assert get_request_context() is None

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/failing", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 500)

        self.assertIsNone(get_request_context())

    # e) PROPAGATION CHECK: context set by BearerAuthMiddleware is visible inside handle_call_tool
    def test_propagation_check_middleware_to_handle_call_tool(self):
        token = self._make_token("alice_mcp", roles=("Sales User", "Accounts User"))
        observed = []

        def fake_dispatch(tool_name, args, ctx, client):
            observed.append({
                "param_ctx_user": ctx.user_id,
                "param_ctx_roles": ctx.roles,
                "var_ctx_user": get_request_context().user_id if get_request_context() else None,
                "var_ctx_roles": get_request_context().roles if get_request_context() else None,
            })
            return {"status": "success", "data": [{"name": "Doc1"}]}

        with patch("mcp_erpnext.server.dispatch_tool", side_effect=fake_dispatch):
            app = create_http_app(jwt_secret=self.secret)
            with TestClient(app) as client:
                init_res = client.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
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
                self.assertEqual(init_res.status_code, 200)
                session_id = init_res.headers.get("mcp-session-id")

                call_headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                }
                if session_id:
                    call_headers["mcp-session-id"] = session_id

                call_res = client.post(
                    "/mcp",
                    headers=call_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "get_list", "arguments": {"doctype": "Customer"}},
                    },
                )
                self.assertEqual(call_res.status_code, 200)

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["param_ctx_user"], "alice_mcp")
        self.assertEqual(observed[0]["param_ctx_roles"], ("Sales User", "Accounts User"))
        self.assertEqual(observed[0]["var_ctx_user"], "alice_mcp")
        self.assertEqual(observed[0]["var_ctx_roles"], ("Sales User", "Accounts User"))
        self.assertIsNone(get_request_context())

    # f) STDIO path: identity ends up in unified var during tool call, defaults when env unset
    def test_stdio_identity_binding_and_defaults(self):
        observed_stdio = []

        def fake_dispatch(tool_name, args, ctx, client):
            observed_stdio.append({
                "param_user": ctx.user_id,
                "var_user": get_request_context().user_id if get_request_context() else None,
                "var_roles": get_request_context().roles if get_request_context() else None,
                "var_read_only": get_request_context().read_only if get_request_context() else None,
            })
            return {"status": "success", "data": []}

        # 1. Unset environment: test defaults
        with patch.dict(os.environ, {}, clear=True):
            os.environ["ERPNEXT_API_KEY"] = "mock_key"
            os.environ["ERPNEXT_API_SECRET"] = "mock_secret"
            with patch("mcp_erpnext.server.dispatch_tool", side_effect=fake_dispatch):
                srv = create_server()
                handler = srv.get_request_handler("tools/call").handler
                params = types.CallToolRequestParams(name="get_list", arguments={"doctype": "Item"})
                asyncio.run(handler(None, params))

        self.assertEqual(len(observed_stdio), 1)
        self.assertEqual(observed_stdio[0]["param_user"], "anonymous")
        self.assertEqual(observed_stdio[0]["var_user"], "anonymous")
        self.assertEqual(observed_stdio[0]["var_roles"], ())
        self.assertTrue(observed_stdio[0]["var_read_only"])
        self.assertIsNone(get_request_context())

        # 2. Configured environment: MCP_USER_ID, MCP_USER_ROLES, MCP_READ_ONLY
        custom_env = {
            "MCP_USER_ID": "stdio_charlie",
            "MCP_USER_ROLES": "Stock Manager, Accounts User",
            "MCP_READ_ONLY": "false",
            "ERPNEXT_API_KEY": "mock_key",
            "ERPNEXT_API_SECRET": "mock_secret",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            with patch("mcp_erpnext.server.dispatch_tool", side_effect=fake_dispatch):
                srv2 = create_server()
                handler2 = srv2.get_request_handler("tools/call").handler
                params2 = types.CallToolRequestParams(name="get_list", arguments={"doctype": "Customer"})
                asyncio.run(handler2(None, params2))

        self.assertEqual(len(observed_stdio), 2)
        self.assertEqual(observed_stdio[1]["param_user"], "stdio_charlie")
        self.assertEqual(observed_stdio[1]["var_user"], "stdio_charlie")
        self.assertEqual(observed_stdio[1]["var_roles"], ("Stock Manager", "Accounts User"))
        self.assertFalse(observed_stdio[1]["var_read_only"])
        self.assertIsNone(get_request_context())
