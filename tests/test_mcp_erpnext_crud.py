import os
import sys
import unittest
import json
import httpx
from unittest.mock import MagicMock, patch

# Ensure repo root is on sys.path
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from mcp_erpnext.client import Credentials, ERPNextClient
from mcp_erpnext.errors import ERPNextTransportError, ERPNextConfigError
from mcp_erpnext.crud import (
    create_document,
    update_document,
    get_document,
    get_list,
    search_document,
    delete_document,
    get_count,
    aggregate,
    submit_document,
    cancel_document,
)
from mcp_erpnext.defaults import reset_resolver_caches


class TestMCPErpnextCrudCredentialsFlow(unittest.TestCase):

    def setUp(self):
        super().setUp()
        reset_resolver_caches()
        self.env_patcher = patch.dict(os.environ, {
            "ERPNEXT_BASE_URL": "http://env-erp:8000",
            "ERPNEXT_API_KEY": "env_key",
            "ERPNEXT_API_SECRET": "env_secret",
            "DEFAULT_COMPANY": "",
            "DEFAULT_WAREHOUSE": "",
        })
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        reset_resolver_caches()
        super().tearDown()

    @patch("mcp_erpnext.client.httpx.Client")
    def test_create_document_uses_client_credentials_for_resolvers_and_post(self, mock_client_cls):
        """Every HTTP call during create_document (Company, Warehouse, Stock Settings, POST) must use client's credentials, not env."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Mock responses for company list, warehouse list, company doc, stock settings, and post
        def mock_get(url, *args, **kwargs):
            resp = MagicMock(is_success=True, status_code=200)
            if "/api/resource/Company" in url and not url.endswith("/Company/TenantA-Comp"):
                resp.json.return_value = {"data": [{"name": "TenantA-Comp"}]}
            elif "/api/resource/Warehouse" in url:
                resp.json.return_value = {"data": [{"name": "Stores - TAC", "company": "TenantA-Comp", "is_group": 0}]}
            elif url.endswith("/Company/TenantA-Comp"):
                resp.json.return_value = {"data": {"default_warehouse": "Stores - TAC"}}
            elif "Stock Settings" in url:
                resp.json.return_value = {"data": {}}
            else:
                resp.json.return_value = {"data": {}}
            return resp

        mock_client.get.side_effect = mock_get
        post_resp = MagicMock(is_success=True, status_code=201)
        post_resp.json.return_value = {"data": {"name": "SO-001", "docstatus": 0}}
        mock_client.post.return_value = post_resp

        creds_a = Credentials(base_url="http://tenant-a:8000", api_key="key_a", api_secret="secret_a")
        client_a = ERPNextClient(credentials=creds_a)

        res = create_document("Sales Order", {"customer": "Customer A"}, client=client_a)
        self.assertEqual(res["status"], "success")

        # Verify all GET and POST calls used Tenant A's base_url and Authorization header
        for call in mock_client.get.call_args_list:
            url, = call[0]
            headers = call[1].get("headers", {})
            self.assertTrue(url.startswith("http://tenant-a:8000/"), f"GET URL used wrong base: {url}")
            self.assertEqual(headers.get("Authorization"), "token key_a:secret_a")
            self.assertFalse(url.startswith("http://env-erp:8000/"))

        post_url, = mock_client.post.call_args[0]
        post_headers = mock_client.post.call_args[1].get("headers", {})
        self.assertTrue(post_url.startswith("http://tenant-a:8000/"))
        self.assertEqual(post_headers.get("Authorization"), "token key_a:secret_a")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_update_document_sales_order_uses_client_credentials_for_getter_and_put(self, mock_client_cls):
        """update_document with delivery_date must use client's credentials for both get_document and PUT."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        get_resp = MagicMock(is_success=True, status_code=200)
        get_resp.json.return_value = {
            "data": {
                "name": "SO-001",
                "items": [{"item_code": "ITEM-1", "delivery_date": "2026-11-01"}]
            }
        }
        mock_client.get.return_value = get_resp

        put_resp = MagicMock(is_success=True, status_code=200)
        put_resp.json.return_value = {"data": {"name": "SO-001", "delivery_date": "2026-12-01"}}
        mock_client.put.return_value = put_resp

        creds_a = Credentials(base_url="http://tenant-a:8000", api_key="key_a", api_secret="secret_a")
        client_a = ERPNextClient(credentials=creds_a)

        res = update_document("Sales Order", "SO-001", {"delivery_date": "2026-12-01"}, client=client_a)
        self.assertEqual(res["status"], "success")

        get_url, = mock_client.get.call_args[0]
        get_headers = mock_client.get.call_args[1].get("headers", {})
        self.assertTrue(get_url.startswith("http://tenant-a:8000/"))
        self.assertEqual(get_headers.get("Authorization"), "token key_a:secret_a")

        put_url, = mock_client.put.call_args[0]
        put_headers = mock_client.put.call_args[1].get("headers", {})
        self.assertTrue(put_url.startswith("http://tenant-a:8000/"))
        self.assertEqual(put_headers.get("Authorization"), "token key_a:secret_a")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_submit_and_cancel_document_pass_client_to_update(self, mock_client_cls):
        """submit_document and cancel_document pass client to update_document."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        put_resp = MagicMock(is_success=True, status_code=200)
        put_resp.json.return_value = {"data": {"name": "SO-001"}}
        mock_client.put.return_value = put_resp

        creds_a = Credentials(base_url="http://tenant-a:8000", api_key="key_a", api_secret="secret_a")
        client_a = ERPNextClient(credentials=creds_a)

        submit_document("Sales Order", "SO-001", client=client_a)
        put_url, = mock_client.put.call_args[0]
        put_headers = mock_client.put.call_args[1].get("headers", {})
        self.assertTrue(put_url.startswith("http://tenant-a:8000/"))
        self.assertEqual(put_headers.get("Authorization"), "token key_a:secret_a")

        cancel_document("Sales Order", "SO-001", client=client_a)
        put_url, = mock_client.put.call_args[0]
        put_headers = mock_client.put.call_args[1].get("headers", {})
        self.assertTrue(put_url.startswith("http://tenant-a:8000/"))
        self.assertEqual(put_headers.get("Authorization"), "token key_a:secret_a")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_two_clients_do_not_share_default_company_cache(self, mock_client_cls):
        """Two clients with different credentials never share the default-company cache."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        def mock_get(url, *args, **kwargs):
            resp = MagicMock(is_success=True, status_code=200)
            if "tenant-a" in url:
                resp.json.return_value = {"data": [{"name": "Company A"}]}
            elif "tenant-b" in url:
                resp.json.return_value = {"data": [{"name": "Company B"}]}
            elif "/Warehouse" in url:
                resp.json.return_value = {"data": [{"name": "Stores", "company": "Company A", "is_group": 0}]}
            else:
                resp.json.return_value = {"data": {}}
            return resp

        mock_client.get.side_effect = mock_get
        post_resp = MagicMock(is_success=True, status_code=201)
        post_resp.json.return_value = {"data": {"name": "Doc-1"}}
        mock_client.post.return_value = post_resp

        creds_a = Credentials(base_url="http://tenant-a:8000", api_key="key_a", api_secret="secret_a")
        client_a = ERPNextClient(credentials=creds_a)
        creds_b = Credentials(base_url="http://tenant-b:8000", api_key="key_b", api_secret="secret_b")
        client_b = ERPNextClient(credentials=creds_b)

        create_document("Sales Order", {"customer": "Cust A"}, client=client_a)
        create_document("Sales Order", {"customer": "Cust B"}, client=client_b)

        # Tenant B's POST must have company="Company B", NOT "Company A"
        last_post_call = mock_client.post.call_args_list[-1]
        posted_data = last_post_call[1].get("json", {})
        self.assertEqual(posted_data.get("company"), "Company B")


class TestMCPErpnextCrudDirect(unittest.TestCase):
    """Direct tests of every CRUD function asserting 5-key envelope across all statuses."""

    def setUp(self):
        super().setUp()
        self.mock_client = MagicMock(spec=ERPNextClient)

    def _assert_envelope(self, res: dict, expected_status: str):
        self.assertIsInstance(res, dict)
        base_keys = {"status", "data", "error", "has_more", "success"}
        page_keys = {"total_count", "offset", "limit", "truncated"}
        # Read tools add pagination metadata on success/empty; everything else keeps the base keys.
        self.assertIn(set(res.keys()), (base_keys, base_keys | page_keys))
        self.assertEqual(res["status"], expected_status)
        if expected_status in ("success", "empty"):
            self.assertTrue(res["success"])
            self.assertIsNone(res["error"])
        else:
            self.assertFalse(res["success"])
            self.assertIsNotNone(res["error"])

    def _make_response(self, status_code: int, json_data: any = None, text: str = ""):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.is_success = (200 <= status_code < 300)
        resp.text = text or (json.dumps(json_data) if json_data is not None else "")
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = ValueError("Non-JSON")
        return resp

    # --- get_list ---
    def test_get_list_success(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "Cust-1"}]})
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"], [{"name": "Cust-1"}])

    def test_get_list_empty(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": []})
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], [])

    def test_get_list_not_found(self):
        self.mock_client.get.return_value = self._make_response(404, {"message": "Not found"})
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_get_list_permission_denied_403(self):
        self.mock_client.get.return_value = self._make_response(403, {"message": "Forbidden"})
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_get_list_permission_denied_whitelist(self):
        res = get_list("ForbiddenDocType", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    def test_get_list_system_error_500(self):
        self.mock_client.get.return_value = self._make_response(500, {"exception": "DB Error"})
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    def test_get_list_transport_error_becomes_system_error(self):
        self.mock_client.get.side_effect = ERPNextTransportError("Connection failed")
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Connection failed", res["error"])

    def test_get_list_config_error_becomes_system_error(self):
        self.mock_client.get.side_effect = ERPNextConfigError("Missing API key")
        res = get_list("Customer", client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Missing API key", res["error"])

    # --- get_document ---
    def test_get_document_success(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": {"name": "Cust-1", "customer_name": "Acme"}})
        res = get_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["customer_name"], "Acme")

    def test_get_document_not_found(self):
        self.mock_client.get.return_value = self._make_response(404, {"message": "Customer Cust-99 does not exist"})
        res = get_document("Customer", "Cust-99", client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_get_document_permission_denied(self):
        self.mock_client.get.return_value = self._make_response(401, {"message": "Unauthorized"})
        res = get_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_get_document_system_error_500(self):
        self.mock_client.get.return_value = self._make_response(500, text="Internal Server Error")
        res = get_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    def test_get_document_transport_error(self):
        self.mock_client.get.side_effect = ERPNextTransportError("Timeout")
        res = get_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Timeout", res["error"])

    def test_get_document_200_json_list_returns_invalid_json_system_error(self):
        """A 200 response whose body is a JSON list (not object) triggers AttributeError and returns Invalid JSON response."""
        self.mock_client.get.return_value = self._make_response(200, [{"name": "Cust-1"}])
        res = get_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertEqual(res["error"], "Invalid JSON response")

    # --- search_document ---
    def test_search_document_success(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "Cust-1"}]})
        res = search_document("Customer", query="Acme", client=self.mock_client)
        self._assert_envelope(res, "success")

    def test_search_document_empty(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": []})
        res = search_document("Customer", query="Nonexistent", client=self.mock_client)
        self._assert_envelope(res, "empty")

    def test_search_document_not_found(self):
        self.mock_client.get.return_value = self._make_response(404, {"message": "DocType not found"})
        res = search_document("Customer", query="Acme", client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_search_document_system_error(self):
        self.mock_client.get.return_value = self._make_response(500, {"exception": "Server Crash"})
        res = search_document("Customer", query="Acme", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    # --- create_document ---
    def test_create_document_success(self):
        self.mock_client.post.return_value = self._make_response(201, {"data": {"name": "Cust-100"}})
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["name"], "Cust-100")

    def test_create_document_whitelist_rejection(self):
        res = create_document("SecretDoctype", {"field": "val"}, client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_create_document_not_found_404(self):
        self.mock_client.post.return_value = self._make_response(404, {"message": "DocType Customer not found"})
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_create_document_permission_denied_403(self):
        self.mock_client.post.return_value = self._make_response(403, {"message": "No write permission"})
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_create_document_system_error_500(self):
        self.mock_client.post.return_value = self._make_response(500, {"exception": "DB write lock"})
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")

    def test_create_document_transport_error(self):
        self.mock_client.post.side_effect = ERPNextTransportError("Connection reset")
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Connection reset", res["error"])

    def test_create_document_200_json_list_returns_invalid_json_system_error(self):
        self.mock_client.post.return_value = self._make_response(200, [{"name": "Cust-1"}])
        res = create_document("Customer", {"customer_name": "New Corp"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertEqual(res["error"], "Invalid JSON response")

    # --- update_document ---
    def test_update_document_success(self):
        self.mock_client.put.return_value = self._make_response(200, {"data": {"name": "Cust-1", "phone": "12345"}})
        res = update_document("Customer", "Cust-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["phone"], "12345")

    def test_update_document_whitelist_rejection(self):
        res = update_document("SecretDoctype", "S-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_update_document_not_found_404(self):
        self.mock_client.put.return_value = self._make_response(404, {"message": "Cust-99 not found"})
        res = update_document("Customer", "Cust-99", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_update_document_permission_denied_403(self):
        self.mock_client.put.return_value = self._make_response(403, {"message": "Cannot update document"})
        res = update_document("Customer", "Cust-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_update_document_system_error_500(self):
        self.mock_client.put.return_value = self._make_response(500, {"exception": "Update failed"})
        res = update_document("Customer", "Cust-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")

    def test_update_document_transport_error(self):
        self.mock_client.put.side_effect = ERPNextTransportError("Host down")
        res = update_document("Customer", "Cust-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Host down", res["error"])

    def test_update_document_200_json_list_returns_invalid_json_system_error(self):
        self.mock_client.put.return_value = self._make_response(200, [{"name": "Cust-1"}])
        res = update_document("Customer", "Cust-1", {"phone": "12345"}, client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertEqual(res["error"], "Invalid JSON response")

    # --- delete_document ---
    def test_delete_document_success(self):
        self.mock_client.delete.return_value = self._make_response(200, {"message": "ok"})
        res = delete_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "success")

    def test_delete_document_whitelist_rejection(self):
        res = delete_document("SecretDoctype", "S-1", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_delete_document_not_found_404(self):
        self.mock_client.delete.return_value = self._make_response(404, {"message": "Not found"})
        res = delete_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_delete_document_permission_denied_403(self):
        self.mock_client.delete.return_value = self._make_response(403, {"message": "Delete forbidden"})
        res = delete_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_delete_document_system_error_500(self):
        self.mock_client.delete.return_value = self._make_response(500, text="Crash")
        res = delete_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    def test_delete_document_transport_error(self):
        self.mock_client.delete.side_effect = ERPNextTransportError("Network error")
        res = delete_document("Customer", "Cust-1", client=self.mock_client)
        self._assert_envelope(res, "system_error")
        self.assertIn("Network error", res["error"])

    # --- pagination metadata ---
    def test_get_list_offset_sent_as_limit_start(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "C3"}]})
        res = get_list("Customer", limit=2, offset=4, client=self.mock_client)
        _, kwargs = self.mock_client.get.call_args
        self.assertEqual(kwargs["params"]["limit_start"], "4")
        self.assertEqual(kwargs["params"]["limit_page_length"], "3")
        self.assertEqual(res["offset"], 4)
        self.assertEqual(res["limit"], 2)

    def test_get_list_no_offset_omits_limit_start(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "C1"}]})
        get_list("Customer", client=self.mock_client)
        _, kwargs = self.mock_client.get.call_args
        self.assertNotIn("limit_start", kwargs["params"])

    def test_get_list_last_page_knows_total_without_extra_call(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "C3"}, {"name": "C4"}]})
        res = get_list("Customer", limit=5, offset=10, client=self.mock_client)
        self.assertEqual(res["total_count"], 12)
        self.assertFalse(res["truncated"])
        self.assertFalse(res["has_more"])
        self.assertEqual(self.mock_client.get.call_count, 1)

    def test_get_list_truncated_total_unknown_by_default(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": [{"name": "C1"}, {"name": "C2"}, {"name": "C3"}]})
        res = get_list("Customer", limit=2, client=self.mock_client)
        self.assertTrue(res["truncated"])
        self.assertTrue(res["has_more"])
        self.assertIsNone(res["total_count"])
        self.assertEqual(len(res["data"]), 2)
        self.assertEqual(self.mock_client.get.call_count, 1)

    def test_get_list_include_total_makes_one_count_call(self):
        self.mock_client.get.side_effect = [
            self._make_response(200, {"data": [{"name": "C1"}, {"name": "C2"}, {"name": "C3"}]}),
            self._make_response(200, {"message": 57}),
        ]
        res = get_list("Customer", limit=2, include_total=True, client=self.mock_client)
        self.assertEqual(res["total_count"], 57)
        self.assertTrue(res["truncated"])
        self.assertEqual(self.mock_client.get.call_count, 2)
        count_args, _ = self.mock_client.get.call_args
        self.assertTrue(count_args[0].endswith("frappe.client.get_count"))

    def test_get_list_include_total_count_failure_does_not_fail_the_list(self):
        self.mock_client.get.side_effect = [
            self._make_response(200, {"data": [{"name": "C1"}, {"name": "C2"}, {"name": "C3"}]}),
            self._make_response(500, text="boom"),
        ]
        res = get_list("Customer", limit=2, include_total=True, client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertIsNone(res["total_count"])
        self.assertTrue(res["truncated"])

    def test_get_list_invalid_offset_is_rejected_without_a_call(self):
        for bad in (-1, "abc", True):
            res = get_list("Customer", offset=bad, client=self.mock_client)
            self._assert_envelope(res, "system_error")
            self.assertIn("Invalid offset", res["error"])
        self.mock_client.get.assert_not_called()

    def test_get_list_errors_keep_the_base_envelope(self):
        self.mock_client.get.return_value = self._make_response(500, text="boom")
        res = get_list("Customer", client=self.mock_client)
        self.assertEqual(set(res.keys()), {"status", "data", "error", "has_more", "success"})

    # --- get_count ---
    def test_get_count_rejects_malformed_count(self):
        for bad in ({"message": "12"}, {"message": None}, {"message": True}, {}):
            self.mock_client.get.return_value = self._make_response(200, bad)
            res = get_count("Customer", client=self.mock_client)
            self._assert_envelope(res, "system_error")

    def test_get_count_permission_denied(self):
        self.mock_client.get.return_value = self._make_response(403, {"message": "no"})
        res = get_count("Customer", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")

    def test_get_count_sends_filters_as_json(self):
        self.mock_client.get.return_value = self._make_response(200, {"message": 4})
        get_count("Sales Order", filters=[["docstatus", "=", 1]], client=self.mock_client)
        _, kwargs = self.mock_client.get.call_args
        self.assertEqual(kwargs["params"]["filters"], '[["docstatus", "=", 1]]')

    def test_get_count_success(self):
        self.mock_client.get.return_value = self._make_response(200, {"message": 2})
        res = get_count("Customer", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"], 2)
        self.assertEqual(res["total_count"], 2)

    def test_get_count_empty(self):
        self.mock_client.get.return_value = self._make_response(200, {"message": 0})
        res = get_count("Customer", client=self.mock_client)
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], 0)

    def test_get_count_system_error_500(self):
        self.mock_client.get.return_value = self._make_response(500, text="Internal error")
        res = get_count("Customer", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    # --- aggregate ---
    def test_aggregate_success(self):
        self.mock_client.get.return_value = self._make_response(200, {
            "data": [
                {"name": "SO-1", "customer": "Acme", "grand_total": 100.0},
                {"name": "SO-2", "customer": "Acme", "grand_total": 200.0},
                {"name": "SO-3", "customer": "Beta", "grand_total": 50.0},
            ]
        })
        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(len(res["data"]), 2)

    def test_aggregate_empty(self):
        self.mock_client.get.return_value = self._make_response(200, {"data": []})
        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum", client=self.mock_client)
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], [])

    def test_aggregate_system_error_500(self):
        self.mock_client.get.return_value = self._make_response(500, text="Server Error")
        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum", client=self.mock_client)
        self._assert_envelope(res, "system_error")

    # --- submit_document and cancel_document ---
    def test_submit_document_success(self):
        self.mock_client.put.return_value = self._make_response(200, {"data": {"name": "SO-001", "docstatus": 1}})
        res = submit_document("Sales Order", "SO-001", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["docstatus"], 1)

    def test_cancel_document_success(self):
        self.mock_client.put.return_value = self._make_response(200, {"data": {"name": "SO-001", "docstatus": 2}})
        res = cancel_document("Sales Order", "SO-001", client=self.mock_client)
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["docstatus"], 2)

    def test_submit_document_not_found(self):
        self.mock_client.put.return_value = self._make_response(404, {"message": "SO-99 not found"})
        res = submit_document("Sales Order", "SO-99", client=self.mock_client)
        self._assert_envelope(res, "not_found")

    def test_cancel_document_permission_denied(self):
        self.mock_client.put.return_value = self._make_response(403, {"message": "Not allowed to cancel"})
        res = cancel_document("Sales Order", "SO-001", client=self.mock_client)
        self._assert_envelope(res, "permission_denied")


if __name__ == "__main__":
    unittest.main()
