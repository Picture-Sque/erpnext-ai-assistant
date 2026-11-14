"""
Unit tests for Part L: MCP Server Per-User Identity and Session Propagation.

Verifies:
1. HTTP transport, external clients: force read_only=True regardless of any incoming claim,
   and deny write tools in all cases over HTTP (Rule 1.1).
2. Unified verification path in auth.py called by both main.py and server.py,
   including fail-closed behaviour when ERPNEXT_BASE_URL is unset (Rules 1.2 & 1.3).
3. InMemoryMCPClient requires active RequestContext and fails closed when strict identity is enabled (Rule 1.4).
4. STDIO server refuses to start unless explicit read-only credentials are configured and is never writable (Rule 1.5).
5. HTTP rejects unauthenticated, invalid-signature, or expired tokens before tool runs (Rule 1.7).
6. Concurrency: concurrent requests with different users do not leak identity across threads (Rule 1.7).
7. Parity between direct backend and MCP in-process execution preserving user session headers.
"""

import asyncio
import concurrent.futures
import datetime
import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch

import jwt
from cryptography.fernet import Fernet
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from mcp_erpnext.auth import (
    AuthTokenError,
    RequestContext,
    TokenExpiredError,
    build_request_context_from_payload,
    current_request_context,
    decrypt_session_credentials,
    get_request_context,
    set_request_context,
    verify_jwt,
)
from mcp_erpnext.client import Credentials, ERPNextClient
from mcp_erpnext.errors import ERPNextConfigError
from mcp_erpnext.server import (
    BearerAuthMiddleware,
    InMemoryMCPClient,
    create_http_app,
    create_server,
    dispatch_tool,
    run_stdio,
)
from agent.tools.adapter import ToolAdapter


class TestPartLMCPIdentity(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.jwt_secret = "test-secret-key-that-is-at-least-32-chars-long"
        self.enc_key = Fernet.generate_key().decode("utf-8")
        self.fernet = Fernet(self.enc_key.encode("utf-8"))
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.base_url = "http://localhost:8080"

    def _mint_test_token(
        self,
        sub: str = "sandra@gmail.com",
        roles: tuple = ("Sales User",),
        scope: str = None,
        read_only: bool = None,
        sid: str = "test_sid_12345",
        csrf_token: str = "test_csrf_token_67890",
        include_ctx: bool = True,
        secret: str = None,
        exp_delta: datetime.timedelta = datetime.timedelta(hours=1),
    ) -> str:
        payload = {
            "sub": sub,
            "roles": list(roles),
            "iat": self.now,
        }
        if exp_delta is not None:
            payload["exp"] = self.now + exp_delta
        if scope is not None:
            payload["scope"] = scope
        if read_only is not None:
            payload["read_only"] = read_only

        if include_ctx:
            ctx_data = json.dumps({"sid": sid, "csrf_token": csrf_token})
            payload["ctx"] = self.fernet.encrypt(ctx_data.encode("utf-8")).decode("utf-8")

        return jwt.encode(payload, secret or self.jwt_secret, algorithm="HS256")

    # -------------------------------------------------------------------------
    # 1. HTTP Transport Forces Read-Only for External Clients & Denies Write Tools
    # -------------------------------------------------------------------------

    def test_http_forces_read_only_and_denies_write_tools_for_all_external_tokens(self):
        """
        Verify that over HTTP transport, BearerAuthMiddleware unconditionally forces read_only=True
        regardless of incoming claims (no scope, scope='read', scope='all', read_only=False, no ctx).
        Assert that NONE of the 5 write tools can execute over HTTP.
        """
        tokens = {
            "no_scope": self._mint_test_token(sub="u1@example.com"),
            "scope_read": self._mint_test_token(sub="u2@example.com", scope="read"),
            "scope_all": self._mint_test_token(sub="u3@example.com", scope="all"),
            "read_only_false": self._mint_test_token(sub="u4@example.com", read_only=False),
            "no_ctx": self._mint_test_token(sub="u5@example.com", include_ctx=False),
        }

        write_tools = (
            "create_document",
            "update_document",
            "delete_document",
            "cancel_document",
            "submit_document",
        )

        with patch.dict(os.environ, {"CREDENTIAL_ENC_KEY": self.enc_key, "ERPNEXT_BASE_URL": self.base_url}):
            app = create_http_app(jwt_secret=self.jwt_secret)

            # Route to capture request context read_only flag
            observed_contexts = {}

            async def capture_endpoint(request: Request):
                ctx = get_request_context()
                observed_contexts[ctx.user_id] = ctx.read_only
                return JSONResponse({"status": "ok"})

            app.add_route("/check_ro", capture_endpoint, methods=["GET"])

            with TestClient(app) as client:
                for name, tok in tokens.items():
                    resp = client.get("/check_ro", headers={"Authorization": f"Bearer {tok}"})
                    self.assertEqual(resp.status_code, 200)

                # Verify all contexts observed over HTTP have read_only=True
                for uid, ro_flag in observed_contexts.items():
                    self.assertTrue(ro_flag, f"User {uid} context must have read_only=True over HTTP")

                # Verify each write tool is blocked server-side by policy
                for token_name, tok in tokens.items():
                    # Initialize MCP session
                    init_res = client.post(
                        "/mcp",
                        headers={
                            "Authorization": f"Bearer {tok}",
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
                    self.assertEqual(init_res.status_code, 200)
                    sess_id = init_res.headers.get("mcp-session-id")

                    for tool_name in write_tools:
                        with patch(f"mcp_erpnext.crud.{tool_name}") as mock_write_crud:
                            call_args = {"doctype": "Customer"}
                            if tool_name in ("update_document", "delete_document", "cancel_document", "submit_document"):
                                call_args["name"] = "CUST-001"
                            if tool_name in ("create_document", "update_document"):
                                call_args["fields"] = {"customer_name": "Test"}

                            res = client.post(
                                "/mcp",
                                headers={
                                    "Authorization": f"Bearer {tok}",
                                    "Accept": "application/json, text/event-stream",
                                    "mcp-session-id": sess_id,
                                },
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 2,
                                    "method": "tools/call",
                                    "params": {
                                        "name": tool_name,
                                        "arguments": call_args,
                                    },
                                },
                            )
                            self.assertEqual(res.status_code, 200)
                            self.assertIn("read-only mode", res.text)
                            self.assertIn("permission_denied", res.text)
                            mock_write_crud.assert_not_called()

    # -------------------------------------------------------------------------
    # 2. Rejection Before Tool Execution (Rule 1.7)
    # -------------------------------------------------------------------------

    def test_http_rejects_unauthenticated_requests_before_tool_runs(self):
        """
        Verify that missing auth, invalid signature, or expired token are rejected
        with 401 BEFORE any tool runs. Assert underlying tool function is never called.
        """
        with patch.dict(os.environ, {"CREDENTIAL_ENC_KEY": self.enc_key, "ERPNEXT_BASE_URL": self.base_url}):
            app = create_http_app(jwt_secret=self.jwt_secret)

            with patch("mcp_erpnext.crud.get_list") as mock_get_list:
                with TestClient(app) as client:
                    call_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "get_list", "arguments": {"doctype": "Customer"}},
                    }

                    # 1. Missing Authorization header
                    resp_missing = client.post("/mcp", json=call_payload)
                    self.assertEqual(resp_missing.status_code, 401)
                    mock_get_list.assert_not_called()

                    # 2. Invalid signature
                    bad_sig_token = self._mint_test_token(secret="wrong-secret-key-99999999999999999")
                    resp_badsig = client.post(
                        "/mcp",
                        headers={"Authorization": f"Bearer {bad_sig_token}"},
                        json=call_payload,
                    )
                    self.assertEqual(resp_badsig.status_code, 401)
                    mock_get_list.assert_not_called()

                    # 3. Expired token
                    expired_token = self._mint_test_token(exp_delta=datetime.timedelta(seconds=-10))
                    resp_expired = client.post(
                        "/mcp",
                        headers={"Authorization": f"Bearer {expired_token}"},
                        json=call_payload,
                    )
                    self.assertEqual(resp_expired.status_code, 401)
                    self.assertIn("expired", resp_expired.json().get("detail", "").lower())
                    mock_get_list.assert_not_called()

    # -------------------------------------------------------------------------
    # 3. Concurrency: Identity Isolation Across Concurrent Requests (Rule 1.7)
    # -------------------------------------------------------------------------

    def test_concurrent_requests_with_different_users_do_not_leak_identity(self):
        """
        Verify two concurrent callers with different users and roles never leak identity
        or cross-contaminate ContextVars during asynchronous execution.
        """
        token_alice = self._mint_test_token(sub="alice@example.com", roles=("Sales User",))
        token_bob = self._mint_test_token(sub="bob@example.com", roles=("Accounts Manager",))

        with patch.dict(os.environ, {"CREDENTIAL_ENC_KEY": self.enc_key, "ERPNEXT_BASE_URL": self.base_url}):
            app = create_http_app(jwt_secret=self.jwt_secret)

            async def sleep_and_capture(request: Request):
                # Introduce slight async delay to allow task interleaving
                await asyncio.sleep(0.02)
                ctx = get_request_context()
                return JSONResponse({
                    "user_id": ctx.user_id if ctx else None,
                    "roles": list(ctx.roles) if ctx else [],
                })

            app.add_route("/concurrent_test", sleep_and_capture, methods=["GET"])

            with TestClient(app) as client:
                def call_as(token: str) -> dict:
                    resp = client.get("/concurrent_test", headers={"Authorization": f"Bearer {token}"})
                    return resp.json()

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    futures = []
                    expected = []
                    for _ in range(10):
                        futures.append(executor.submit(call_as, token_alice))
                        expected.append(("alice@example.com", ["Sales User"]))
                        futures.append(executor.submit(call_as, token_bob))
                        expected.append(("bob@example.com", ["Accounts Manager"]))

                    for fut, (exp_user, exp_roles) in zip(futures, expected):
                        data = fut.result()
                        self.assertEqual(data["user_id"], exp_user)
                        self.assertEqual(data["roles"], exp_roles)

    # -------------------------------------------------------------------------
    # 4. InMemoryMCPClient Active Context Requirement (Rule 1.4)
    # -------------------------------------------------------------------------

    def test_in_memory_mcp_client_requires_active_context_in_strict_mode(self):
        """
        Verify InMemoryMCPClient refuses to run tools without an active RequestContext
        when ERPNEXT_STRICT_IDENTITY is enabled (default), and does not silently use get_default_context().
        """
        client = InMemoryMCPClient()

        # Strict identity enabled: calling tool without context must raise ERPNextConfigError
        with patch.dict(os.environ, {"ERPNEXT_STRICT_IDENTITY": "true"}):
            with self.assertRaises(ERPNextConfigError) as ctx_err:
                client.call_tool("get_list", {"doctype": "Customer"})
            self.assertIn("No active RequestContext found", str(ctx_err.exception))

        # Explicit context provided: succeeds
        creds = Credentials(base_url=self.base_url, auth_headers={"Cookie": "sid=active_sid"})
        user_ctx = RequestContext(user_id="active@example.com", roles=("Sales User",), credentials=creds, read_only=True)

        with patch("mcp_erpnext.crud.get_list", return_value={"status": "success", "data": []}):
            res = client.call_tool("get_list", {"doctype": "Customer"}, context=user_ctx)
            self.assertEqual(res["status"], "success")

    # -------------------------------------------------------------------------
    # 5. STDIO Transport Read-Only & Credential Guards (Rule 1.5)
    # -------------------------------------------------------------------------

    def test_stdio_transport_guards_refuses_writable_and_unconfigured_credentials(self):
        """
        Verify stdio transport:
        1. Refuses to start if read_only=False (never writable).
        2. Refuses to start if no credentials can be resolved.
        """
        writable_ctx = RequestContext(
            user_id="stdio_user",
            roles=("System Manager",),
            read_only=False,
            credentials=Credentials(base_url=self.base_url, auth_headers={}),
        )

        # 1. Refuses writable context
        with self.assertRaises(RuntimeError) as exc_writable:
            asyncio.run(run_stdio(default_context=writable_ctx))
        self.assertIn("STDIO transport is never writable", str(exc_writable.exception))

        # 2. Refuses when no credentials exist anywhere
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as exc_creds:
                asyncio.run(run_stdio(default_context=None, client=None))
            self.assertIn("no explicit read-only service credential configured", str(exc_creds.exception))

    # -------------------------------------------------------------------------
    # 6. Unified Auth & Base URL Fail-Closed (Rules 1.2 & 1.3)
    # -------------------------------------------------------------------------

    def test_base_url_fails_closed_when_unset(self):
        """Verify session credential decryption fails closed if ERPNEXT_BASE_URL is unset."""
        ctx_data = json.dumps({"sid": "test_sid", "csrf_token": "test_csrf"})
        encrypted_ctx = self.fernet.encrypt(ctx_data.encode("utf-8")).decode("utf-8")

        with patch.dict(os.environ, {"CREDENTIAL_ENC_KEY": self.enc_key}, clear=True):
            os.environ.pop("ERPNEXT_BASE_URL", None)
            os.environ.pop("ERPNEXT_URL", None)
            with self.assertRaises(ERPNextConfigError) as ctx_err:
                decrypt_session_credentials(encrypted_ctx, enc_key=self.enc_key)
            self.assertIn("ERPNEXT_BASE_URL is unconfigured", str(ctx_err.exception))

    def test_verify_jwt_unified_helper(self):
        """Verify unified verify_jwt validates secrets, signatures, and expiration."""
        token = self._mint_test_token(sub="alice@example.com")

        # Valid decode
        payload = verify_jwt(token, self.jwt_secret)
        self.assertEqual(payload["sub"], "alice@example.com")

        # Missing secret
        with self.assertRaises(ERPNextConfigError):
            verify_jwt(token, "")

        # Expired token
        expired_token = self._mint_test_token(exp_delta=datetime.timedelta(seconds=-5))
        with self.assertRaises(TokenExpiredError):
            verify_jwt(expired_token, self.jwt_secret)

        # Invalid token
        with self.assertRaises(AuthTokenError):
            verify_jwt("not.a.valid.jwt.token", self.jwt_secret)

    # -------------------------------------------------------------------------
    # 7. In-Memory MCP Client & Adapter Parity
    # -------------------------------------------------------------------------

    def test_in_memory_mcp_client_preserves_contextvar_identity(self):
        """Verify InMemoryMCPClient executes tools while preserving active RequestContext."""
        creds = Credentials(
            base_url=self.base_url,
            auth_headers={"Cookie": "sid=sandra_sid", "X-Frappe-CSRF-Token": "sandra_csrf"},
        )
        ctx = RequestContext(
            user_id="sandra@gmail.com",
            roles=("Sales User",),
            credentials=creds,
            read_only=True,
        )

        observed_client_headers = []

        def fake_crud_get_list(doctype, **kwargs):
            c = ERPNextClient()
            observed_client_headers.append(c.get_credentials().get_headers())
            return {"status": "success", "data": [{"name": "ITEM-001"}]}

        client = InMemoryMCPClient()

        with set_request_context(ctx):
            with patch("mcp_erpnext.crud.get_list", side_effect=fake_crud_get_list):
                res = client.call_tool("get_list", {"doctype": "Item"})
                self.assertEqual(res["status"], "success")

        self.assertEqual(len(observed_client_headers), 1)
        self.assertEqual(observed_client_headers[0].get("Cookie"), "sid=sandra_sid")
        self.assertEqual(observed_client_headers[0].get("X-Frappe-CSRF-Token"), "sandra_csrf")

    def test_direct_backend_and_in_memory_mcp_parity(self):
        """
        Verify identity preservation parity between direct ToolAdapter
        and ToolAdapter backed by InMemoryMCPClient.
        """
        creds = Credentials(
            base_url=self.base_url,
            auth_headers={"Cookie": "sid=parity_sid", "X-Frappe-CSRF-Token": "parity_csrf"},
        )
        ctx = RequestContext(
            user_id="sandra@gmail.com",
            roles=("Sales User",),
            credentials=creds,
            read_only=True,
        )

        direct_headers = []
        mcp_headers = []

        def fake_generic_get_list(doctype, **kwargs):
            c = ERPNextClient()
            direct_headers.append(c.get_credentials().get_headers())
            return {"status": "success", "data": []}

        def fake_crud_get_list(doctype, **kwargs):
            c = ERPNextClient()
            mcp_headers.append(c.get_credentials().get_headers())
            return {"status": "success", "data": []}

        # Direct backend
        with patch.dict(os.environ, {"TOOL_BACKEND": "direct"}):
            adapter_direct = ToolAdapter()
            with set_request_context(ctx):
                with patch("agent.tools.generic_tools.get_list", side_effect=fake_generic_get_list):
                    adapter_direct.get_list("Customer")

        # MCP backend with InMemoryMCPClient passed directly
        in_mem_client = InMemoryMCPClient()
        adapter_mcp = ToolAdapter(backend="mcp", mcp_client=in_mem_client)
        with set_request_context(ctx):
            with patch("mcp_erpnext.crud.get_list", side_effect=fake_crud_get_list):
                adapter_mcp.get_list("Customer")

        self.assertEqual(len(direct_headers), 1)
        self.assertEqual(len(mcp_headers), 1)
        self.assertEqual(direct_headers[0]["Cookie"], "sid=parity_sid")
        self.assertEqual(mcp_headers[0]["Cookie"], "sid=parity_sid")
        self.assertEqual(direct_headers[0]["X-Frappe-CSRF-Token"], "parity_csrf")
        self.assertEqual(mcp_headers[0]["X-Frappe-CSRF-Token"], "parity_csrf")

    def test_two_user_mcp_backend_uses_per_user_credentials_and_no_env_fallback(self):
        """
        Verify two users on TOOL_BACKEND=mcp with InMemoryMCPClient each send their own
        credentials/cookies to ERPNext over httpx, and that no call falls back to the
        .env service key when ERPNEXT_STRICT_IDENTITY=true.
        """
        import httpx
        from agent.tools.adapter import ToolAdapter

        creds_alice = Credentials(
            base_url="http://localhost:8080",
            auth_headers={"Cookie": "sid=alice_session_sid_111", "X-Frappe-CSRF-Token": "alice_csrf_111"},
        )
        ctx_alice = RequestContext(
            user_id="alice@example.com",
            roles=("Sales User",),
            credentials=creds_alice,
            read_only=True,
        )

        creds_bob = Credentials(
            base_url="http://localhost:8080",
            auth_headers={"Cookie": "sid=bob_session_sid_222", "X-Frappe-CSRF-Token": "bob_csrf_222"},
        )
        ctx_bob = RequestContext(
            user_id="bob@example.com",
            roles=("Accounts User",),
            credentials=creds_bob,
            read_only=True,
        )

        sent_requests = []

        def fake_send(self, request, *args, **kwargs):
            sent_requests.append(request)
            return httpx.Response(
                200,
                json={"data": [{"name": f"Customer-for-{request.headers.get('cookie')}"}]},
                request=request,
            )

        env_overrides = {
            "TOOL_BACKEND": "mcp",
            "ERPNEXT_STRICT_IDENTITY": "true",
            "ERPNEXT_BASE_URL": "http://localhost:8080",
            "ERPNEXT_API_KEY": "forbidden_env_service_key",
            "ERPNEXT_API_SECRET": "forbidden_env_service_secret",
            "ALLOW_DEV_MOCK_AUTH": "false",
        }

        with patch.dict(os.environ, env_overrides):
            in_mem_client = InMemoryMCPClient()
            adapter = ToolAdapter(backend="mcp", mcp_client=in_mem_client)

            with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                # 1. User Alice executes read tool via MCP backend
                with set_request_context(ctx_alice):
                    res_alice = adapter.get_list("Customer")
                    self.assertEqual(res_alice.get("status"), "success")

                # 2. User Bob executes read tool via MCP backend
                with set_request_context(ctx_bob):
                    res_bob = adapter.get_list("Customer")
                    self.assertEqual(res_bob.get("status"), "success")

                # Verify each request presented its own credentials to httpx
                self.assertEqual(len(sent_requests), 2)

                alice_req = sent_requests[0]
                bob_req = sent_requests[1]

                # Assert Alice's session cookie and CSRF token
                self.assertEqual(alice_req.headers.get("cookie"), "sid=alice_session_sid_111")
                self.assertEqual(alice_req.headers.get("x-frappe-csrf-token"), "alice_csrf_111")
                self.assertNotIn("forbidden_env_service_key", alice_req.headers.get("authorization", ""))

                # Assert Bob's session cookie and CSRF token
                self.assertEqual(bob_req.headers.get("cookie"), "sid=bob_session_sid_222")
                self.assertEqual(bob_req.headers.get("x-frappe-csrf-token"), "bob_csrf_222")
                self.assertNotIn("forbidden_env_service_key", bob_req.headers.get("authorization", ""))

                # Neither request used Authorization header with service credentials
                self.assertIsNone(alice_req.headers.get("authorization"))
                self.assertIsNone(bob_req.headers.get("authorization"))

                # 3. Caller with NO context: fails closed with strict identity, httpx NEVER called
                res_no_ctx = adapter.get_list("Customer")
                self.assertEqual(res_no_ctx.get("status"), "system_error")
                self.assertEqual(len(sent_requests), 2)  # No new outbound HTTP request

                # 4. Caller with context but NO credentials: fails closed, httpx NEVER called
                ctx_no_creds = RequestContext(
                    user_id="carol@example.com",
                    roles=("System Manager",),
                    credentials=None,
                    read_only=True,
                )
                with set_request_context(ctx_no_creds):
                    res_carol = adapter.get_list("Customer")
                    self.assertEqual(res_carol.get("status"), "system_error")
                    self.assertEqual(len(sent_requests), 2)  # No new outbound HTTP request


if __name__ == "__main__":
    unittest.main()
