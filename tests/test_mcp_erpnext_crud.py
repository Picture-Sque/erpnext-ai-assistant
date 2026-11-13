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
from mcp_erpnext.crud import (
    create_document,
    update_document,
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


if __name__ == "__main__":
    unittest.main()
