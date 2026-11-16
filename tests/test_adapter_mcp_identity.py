"""
Unit tests for ToolAdapter MCP-mode identity preservation and isolation.

Validates Requirements P1.0 (a through i):
 a. Users A and B with different Cookie sids get only their own rows and carry only their own cookie, never service key.
 b. With a user context active the STDIO/service-key client is never used (strict on, off, or unset).
 c. Direct vs MCP parity for the same user and question.
 d. No user context and strict identity on: system_error envelope, no service key.
 e. Concurrency: two users calling adapter simultaneously from separate threads (50+ iterations), each getting only their own rows/cookie.
 f. Worker thread with copied context (contextvars.copy_context().run) carries user identity.
 g. NEGATIVE: worker thread with NO copied context under strict identity fails closed, never uses service key.
 h. Identity keys passed as tool arguments are denied / ignored without overriding context.
 i. No cookie, token, or credential value appears in captured log lines, exceptions, or envelopes.
"""

import concurrent.futures
import contextvars
import hashlib
import io
import json
import logging
import os
import unittest
from unittest.mock import MagicMock, patch

import httpx

from agent.tools.adapter import ToolAdapter
from agent.tools.mcp_client import ERPNextMCPClient
from mcp_erpnext.auth import (
    RequestContext,
    set_request_context,
    get_request_context,
    current_request_context,
)
from mcp_erpnext.client import Credentials
from mcp_erpnext.server import InMemoryMCPClient


class TestAdapterMCPIdentity(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.base_url = "http://localhost:8080"
        self.service_key = "env_service_api_key_123"
        self.service_secret = "env_service_api_secret_456"

        self.cookie_a = "sid=user_alice_secret_session_sid_aaa"
        self.cookie_b = "sid=user_bob_secret_session_sid_bbb"

        self.creds_a = Credentials(
            base_url=self.base_url,
            auth_headers={"Cookie": self.cookie_a, "X-Frappe-CSRF-Token": "csrf_token_alice"},
        )
        self.ctx_a = RequestContext(
            user_id="alice@example.com",
            roles=("Sales User",),
            credentials=self.creds_a,
            read_only=True,
        )

        self.creds_b = Credentials(
            base_url=self.base_url,
            auth_headers={"Cookie": self.cookie_b, "X-Frappe-CSRF-Token": "csrf_token_bob"},
        )
        self.ctx_b = RequestContext(
            user_id="bob@example.com",
            roles=("Sales User",),
            credentials=self.creds_b,
            read_only=True,
        )

        self.env_base = {
            "TOOL_BACKEND": "mcp",
            "ERPNEXT_BASE_URL": self.base_url,
            "ERPNEXT_API_KEY": self.service_key,
            "ERPNEXT_API_SECRET": self.service_secret,
            "ALLOW_DEV_MOCK_AUTH": "false",
        }

    # -------------------------------------------------------------------------
    # a. Users A and B with different Cookie sids
    # -------------------------------------------------------------------------
    def test_a_two_users_mcp_get_only_their_own_rows_and_cookies(self):
        """Users A and B get only their own rows and outgoing requests carry matching cookie, never service key."""
        captured_requests = []

        def fake_send(client_self, request, *args, **kwargs):
            captured_requests.append(request)
            req_cookie = request.headers.get("cookie", "")
            auth_header = request.headers.get("authorization", "")

            # If service key was used, return all rows (the bug)
            if self.service_key in auth_header:
                return httpx.Response(
                    200,
                    json={"data": [{"name": "Cust-All-1"}, {"name": "Cust-All-2"}]},
                    request=request,
                )

            # Per-user permission response
            if self.cookie_a in req_cookie:
                return httpx.Response(
                    200,
                    json={"data": [{"name": "Cust-Alice", "customer_name": "Alice Corp"}]},
                    request=request,
                )
            elif self.cookie_b in req_cookie:
                return httpx.Response(
                    200,
                    json={"data": [{"name": "Cust-Bob", "customer_name": "Bob LLC"}]},
                    request=request,
                )
            return httpx.Response(403, json={"message": "Forbidden"}, request=request)

        with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
            adapter = ToolAdapter(backend="mcp")

            with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                # User A query
                with set_request_context(self.ctx_a):
                    res_a = adapter.get_list("Customer")
                    self.assertEqual(res_a.get("status"), "success")
                    self.assertEqual(len(res_a.get("data", [])), 1)
                    self.assertEqual(res_a["data"][0]["name"], "Cust-Alice")

                # User B query
                with set_request_context(self.ctx_b):
                    res_b = adapter.get_list("Customer")
                    self.assertEqual(res_b.get("status"), "success")
                    self.assertEqual(len(res_b.get("data", [])), 1)
                    self.assertEqual(res_b["data"][0]["name"], "Cust-Bob")

            # Verify outgoing requests
            self.assertEqual(len(captured_requests), 2)
            hash_a = hashlib.sha256(self.cookie_a.encode()).hexdigest()[:8]
            hash_b = hashlib.sha256(self.cookie_b.encode()).hexdigest()[:8]

            req_a = captured_requests[0]
            req_b = captured_requests[1]

            cookie_hdr_a = req_a.headers.get("cookie", "")
            cookie_hdr_b = req_b.headers.get("cookie", "")

            hash_req_a = hashlib.sha256(cookie_hdr_a.encode()).hexdigest()[:8] if cookie_hdr_a else "none"
            hash_req_b = hashlib.sha256(cookie_hdr_b.encode()).hexdigest()[:8] if cookie_hdr_b else "none"

            auth_hdr_a = req_a.headers.get("authorization")
            auth_hdr_b = req_b.headers.get("authorization")

            print(f"\n[test_a] User A rows ({res_a['data'][0]['name']}) paired with Cookie SHA-256={hash_req_a}, Authorization={auth_hdr_a}")
            print(f"[test_a] User B rows ({res_b['data'][0]['name']}) paired with Cookie SHA-256={hash_req_b}, Authorization={auth_hdr_b}")

            # Assertions comparing cookies and proving no service authorization header
            self.assertEqual(hash_req_a, hash_a)
            self.assertEqual(hash_req_b, hash_b)
            self.assertIsNone(auth_hdr_a)
            self.assertIsNone(auth_hdr_b)
            self.assertEqual(req_a.headers.get("cookie"), self.cookie_a)
            self.assertEqual(req_b.headers.get("cookie"), self.cookie_b)
            self.assertNotIn("authorization", req_a.headers)
            self.assertNotIn("authorization", req_b.headers)

    # -------------------------------------------------------------------------
    # b. STDIO/service-key client is never used with user context active
    # -------------------------------------------------------------------------
    def test_b_stdio_client_never_used_with_user_context_active(self):
        """With user context active, STDIO/service client is never used with strict on, off, or unset."""
        for strict_val in ("true", "false", ""):
            env = dict(self.env_base)
            if strict_val:
                env["ERPNEXT_STRICT_IDENTITY"] = strict_val
            else:
                env.pop("ERPNEXT_STRICT_IDENTITY", None)

            with patch.dict(os.environ, env):
                adapter = ToolAdapter(backend="mcp")

                # Guard: if ERPNextMCPClient.call_tool or connect is called, fail test immediately
                with patch.object(
                    ERPNextMCPClient,
                    "call_tool",
                    side_effect=AssertionError("ERPNextMCPClient.call_tool (STDIO) was invoked for user request!"),
                ), patch.object(
                    ERPNextMCPClient,
                    "connect",
                    side_effect=AssertionError("ERPNextMCPClient.connect (STDIO) was invoked for user request!"),
                ):
                    with set_request_context(self.ctx_a):
                        client = adapter._get_mcp_client()
                        self.assertNotIsInstance(
                            client,
                            ERPNextMCPClient,
                            f"Adapter selected ERPNextMCPClient (STDIO) with strict='{strict_val}'!",
                        )

    # -------------------------------------------------------------------------
    # c. Direct vs MCP Parity for same user and question
    # -------------------------------------------------------------------------
    def test_c_direct_vs_mcp_parity_for_same_user(self):
        """Direct and MCP results are identical for the same user and question."""
        expected_rows = [{"name": "Cust-Alice", "customer_name": "Alice Corp"}]

        def fake_send(client_self, request, *args, **kwargs):
            return httpx.Response(
                200,
                json={"data": expected_rows},
                request=request,
            )

        with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
            with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send), \
                 patch("agent.tools.generic_tools.httpx.Client.send", new=fake_send):
                with set_request_context(self.ctx_a):
                    adapter_direct = ToolAdapter(backend="direct")
                    res_direct = adapter_direct.get_list("Customer", limit=10)

                    adapter_mcp = ToolAdapter(backend="mcp")
                    res_mcp = adapter_mcp.get_list("Customer", limit=10)

                    self.assertEqual(res_direct.get("status"), res_mcp.get("status"))
                    self.assertEqual(res_direct.get("data"), res_mcp.get("data"))

    # -------------------------------------------------------------------------
    # d. No user context and strict identity on -> fails closed, no service key
    # -------------------------------------------------------------------------
    def test_d_no_user_context_strict_identity_fails_closed_system_error(self):
        """With no user context (strict on or unset), adapter returns system_error envelope and no service key."""
        for strict_val in ("true", ""):
            captured_requests = []

            def fake_send(client_self, request, *args, **kwargs):
                captured_requests.append(request)
                return httpx.Response(200, json={"data": [{"name": "Cust-Leaked"}]}, request=request)

            env = dict(self.env_base)
            if strict_val:
                env["ERPNEXT_STRICT_IDENTITY"] = strict_val
            else:
                env.pop("ERPNEXT_STRICT_IDENTITY", None)

            with patch.dict(os.environ, env):
                adapter = ToolAdapter(backend="mcp")

                with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                    # Ensure no context is active
                    current_request_context.set(None)
                    res = adapter.get_list("Customer")

                    self.assertEqual(res.get("status"), "system_error")
                    self.assertFalse(res.get("success", True))
                    self.assertIn("Strict identity", res.get("error", "") + res.get("message", ""))
                    # No request should have reached ERPNext with service key
                    self.assertEqual(len(captured_requests), 0)

    # -------------------------------------------------------------------------
    # e. Concurrency: two users calling adapter simultaneously (50+ runs)
    # -------------------------------------------------------------------------
    def test_e_concurrency_two_users_simultaneous_50_iterations(self):
        """Two users calling adapter simultaneously from separate threads (50 iterations) never leak rows or cookies."""
        def fake_send(client_self, request, *args, **kwargs):
            cookie = request.headers.get("cookie", "")
            if self.cookie_a in cookie:
                return httpx.Response(200, json={"data": [{"name": "Alice-Row"}]}, request=request)
            elif self.cookie_b in cookie:
                return httpx.Response(200, json={"data": [{"name": "Bob-Row"}]}, request=request)
            return httpx.Response(403, json={"error": "Leaked/Forbidden"}, request=request)

        with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
            adapter = ToolAdapter(backend="mcp")

            with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                def run_user(ctx, expected_name):
                    with set_request_context(ctx):
                        res = adapter.get_list("Customer")
                        return res.get("data", [{}])[0].get("name") == expected_name

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    for _ in range(50):
                        fut_a = executor.submit(run_user, self.ctx_a, "Alice-Row")
                        fut_b = executor.submit(run_user, self.ctx_b, "Bob-Row")
                        self.assertTrue(fut_a.result(), "User A received incorrect or leaked data during concurrency!")
                        self.assertTrue(fut_b.result(), "User B received incorrect or leaked data during concurrency!")

    # -------------------------------------------------------------------------
    # f. Worker thread with copied context (LangGraph copy_context)
    # -------------------------------------------------------------------------
    def test_f_worker_thread_with_copied_context_preserves_identity(self):
        """A worker thread whose context was copied (contextvars.copy_context().run) carries user identity."""
        def fake_send(client_self, request, *args, **kwargs):
            cookie = request.headers.get("cookie", "")
            if self.cookie_a in cookie:
                return httpx.Response(200, json={"data": [{"name": "Alice-Copied"}]}, request=request)
            return httpx.Response(403, json={"error": "No cookie"}, request=request)

        with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
            adapter = ToolAdapter(backend="mcp")

            with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                with set_request_context(self.ctx_a):
                    ctx_snapshot = contextvars.copy_context()

                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        fut = executor.submit(ctx_snapshot.run, adapter.get_list, "Customer")
                        res = fut.result()
                        self.assertEqual(res.get("status"), "success")
                        self.assertEqual(res["data"][0]["name"], "Alice-Copied")

    # -------------------------------------------------------------------------
    # g. NEGATIVE: worker thread with NO copied context fails closed
    # -------------------------------------------------------------------------
    def test_g_worker_thread_no_copied_context_fails_closed(self):
        """Worker thread with NO copied context (strict on or unset) fails closed, never uses service key."""
        for strict_val in ("true", ""):
            captured = []

            def fake_send(client_self, request, *args, **kwargs):
                captured.append(request)
                return httpx.Response(200, json={"data": [{"name": "Service-Leaked"}]}, request=request)

            env = dict(self.env_base)
            if strict_val:
                env["ERPNEXT_STRICT_IDENTITY"] = strict_val
            else:
                env.pop("ERPNEXT_STRICT_IDENTITY", None)

            with patch.dict(os.environ, env):
                adapter = ToolAdapter(backend="mcp")

                with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        # Submit directly with NO contextvars copying
                        fut = executor.submit(adapter.get_list, "Customer")
                        res = fut.result()

                        self.assertEqual(res.get("status"), "system_error")
                        self.assertFalse(res.get("success", True))
                        self.assertEqual(len(captured), 0, "Outgoing request was sent using service key on un-contexted thread!")

    # -------------------------------------------------------------------------
    # h. Identity keys passed as tool arguments are denied / rejected
    # -------------------------------------------------------------------------
    def test_h_identity_keys_in_arguments_are_denied(self):
        """Identity keys passed as tool arguments (roles, user_id, read_only, credential, request_id) are rejected."""
        with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
            adapter = ToolAdapter(backend="mcp")

            with set_request_context(self.ctx_a):
                for forged_key, forged_val in [
                    ("user_id", "Administrator"),
                    ("roles", ["System Manager"]),
                    ("read_only", False),
                    ("credential", {"api_key": "hacked"}),
                    ("request_id", "req-fake-123"),
                ]:
                    # 1. Calling adapter directly with forged identity kwarg is refused by Python interface
                    with self.assertRaises(TypeError):
                        adapter.get_list("Customer", **{forged_key: forged_val})

                    # 2. Calling MCP client directly with forged identity key in args must be rejected by schema/policy
                    client = adapter._get_mcp_client()
                    res = client.call_tool("get_list", {"doctype": "Customer", forged_key: forged_val})
                    self.assertIn(
                        res.get("status"),
                        ("error", "system_error", "permission_denied"),
                        f"Forged identity argument '{forged_key}' was accepted without error!",
                    )

    # -------------------------------------------------------------------------
    # i. No cookie, token, or credential value appears in logs or exceptions
    # -------------------------------------------------------------------------
    def test_i_no_credential_values_in_logs_or_messages(self):
        """No cookie, token, or credential value appears in any captured log line or exception message."""
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)

        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

        try:
            with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
                adapter = ToolAdapter(backend="mcp")

                with set_request_context(self.ctx_a):
                    # Trigger both valid and error execution
                    adapter.get_list("Customer")
                    adapter.get_document("Customer", "NONEXISTENT")

            captured_logs = log_stream.getvalue()
            # Assert secrets are absent
            self.assertNotIn(self.cookie_a, captured_logs)
            self.assertNotIn("user_alice_secret_session_sid_aaa", captured_logs)
            self.assertNotIn("csrf_token_alice", captured_logs)
            self.assertNotIn(self.service_secret, captured_logs)
        finally:
            root_logger.removeHandler(handler)

    # -------------------------------------------------------------------------
    # j. Structured logging: allowed read, policy denial, read-only write block
    # -------------------------------------------------------------------------
    def test_j_structured_logging_lifecycle_denial_and_read_only_write(self):
        """Adapter logs lifecycle, policy denial, and read-only write block without leaking credentials or args."""
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.INFO)
        adapter_logger = logging.getLogger("agent.tools.adapter")
        adapter_logger.addHandler(handler)

        def fake_send(client_self, request, *args, **kwargs):
            return httpx.Response(200, json={"data": [{"name": "Cust-Alice"}]}, request=request)

        try:
            with patch.dict(os.environ, {**self.env_base, "ERPNEXT_STRICT_IDENTITY": "true"}):
                adapter = ToolAdapter(backend="mcp")

                with patch("mcp_erpnext.client.httpx.Client.send", new=fake_send):
                    with set_request_context(self.ctx_a):
                        # 1. Allowed read
                        res_read = adapter.get_list("Customer", limit=5)
                        self.assertEqual(res_read.get("status"), "success")

                        # 2. Policy denial (unpermitted doctype)
                        res_denial = adapter.get_list("SecretInternalDoc", limit=5)
                        self.assertEqual(res_denial.get("status"), "permission_denied")

                        # 3. Read-only write block (write attempted under read_only=True context)
                        res_write = adapter.create_document("Customer", {"customer_name": "SECRET_ARG_VALUE"})
                        self.assertEqual(res_write.get("status"), "permission_denied")

            lines = [line.strip() for line in log_stream.getvalue().splitlines() if line.strip()]

            # Expected lines:
            self.assertTrue(len(lines) >= 3, f"Expected at least 3 log lines, got: {lines}")

            # Find specific log lines
            allowed_read_start = [l for l in lines if "MCP call: tool=get_list" in l and "transport=in-process" in l]
            allowed_read_done = [l for l in lines if "MCP call completed: tool=get_list" in l and "status=success" in l]
            policy_denial_warn = [l for l in lines if "MCP policy denial: tool=get_list" in l]
            write_block_warn = [l for l in lines if "MCP read-only write blocked: tool=create_document" in l]

            self.assertTrue(allowed_read_start, "Allowed read start line missing!")
            self.assertTrue(allowed_read_done, "Allowed read completion line missing!")
            self.assertTrue(policy_denial_warn, "Policy denial warning line missing!")
            self.assertTrue(write_block_warn, "Read-only write block warning line missing!")

            # Assert each line contains only expected structural keys
            for l in [allowed_read_start[0], allowed_read_done[0], policy_denial_warn[0], write_block_warn[0]]:
                self.assertIn("tool=", l)
                self.assertNotIn(self.cookie_a, l)
                self.assertNotIn("user_alice_secret_session_sid_aaa", l)
                self.assertNotIn("csrf_token_alice", l)
                self.assertNotIn(self.service_key, l)
                self.assertNotIn(self.service_secret, l)
                self.assertNotIn("SECRET_ARG_VALUE", l)
        finally:
            adapter_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
