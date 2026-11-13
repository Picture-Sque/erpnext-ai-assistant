import os
import sys
import unittest
import json
import httpx
import pytest
from unittest.mock import MagicMock, patch

# Ensure repo root and agent are on sys.path
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
agent_path = os.path.join(root_path, "agent")
if agent_path not in sys.path:
    sys.path.insert(0, agent_path)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from mcp_erpnext.defaults import (
    get_default_company,
    get_default_warehouse,
    reset_resolver_caches,
)
from mcp_erpnext.errors import ERPNextTransportError


class TestMCPErpnextDefaults(unittest.TestCase):

    def setUp(self):
        super().setUp()
        reset_resolver_caches()
        self._env_patch = patch.dict(os.environ, {
            "ERPNEXT_BASE_URL": "http://localhost:8081",
            "ERPNEXT_API_KEY": "test_api_key",
            "ERPNEXT_API_SECRET": "test_api_secret",
        }, clear=False)
        self._env_patch.start()
        os.environ.pop("DEFAULT_COMPANY", None)
        os.environ.pop("DEFAULT_WAREHOUSE", None)

    def tearDown(self):
        self._env_patch.stop()
        reset_resolver_caches()
        super().tearDown()

    @patch("mcp_erpnext.client.httpx.Client")
    def test_company_fetch_transport_error_falls_through_and_logs_warning(self, mock_client_cls):
        """Company-default fetch raising ERPNextTransportError logs a warning and falls through."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {
            "data": [
                {"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}
            ]
        }
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": {}}

        # warehouse list -> 200, company doc -> ConnectError (wrapped in ERPNextTransportError), stock settings -> 200
        mock_client.get.side_effect = [w_resp, httpx.ConnectError("timeout"), s_resp]

        with self.assertLogs("mcp_erpnext.defaults", level="WARNING") as log:
            wh = get_default_warehouse("TC")
            self.assertEqual(wh, "Stores - TC")
            self.assertTrue(any("Failed to fetch default warehouse from Company" in msg for msg in log.output))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_single_company_dynamic_resolution(self, mock_client_cls):
        """Single-company setup dynamically resolves company name when DEFAULT_COMPANY is unset."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        resp = MagicMock(is_success=True, status_code=200)
        resp.json.return_value = {"data": [{"name": "Solo Corp"}]}
        mock_client.get.return_value = resp

        comp = get_default_company()
        self.assertEqual(comp, "Solo Corp")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_multi_company_requires_default_company(self, mock_client_cls):
        """Multi-company setup raises RuntimeError when DEFAULT_COMPANY is unset."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        resp = MagicMock(is_success=True, status_code=200)
        resp.json.return_value = {"data": [{"name": "Company A"}, {"name": "Company B"}]}
        mock_client.get.return_value = resp

        with self.assertRaises(RuntimeError) as ctx:
            get_default_company()
        self.assertIn("Default company is not configured", str(ctx.exception))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_multi_company_with_valid_default_company(self, mock_client_cls):
        """Multi-company setup honors DEFAULT_COMPANY when valid."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        resp = MagicMock(is_success=True, status_code=200)
        resp.json.return_value = {"data": [{"name": "Company A"}, {"name": "Company B"}]}
        mock_client.get.return_value = resp

        with patch.dict(os.environ, {"DEFAULT_COMPANY": "Company B"}):
            comp = get_default_company()
            self.assertEqual(comp, "Company B")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_empty_erpnext_companies_raises_runtime_error(self, mock_client_cls):
        """Empty company list in ERPNext raises RuntimeError."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        resp = MagicMock(is_success=True, status_code=200)
        resp.json.return_value = {"data": []}
        mock_client.get.return_value = resp

        with self.assertRaises(RuntimeError) as ctx:
            get_default_company()
        self.assertIn("No companies found in ERPNext", str(ctx.exception))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_empty_erpnext_warehouses_raises_runtime_error(self, mock_client_cls):
        """Empty warehouse list for company in ERPNext raises RuntimeError."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        resp = MagicMock(is_success=True, status_code=200)
        resp.json.return_value = {"data": []}
        mock_client.get.return_value = resp

        with self.assertRaises(RuntimeError) as ctx:
            get_default_warehouse("Solo Corp")
        self.assertIn("No warehouses found for company 'Solo Corp'", str(ctx.exception))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_default_warehouse_env_wrong_company_ignored(self, mock_client_cls):
        """DEFAULT_WAREHOUSE of another company is ignored and logs info."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {
            "data": [
                {"name": "Stores - RightCo", "warehouse_name": "Stores", "company": "RightCo", "is_group": 0}
            ]
        }
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": {}}
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": {}}
        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        with patch.dict(os.environ, {"DEFAULT_WAREHOUSE": "Stores - WrongCo"}):
            with self.assertLogs("mcp_erpnext.defaults", level="INFO") as log:
                wh = get_default_warehouse("RightCo")
                self.assertEqual(wh, "Stores - RightCo")
                self.assertTrue(any("ignoring" in msg for msg in log.output))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_stock_settings_403_falls_through_and_logs_warning(self, mock_client_cls):
        """Stock Settings 403 logs a warning (with status code) and falls through."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {
            "data": [
                {"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}
            ]
        }
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": {}}
        s_resp = MagicMock(is_success=False, status_code=403)

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        with self.assertLogs("mcp_erpnext.defaults", level="WARNING") as log:
            wh = get_default_warehouse("TC")
            self.assertEqual(wh, "Stores - TC")
            self.assertTrue(any("Failed to fetch Stock Settings from ERPNext (403)" in msg for msg in log.output))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_company_data_null_does_not_raise(self, mock_client_cls):
        """"data": null does not raise AttributeError and falls through."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {
            "data": [
                {"name": "Finished Goods - TC", "warehouse_name": "Finished Goods", "company": "TC", "is_group": 0}
            ]
        }
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": None}
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": None}

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        wh = get_default_warehouse("TC")
        self.assertEqual(wh, "Finished Goods - TC")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_company_and_stock_settings_data_as_list_does_not_raise_and_falls_through(self, mock_client_cls):
        """When Company doc or Stock Settings returns data as a list (non-dict), it does not raise AttributeError and falls through."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {
            "data": [
                {"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}
            ]
        }
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": [{"some_field": "some_val"}]}
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": [{"setting_field": "val"}]}

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        wh = get_default_warehouse("TC")
        self.assertEqual(wh, "Stores - TC")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_preference_order_full_hierarchy(self, mock_client_cls):
        """Preference order: Company default > Stock Settings default > stores heuristic > first leaf warehouse."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        warehouses = [
            {"name": "All Warehouses - TC", "warehouse_name": "All", "company": "TC", "is_group": 1},
            {"name": "Leaf A - TC", "warehouse_name": "Leaf A", "company": "TC", "is_group": 0},
            {"name": "Stores - TC", "warehouse_name": "Stores", "company": "TC", "is_group": 0},
            {"name": "Stock Setting WH - TC", "warehouse_name": "Stock WH", "company": "TC", "is_group": 0},
            {"name": "Company Default WH - TC", "warehouse_name": "Comp WH", "company": "TC", "is_group": 0},
        ]
        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": warehouses}

        # Case 1: Company default wins
        c_resp1 = MagicMock(is_success=True, status_code=200)
        c_resp1.json.return_value = {"data": {"default_warehouse": "Company Default WH - TC"}}
        mock_client.get.side_effect = [w_resp, c_resp1]
        reset_resolver_caches()
        self.assertEqual(get_default_warehouse("TC"), "Company Default WH - TC")

        # Case 2: Stock Settings default wins when Company default absent
        c_resp2 = MagicMock(is_success=True, status_code=200)
        c_resp2.json.return_value = {"data": {}}
        s_resp2 = MagicMock(is_success=True, status_code=200)
        s_resp2.json.return_value = {"data": {"default_warehouse": "Stock Setting WH - TC"}}
        mock_client.get.side_effect = [w_resp, c_resp2, s_resp2]
        reset_resolver_caches()
        self.assertEqual(get_default_warehouse("TC"), "Stock Setting WH - TC")

        # Case 3: 'Stores' heuristic wins when Company and Stock settings defaults absent
        c_resp3 = MagicMock(is_success=True, status_code=200)
        c_resp3.json.return_value = {"data": {}}
        s_resp3 = MagicMock(is_success=True, status_code=200)
        s_resp3.json.return_value = {"data": {}}
        mock_client.get.side_effect = [w_resp, c_resp3, s_resp3]
        reset_resolver_caches()
        self.assertEqual(get_default_warehouse("TC"), "Stores - TC")

        # Case 4: First non-group leaf warehouse wins when no stores warehouse
        no_stores = [
            {"name": "Group - TC", "warehouse_name": "Group", "company": "TC", "is_group": 1},
            {"name": "Leaf A - TC", "warehouse_name": "Leaf A", "company": "TC", "is_group": 0},
            {"name": "Leaf B - TC", "warehouse_name": "Leaf B", "company": "TC", "is_group": 0},
        ]
        w_resp4 = MagicMock(is_success=True, status_code=200)
        w_resp4.json.return_value = {"data": no_stores}
        mock_client.get.side_effect = [w_resp4, c_resp3, s_resp3]
        reset_resolver_caches()
        self.assertEqual(get_default_warehouse("TC"), "Leaf A - TC")

    @patch("mcp_erpnext.client.httpx.Client")
    def test_warehouse_list_transport_failure_raises_runtime_error(self, mock_client_cls):
        """Transport failure on warehouse list becomes RuntimeError keeping the original message."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("Network unreachable")

        with self.assertRaises(RuntimeError) as ctx:
            get_default_warehouse("TC")

        self.assertIn("Failed to fetch Warehouse list for 'TC' from ERPNext", str(ctx.exception))
        self.assertIn("Network unreachable", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
