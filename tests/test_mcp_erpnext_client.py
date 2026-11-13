import os
import sys
import json
import unittest
import httpx
import pytest
from unittest.mock import MagicMock, patch

# Ensure root directory is on sys.path
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from mcp_erpnext.client import Credentials, ERPNextClient, get_env_credentials
from mcp_erpnext.errors import ERPNextConfigError, ERPNextTransportError, ERPNextError, extract_erpnext_error as mcp_extract_error


class TestMCPErpnextClient(unittest.TestCase):

    def test_credentials_repr_redacts_secret_and_key(self):
        """Credentials repr must never leak API secret, API key, or sensitive custom headers."""
        creds = Credentials(
            base_url="http://erp.local:8000",
            api_key="my_key_12345",
            api_secret="super_secret_password_value",
        )
        repr_str = repr(creds)
        self.assertNotIn("super_secret_password_value", repr_str)
        self.assertNotIn("my_key_12345", repr_str)
        self.assertNotIn("12345", repr_str)
        self.assertIn("api_key='[REDACTED]'", repr_str)
        self.assertIn("api_secret='[REDACTED]'", repr_str)

    def test_credentials_repr_redacts_custom_headers(self):
        creds = Credentials(
            base_url="http://erp.local:8000",
            auth_headers={"Authorization": "Bearer sensitive_token_xyz"}
        )
        repr_str = repr(creds)
        self.assertNotIn("sensitive_token_xyz", repr_str)
        self.assertIn("[CUSTOM_HEADERS]", repr_str)

    def test_per_request_credentials_authorization_header_and_base_url(self):
        """Per-request credentials produce expected headers and target base_url."""
        creds = Credentials(
            base_url="https://tenant-a.erpnext.com/",
            api_key="tenant_key",
            api_secret="tenant_secret",
        )
        self.assertEqual(creds.base_url, "https://tenant-a.erpnext.com")
        headers = creds.get_headers()
        self.assertEqual(headers["Authorization"], "token tenant_key:tenant_secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")

    def test_per_request_custom_headers(self):
        """Per-request credentials with custom auth headers (e.g. Bearer token) are honored."""
        creds = Credentials(
            base_url="https://tenant-b.erpnext.com",
            auth_headers={"Authorization": "Bearer jwt_session_token_123"}
        )
        headers = creds.get_headers()
        self.assertEqual(headers["Authorization"], "Bearer jwt_session_token_123")

    def test_env_fallback_read_at_call_time(self):
        """Environment variables are evaluated lazily at call time, not at module import time."""
        with patch.dict(os.environ, {
            "ERPNEXT_BASE_URL": "http://dynamic.env:9000",
            "ERPNEXT_API_KEY": "dyn_key",
            "ERPNEXT_API_SECRET": "dyn_secret",
        }):
            creds = get_env_credentials()
            self.assertEqual(creds.base_url, "http://dynamic.env:9000")
            headers = creds.get_headers()
            self.assertEqual(headers["Authorization"], "token dyn_key:dyn_secret")

    def test_missing_credentials_raises_config_error(self):
        """When API key or secret are missing in env, ERPNextConfigError is raised at call time."""
        with patch.dict(os.environ, {
            "ERPNEXT_API_KEY": "",
            "ERPNEXT_API_SECRET": "",
        }):
            client = ERPNextClient()
            with self.assertRaises(ERPNextConfigError) as ctx:
                client.get("/api/resource/Customer")
            self.assertIn("ERPNext credentials not configured", str(ctx.exception))
            self.assertIsInstance(ctx.exception, RuntimeError)

    @patch("mcp_erpnext.client.httpx.Client")
    def test_per_request_client_sends_to_target_base_url_and_auth_header(self, mock_client_cls):
        """Client built with per-request Credentials sends request to target base_url with that Authorization header."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_client.get.return_value = mock_resp

        creds = Credentials(
            base_url="https://custom-erp.company.org",
            auth_headers={"Authorization": "token custom_user_key:custom_user_secret"}
        )
        client = ERPNextClient(credentials=creds)
        client.get("api/resource/Item", params={"limit_page_length": 5})

        mock_client.get.assert_called_once()
        call_args, call_kwargs = mock_client.get.call_args
        self.assertEqual(call_args[0], "https://custom-erp.company.org/api/resource/Item")
        self.assertEqual(call_kwargs["headers"]["Authorization"], "token custom_user_key:custom_user_secret")
        self.assertEqual(call_kwargs["params"], {"limit_page_length": 5})
        self.assertEqual(call_kwargs["timeout"], 60.0)

    @patch("mcp_erpnext.client.httpx.Client")
    def test_client_get_called_exactly_once(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.return_value = MagicMock(is_success=True, status_code=200)

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)
        client.get("api/resource/Customer", params={"limit": 10}, timeout=15.0)

        mock_client.get.assert_called_once_with(
            "http://localhost:8080/api/resource/Customer",
            headers=creds.get_headers(),
            params={"limit": 10},
            timeout=15.0
        )

    @patch("mcp_erpnext.client.httpx.Client")
    def test_client_post_called_exactly_once(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = MagicMock(is_success=True, status_code=201)

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)
        client.post("api/resource/Sales Order", json={"customer": "Acme"})

        mock_client.post.assert_called_once_with(
            "http://localhost:8080/api/resource/Sales Order",
            headers=creds.get_headers(),
            json={"customer": "Acme"},
            timeout=60.0
        )

    @patch("mcp_erpnext.client.httpx.Client")
    def test_client_put_called_exactly_once(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.put.return_value = MagicMock(is_success=True, status_code=200)

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)
        client.put("api/resource/Sales Order/SO-001", json={"delivery_date": "2026-11-01"})

        mock_client.put.assert_called_once_with(
            "http://localhost:8080/api/resource/Sales Order/SO-001",
            headers=creds.get_headers(),
            json={"delivery_date": "2026-11-01"},
            timeout=60.0
        )

    @patch("mcp_erpnext.client.httpx.Client")
    def test_client_delete_called_exactly_once(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.delete.return_value = MagicMock(is_success=True, status_code=200)

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)
        client.delete("api/resource/Customer/CUST-001")

        mock_client.delete.assert_called_once_with(
            "http://localhost:8080/api/resource/Customer/CUST-001",
            headers=creds.get_headers(),
            timeout=60.0
        )

    @patch("mcp_erpnext.client.httpx.Client")
    def test_transport_error_is_runtime_error_subclass_and_keeps_message(self, mock_client_cls):
        """httpx.HTTPError is wrapped in ERPNextTransportError, subclassing RuntimeError with message kept."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("Connection refused by ERPNext server")

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)

        with self.assertRaises(ERPNextTransportError) as ctx:
            client.get("api/resource/Customer")

        self.assertIsInstance(ctx.exception, ERPNextError)
        self.assertIsInstance(ctx.exception, RuntimeError)
        self.assertIn("Connection refused by ERPNext server", str(ctx.exception))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_no_retries_on_write_operations(self, mock_client_cls):
        """Write operations (POST, PUT, DELETE) must never retry on failure."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectTimeout("Connection timed out writing doc")

        creds = Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s")
        client = ERPNextClient(credentials=creds)

        with self.assertRaises(ERPNextTransportError):
            client.post("api/resource/Sales Order", json={"customer": "Test Corp"})

        # Assert exactly one call was made - NO retries!
        self.assertEqual(mock_client.post.call_count, 1)

    # -------------------------------------------------------------
    # Error extraction tests: extract_erpnext_error
    # -------------------------------------------------------------

    def _create_mock_response(self, status_code: int, json_data: any = None, text: str = ""):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = text if text else (json.dumps(json_data) if json_data is not None else "")
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = ValueError("Non-JSON")
        return resp

    def test_extract_erpnext_error_server_messages(self):
        resp = self._create_mock_response(400, {
            "_server_messages": json.dumps([json.dumps({"message": "Mandatory fields required"})])
        })
        new_out = mcp_extract_error(resp)
        self.assertEqual(new_out, "Mandatory fields required")

    def test_extract_erpnext_error_nested_json(self):
        resp = self._create_mock_response(400, {
            "_server_messages": json.dumps([
                json.dumps({"message": "Invalid company warehouse combination"}),
                json.dumps({"message": "Stock not available"})
            ])
        })
        new_out = mcp_extract_error(resp)
        self.assertEqual(new_out, "Invalid company warehouse combination | Stock not available")

    def test_extract_erpnext_error_exception_field(self):
        resp = self._create_mock_response(500, {
            "exception": "frappe.exceptions.ValidationError: Row 1 warehouse does not belong to company"
        })
        new_out = mcp_extract_error(resp)
        self.assertEqual(new_out, "frappe.exceptions.ValidationError: Row 1 warehouse does not belong to company")

    def test_extract_erpnext_error_plain_message(self):
        resp = self._create_mock_response(404, {"message": "Document SO-99999 does not exist"})
        new_out = mcp_extract_error(resp)
        self.assertEqual(new_out, "Document SO-99999 does not exist")

    def test_extract_erpnext_error_non_json_body(self):
        resp = self._create_mock_response(502, json_data=None, text="Bad Gateway - Nginx")
        new_out = mcp_extract_error(resp)
        self.assertEqual(new_out, "HTTP 502: Bad Gateway - Nginx")


if __name__ == "__main__":
    unittest.main()
