import os
import sys
import unittest
import json
import httpx
import pytest
from unittest.mock import MagicMock, patch

# Ensure agent and root directories are in path
agent_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if agent_path not in sys.path:
    sys.path.insert(0, agent_path)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from agent.tools.generic_tools import (
    create_document,
    get_list,
    update_document,
    delete_document,
    get_document,
    search_document,
    get_count,
    aggregate,
    cancel_document,
    submit_document,
    is_doctype_allowed,
    get_allowed_doctypes,
    extract_erpnext_error,
    get_default_company,
    get_default_warehouse,
)
import agent.tools.generic_tools as generic_tools
from mcp_erpnext.defaults import reset_resolver_caches


@pytest.fixture(autouse=True)
def reset_globals_and_env(monkeypatch):
    reset_resolver_caches()
    monkeypatch.delenv("DEFAULT_COMPANY", raising=False)
    monkeypatch.delenv("DEFAULT_WAREHOUSE", raising=False)
    yield
    reset_resolver_caches()


@pytest.fixture(autouse=True)
def erpnext_env_defaults():
    with patch.dict(os.environ, {
        "ERPNEXT_BASE_URL": "http://localhost:8081",
        "ERPNEXT_API_KEY": "test_api_key",
        "ERPNEXT_API_SECRET": "test_api_secret",
    }):
        yield


class TestGenericTools(unittest.TestCase):

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

    def test_whitelist_defaults_and_case_insensitivity(self):
        """Test default whitelist and case-insensitive check."""
        with patch.dict(os.environ, {}, clear=True):
            allowed = get_allowed_doctypes()
            self.assertIn("Customer", allowed)
            self.assertIn("Item", allowed)
            self.assertIn("Sales Order", allowed)
            self.assertIn("Bin", allowed)
            self.assertIn("Quotation", allowed)
            self.assertIn("Sales Invoice", allowed)
            self.assertIn("Purchase Order", allowed)
            self.assertIn("Supplier", allowed)
            self.assertIn("Delivery Note", allowed)
            self.assertIn("Delivery Note Item", allowed)
            self.assertIn("Purchase Receipt", allowed)
            self.assertIn("Purchase Receipt Item", allowed)
            self.assertIn("Stock Ledger Entry", allowed)

            self.assertTrue(is_doctype_allowed("Customer"))
            self.assertTrue(is_doctype_allowed("customer"))
            self.assertTrue(is_doctype_allowed("Sales Order"))
            self.assertTrue(is_doctype_allowed("sales order"))
            self.assertTrue(is_doctype_allowed("Supplier"))
            self.assertTrue(is_doctype_allowed("supplier"))
            self.assertTrue(is_doctype_allowed("Delivery Note"))
            self.assertTrue(is_doctype_allowed("delivery note"))
            self.assertTrue(is_doctype_allowed("Purchase Receipt"))
            self.assertTrue(is_doctype_allowed("purchase receipt"))
            self.assertTrue(is_doctype_allowed("Stock Ledger Entry"))
            self.assertTrue(is_doctype_allowed("stock ledger entry"))
            self.assertFalse(is_doctype_allowed("User"))
            self.assertFalse(is_doctype_allowed("Employee"))
            self.assertFalse(is_doctype_allowed("Payment Entry"))

    def test_custom_whitelist_env(self):
        """Test custom DOCTYPE_WHITELIST env var."""
        with patch.dict(os.environ, {"DOCTYPE_WHITELIST": "Supplier, User, Customer"}):
            self.assertTrue(is_doctype_allowed("Supplier"))
            self.assertTrue(is_doctype_allowed("user"))
            self.assertTrue(is_doctype_allowed("Customer"))
            self.assertFalse(is_doctype_allowed("Bin"))
            self.assertFalse(is_doctype_allowed("Delivery Note"))
            self.assertFalse(is_doctype_allowed("Purchase Receipt"))
            self.assertFalse(is_doctype_allowed("Stock Ledger Entry"))

        with patch.dict(os.environ, {"DOCTYPE_WHITELIST": "Customer, Item, Purchase Receipt"}):
            self.assertTrue(is_doctype_allowed("Purchase Receipt"))
            self.assertTrue(is_doctype_allowed("purchase receipt"))
            self.assertTrue(is_doctype_allowed("Customer"))
            self.assertTrue(is_doctype_allowed("Item"))
            self.assertFalse(is_doctype_allowed("Delivery Note"))
            self.assertFalse(is_doctype_allowed("Stock Ledger Entry"))
            self.assertFalse(is_doctype_allowed("Bin"))

        # Verify environment cleanup and default restoration
        self.assertNotIn("DOCTYPE_WHITELIST", os.environ)
        self.assertTrue(is_doctype_allowed("Delivery Note"))
        self.assertTrue(is_doctype_allowed("Bin"))

    def test_extract_erpnext_error(self):
        """Test extracting error messages from ERPNext response JSON structures."""
        # Case 1: _server_messages with JSON string
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "_server_messages": json.dumps([json.dumps({"message": "Mandatory field 'customer' missing"})])
        }
        err_msg = extract_erpnext_error(mock_resp)
        self.assertEqual(err_msg, "Mandatory field 'customer' missing")

        # Case 2: exception string
        mock_resp.json.return_value = {
            "exception": "frappe.exceptions.DoesNotExistError: Item SKU999 not found"
        }
        err_msg = extract_erpnext_error(mock_resp)
        self.assertEqual(err_msg, "frappe.exceptions.DoesNotExistError: Item SKU999 not found")

        # Case 3: plain message
        mock_resp.json.return_value = {"message": "Invalid credentials"}
        err_msg = extract_erpnext_error(mock_resp)
        self.assertEqual(err_msg, "Invalid credentials")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_success_and_param_formatting(self, mock_client_cls):
        """Test get_list correctly formats query params and returns ERPNext data."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "CUST-001", "customer_name": "West View Store"}
            ]
        }
        mock_client.get.return_value = mock_resp

        res = get_list("Customer", 
            filters=[["customer_name", "like", "%West View%"]],
            fields=["name", "customer_name"],
            limit=10
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["name"], "CUST-001")

        # Verify query params passed to httpx.get
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params["filters"], '[["customer_name", "like", "%West View%"]]')
        self.assertEqual(params["fields"], '["name", "customer_name"]')
        self.assertEqual(params["limit_page_length"], "11")
        self.assertNotIn("order_by", params)

    def test_get_list_whitelist_rejection(self):
        """Test get_list blocks non-whitelisted DocTypes."""
        res = get_list("User", filters=[])
        self.assertEqual(res["status"], "permission_denied")
        self.assertIn("DocType 'User' is not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.get_default_warehouse", return_value="Stores - MF")
    @patch("agent.tools.generic_tools.get_default_company", return_value="Metal Forms")
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_success(self, mock_client_cls, mock_comp, mock_wh):
        """Test create_document creates a document and returns ERPNext response."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "data": {
                "name": "SALES-ORD-0001",
                "customer": "West View Store",
                "docstatus": 0
            }
        }
        mock_client.post.return_value = mock_resp

        payload = {
            "customer": "West View Store",
            "items": [{"item_code": "SKU005", "qty": 2}]
        }
        res = create_document("Sales Order", payload)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["name"], "SALES-ORD-0001")

    @patch("agent.tools.generic_tools.get_default_warehouse", return_value="Stores - MF")
    @patch("agent.tools.generic_tools.get_default_company", return_value="Metal Forms")
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_validation_error(self, mock_client_cls, mock_comp, mock_wh):
        """Test create_document preserves specific ERPNext error messages on failure."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "_server_messages": json.dumps([json.dumps({"message": "Delivery Date is required"})])
        }
        mock_client.post.return_value = mock_resp

        res = create_document("Sales Order", {"customer": "Test"})

        self.assertEqual(res["status"], "system_error")
        self.assertEqual(res["error"], "Delivery Date is required")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_update_document_success(self, mock_client_cls):
        """Test update_document performs PUT call with parameters."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"name": "CUST-001", "customer_name": "Updated Name"}
        }
        mock_client.put.return_value = mock_resp

        res = update_document("Customer", "CUST-001", {"customer_name": "Updated Name"})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["customer_name"], "Updated Name")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_delete_document_success(self, mock_client_cls):
        """Test delete_document performs DELETE call."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "ok"}
        mock_client.delete.return_value = mock_resp

        res = delete_document("Customer", "CUST-001")
        self.assertEqual(res["status"], "success")

    def test_sales_order_date_defaulting(self):
        """Test default transaction_date and delivery_date in collect_parameters_node."""
        from datetime import datetime, timedelta
        from agent.workflow.graph import collect_parameters_node
        # Setup mock/empty state
        state = {
            "messages": [],
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Grant Plastics Ltd",
                "items": [{"item_code": "SKU009", "qty": 10}]
            }
        }
        res = collect_parameters_node(state)
        fields = res["collected_fields"]
        self.assertIn("transaction_date", fields)
        self.assertIn("delivery_date", fields)
        
        # Verify transaction_date is today's date
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(fields["transaction_date"], today_str)
        
        # Verify delivery_date is transaction_date + 7 days
        expected_delivery = (datetime.now().date() + timedelta(days=7)).strftime("%Y-%m-%d")
        self.assertEqual(fields["delivery_date"], expected_delivery)

    def test_sales_order_date_validation_success(self):
        """Test validation passes when delivery_date is strictly after transaction_date."""
        from agent.workflow.graph import validate_parameters_node
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Grant Plastics Ltd",
                "items": [{"item_code": "SKU009", "qty": 10}],
                "transaction_date": "2026-08-08",
                "delivery_date": "2026-08-15"
            }
        }
        res = validate_parameters_node(state)
        self.assertTrue(res["all_required_filled"])
        self.assertNotIn("delivery_date (must be strictly after transaction_date)", res.get("missing_parameters", []))

    def test_sales_order_date_validation_failure(self):
        """
        Test date auto-healing: when delivery_date <= transaction_date, the node clears the
        invalid date, re-applies the skill default (transaction_date + 7d), then re-validates.
        Since the default produces a valid date, the node returns all_required_filled=True with
        the healed delivery_date in place. This is intentional UX — invalid dates are auto-corrected
        rather than causing a hard failure that blocks the user.
        """
        from datetime import timedelta
        from agent.workflow.graph import validate_parameters_node
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Grant Plastics Ltd",
                "items": [{"item_code": "SKU009", "qty": 10}],
                "transaction_date": "2026-08-08",
                "delivery_date": "2026-08-08"  # invalid — same as transaction_date
            }
        }
        res = validate_parameters_node(state)
        # After auto-heal: delivery_date should be replaced by transaction_date + 7d
        # and validation should pass (no longer blocked)
        self.assertTrue(res["all_required_filled"])
        self.assertIn("delivery_date", res["collected_fields"])
        # The healed delivery_date must be strictly after transaction_date
        from datetime import datetime
        healed = datetime.strptime(res["collected_fields"]["delivery_date"], "%Y-%m-%d").date()
        tx = datetime.strptime("2026-08-08", "%Y-%m-%d").date()
        self.assertGreater(healed, tx)

        # A past date should also be auto-healed the same way
        state2 = {
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Grant Plastics Ltd",
                "items": [{"item_code": "SKU009", "qty": 10}],
                "transaction_date": "2026-08-08",
                "delivery_date": "2026-08-05"  # invalid — before transaction_date
            }
        }
        res2 = validate_parameters_node(state2)
        self.assertTrue(res2["all_required_filled"])
        self.assertIn("delivery_date", res2["collected_fields"])
        healed2 = datetime.strptime(res2["collected_fields"]["delivery_date"], "%Y-%m-%d").date()
        self.assertGreater(healed2, tx)

    @patch("agent.workflow.graph.invoke_structured_llm")
    @patch("agent.workflow.graph.create_document")
    def test_sales_order_payload_explicit_dates(self, mock_create_document, mock_invoke_structured_llm):
        """Test that transaction_date and delivery_date are explicitly present in the tool call parameters."""
        from agent.workflow.graph import call_generic_tool_node
        # Mock LLM returning None to trigger fallback parameter reinforcement
        mock_invoke_structured_llm.return_value = None
        mock_create_document.return_value = {"status": "success"}
        
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Grant Plastics Ltd",
                "items": [{"item_code": "SKU009", "qty": 10}],
                "transaction_date": "2026-08-08",
                "delivery_date": "2026-08-15"
            }
        }
        
        call_generic_tool_node(state)
        
        # Verify create_document was called with transaction_date and delivery_date
        mock_create_document.assert_called_once()
        args, kwargs = mock_create_document.call_args
        called_doctype, called_params = args
        self.assertEqual(called_doctype, "Sales Order")
        self.assertEqual(called_params["transaction_date"], "2026-08-08")
        self.assertEqual(called_params["delivery_date"], "2026-08-15")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_omitted_order_by(self, mock_client_cls):
        """Test get_list omits order_by parameter when not provided."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_client.get.return_value = mock_resp

        res = get_list("Customer", limit=5)
        self.assertEqual(res["status"], "empty")
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertNotIn("order_by", params)

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_valid_single_order_by(self, mock_client_cls):
        """Test get_list passes a valid single-column order_by clause."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "SLE-001"}]}
        mock_client.get.return_value = mock_resp

        res = get_list("Stock Ledger Entry", order_by="posting_date desc")
        self.assertEqual(res["status"], "success")
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params.get("order_by"), "posting_date desc")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_valid_multi_order_by(self, mock_client_cls):
        """Test get_list passes a valid multi-column order_by clause with mixed casing and spacing."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "SLE-001"}]}
        mock_client.get.return_value = mock_resp

        res = get_list("Stock Ledger Entry", order_by="posting_date DESC, posting_time desc")
        self.assertEqual(res["status"], "success")
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params.get("order_by"), "posting_date DESC, posting_time desc")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_sql_injection_rejected_before_http_call(self, mock_client_cls):
        """Test invalid SQL injection-like inputs are rejected before HTTP request is made."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        dangerous_inputs = [
            "posting_date desc; DROP TABLE tabStockLedgerEntry",
            "posting_date desc -- SQL comment",
            "(SELECT 1 FROM tabUser)",
            "1=1",
            "name; DELETE FROM tabBin",
            "name UNION SELECT * FROM tabUser",
        ]
        for bad_order_by in dangerous_inputs:
            res = get_list("Stock Ledger Entry", order_by=bad_order_by)
            self.assertEqual(res["status"], "system_error", f"Expected failure for {bad_order_by}")
            self.assertIn("Invalid order_by clause", res["error"])

        # Crucial check: client.get was never called for any dangerous input
        mock_client.get.assert_not_called()

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_invalid_direction_and_malformed_clauses_rejected(self, mock_client_cls):
        """Test invalid direction words and malformed clauses are rejected before HTTP call."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        malformed_inputs = [
            "",
            "   ",
            ",",
            "posting_date,",
            ",posting_date",
            "posting_date,,posting_time",
            "posting_date invalid_direction",
            "posting_date asc desc",
            "posting-date desc",
            "posting_date desc desc",
        ]
        for bad_clause in malformed_inputs:
            res = get_list("Stock Ledger Entry", order_by=bad_clause)
            self.assertEqual(res["status"], "system_error", f"Expected failure for {bad_clause}")
            self.assertIn("Invalid order_by clause", res["error"])

        mock_client.get.assert_not_called()

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_list_positional_and_keyword_callers_compatible(self, mock_client_cls):
        """Test existing positional and keyword callers remain 100% compatible."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "CUST-001"}]}
        mock_client.get.return_value = mock_resp

        # Positional call with 4 arguments
        res_pos = get_list("Customer", [["customer_name", "like", "%Test%"]], ["name"], 10)
        self.assertEqual(res_pos["status"], "success")

        # Keyword call with only limit
        res_kw = get_list("Customer", limit=1)
        self.assertEqual(res_kw["status"], "success")

    @patch("agent.workflow.graph.get_list")
    @patch("agent.workflow.graph._get_skill_for_intent")
    def test_workflow_call_generic_tool_node_order_by_passthrough(self, mock_get_skill, mock_get_list):
        """Test that workflow call_generic_tool_node passes skill query_parameters.order_by to get_list."""
        from agent.workflow.graph import call_generic_tool_node
        mock_get_skill.return_value = {
            "tool": "get_list",
            "doctype": "Stock Ledger Entry",
            "query_parameters": {
                "filters": [["item_code", "=", "{item_code}"]],
                "fields": ["name", "posting_date", "posting_time"],
                "order_by": "posting_date desc, posting_time desc"
            }
        }
        mock_get_list.return_value = {"status": "success", "data": []}
        state = {
            "detected_intent": "stock-movement-analysis",
            "collected_fields": {"item_code": "SKU001"}
        }

        call_generic_tool_node(state)

        mock_get_list.assert_called_once_with(
            "Stock Ledger Entry",
            filters=[["item_code", "=", "SKU001"]],
            fields=["name", "posting_date", "posting_time"],
            limit=None,
            order_by="posting_date desc, posting_time desc"
        )

    # -------------------------------------------------------------------------
    # get_default_company tests
    # -------------------------------------------------------------------------
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_company_single_company(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "Acme Corp"}]}
        mock_client.get.return_value = mock_resp

        comp = get_default_company()
        self.assertEqual(comp, "Acme Corp")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_company_multiple_companies_unset_raises(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "Comp A"}, {"name": "Comp B"}]}
        mock_client.get.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            get_default_company()
        self.assertIn("Default company is not configured", str(ctx.exception))

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_company_env_not_in_list_single_company_fallback(self, mock_client_cls):
        os.environ["DEFAULT_COMPANY"] = "Unknown Company"
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "Only Co"}]}
        mock_client.get.return_value = mock_resp

        comp = get_default_company()
        self.assertEqual(comp, "Only Co")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_company_connect_error_raises_runtime_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        with self.assertRaises(RuntimeError) as ctx:
            get_default_company()
        self.assertIn("Failed to fetch Company list from ERPNext", str(ctx.exception))

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_company_non_2xx_raises_runtime_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.json.return_value = {}
        mock_client.get.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            get_default_company()
        self.assertIn("500", str(ctx.exception))

    # -------------------------------------------------------------------------
    # get_default_warehouse tests
    # -------------------------------------------------------------------------
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_warehouse_env_wrong_company_ignored(self, mock_client_cls):
        os.environ["DEFAULT_WAREHOUSE"] = "Other WH"
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": [
            {"name": "Target WH", "warehouse_name": "Target WH", "company": "Target Co", "is_group": 0}
        ]}
        c_resp = MagicMock(is_success=False, status_code=404)
        s_resp = MagicMock(is_success=False, status_code=404)

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        wh = get_default_warehouse("Target Co")
        self.assertEqual(wh, "Target WH")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_warehouse_preference_order(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        warehouses_data = [
            {"name": "Wh Comp Def", "warehouse_name": "Wh Comp Def", "company": "Target Co", "is_group": 0},
            {"name": "Wh Stock Def", "warehouse_name": "Wh Stock Def", "company": "Target Co", "is_group": 0},
            {"name": "Stores - Target", "warehouse_name": "Stores - Target", "company": "Target Co", "is_group": 0},
            {"name": "Leaf WH", "warehouse_name": "Leaf WH", "company": "Target Co", "is_group": 0},
        ]

        # 1. Company default preferred
        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": warehouses_data}
        c_resp1 = MagicMock(is_success=True, status_code=200)
        c_resp1.json.return_value = {"data": {"default_warehouse": "Wh Comp Def"}}
        mock_client.get.side_effect = [w_resp, c_resp1]
        wh1 = get_default_warehouse("Target Co", reload=True)
        self.assertEqual(wh1, "Wh Comp Def")

        # 2. Stock settings preferred when Company default not available
        c_resp2 = MagicMock(is_success=True, status_code=200)
        c_resp2.json.return_value = {"data": {"default_warehouse": None}}
        s_resp2 = MagicMock(is_success=True, status_code=200)
        s_resp2.json.return_value = {"data": {"default_warehouse": "Wh Stock Def"}}
        mock_client.get.side_effect = [w_resp, c_resp2, s_resp2]
        wh2 = get_default_warehouse("Target Co", reload=True)
        self.assertEqual(wh2, "Wh Stock Def")

        # 3. Stores heuristic when both Company and Stock defaults missing
        c_resp3 = MagicMock(is_success=False, status_code=404)
        s_resp3 = MagicMock(is_success=False, status_code=404)
        mock_client.get.side_effect = [w_resp, c_resp3, s_resp3]
        wh3 = get_default_warehouse("Target Co", reload=True)
        self.assertEqual(wh3, "Stores - Target")

        # 4. First leaf warehouse when stores heuristic doesn't match
        no_stores_data = [
            {"name": "Group WH", "warehouse_name": "Group WH", "company": "Target Co", "is_group": 1},
            {"name": "Leaf WH", "warehouse_name": "Leaf WH", "company": "Target Co", "is_group": 0},
        ]
        w_resp4 = MagicMock(is_success=True, status_code=200)
        w_resp4.json.return_value = {"data": no_stores_data}
        mock_client.get.side_effect = [w_resp4, c_resp3, s_resp3]
        wh4 = get_default_warehouse("Target Co", reload=True)
        self.assertEqual(wh4, "Leaf WH")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_warehouse_company_fetch_http_error_falls_through(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": [{"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}]}
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": {}}

        mock_client.get.side_effect = [w_resp, httpx.ConnectError("timeout"), s_resp]

        with self.assertLogs("generic_tools", level="WARNING") as log:
            wh = get_default_warehouse("TC")
            self.assertEqual(wh, "Stores - TC")
            self.assertTrue(any("Failed to fetch default warehouse from Company" in msg for msg in log.output))

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_warehouse_stock_settings_403_falls_through(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": [{"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}]}
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": {}}
        s_resp = MagicMock(is_success=False, status_code=403)

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        with self.assertLogs("generic_tools", level="WARNING") as log:
            wh = get_default_warehouse("TC")
            self.assertEqual(wh, "Stores - TC")
            self.assertTrue(any("Failed to fetch Stock Settings from ERPNext (403)" in msg for msg in log.output))

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_default_warehouse_data_null_does_not_raise(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        w_resp = MagicMock(is_success=True, status_code=200)
        w_resp.json.return_value = {"data": [{"name": "Stores - TC", "warehouse_name": "Stores - TC", "company": "TC", "is_group": 0}]}
        c_resp = MagicMock(is_success=True, status_code=200)
        c_resp.json.return_value = {"data": None}
        s_resp = MagicMock(is_success=True, status_code=200)
        s_resp.json.return_value = {"data": None}

        mock_client.get.side_effect = [w_resp, c_resp, s_resp]

        wh = get_default_warehouse("TC")
        self.assertEqual(wh, "Stores - TC")

    # -------------------------------------------------------------------------
    # create_document default resolution tests
    # -------------------------------------------------------------------------
    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_default_company")
    def test_create_document_resolver_runtime_error_returns_system_error_and_skips_post(self, mock_comp, mock_client_cls):
        mock_comp.side_effect = RuntimeError("Default company is not configured.")
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        res = create_document("Sales Order", {"items": [{"item_code": "SKU001", "qty": 1}]})
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "system_error")
        self.assertIn("Default company is not configured", res["error"])
        mock_client.post.assert_not_called()

    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_default_warehouse")
    @patch("agent.tools.generic_tools.get_default_company", return_value="Metal Forms")
    def test_create_document_all_items_have_warehouse_does_not_call_get_default_warehouse(self, mock_comp, mock_wh, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=201)
        mock_resp.json.return_value = {"data": {"name": "SO-001"}}
        mock_client.post.return_value = mock_resp

        payload = {
            "company": "Metal Forms",
            "items": [{"item_code": "SKU001", "warehouse": "Explicit WH"}]
        }
        res = create_document("Sales Order", payload)
        self.assertTrue(res["success"])
        mock_wh.assert_not_called()

    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_default_warehouse", return_value="Resolved Default WH")
    @patch("agent.tools.generic_tools.get_default_company", return_value="Metal Forms")
    def test_create_document_some_items_lack_warehouse_calls_and_applies_only_to_missing(self, mock_comp, mock_wh, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=201)
        mock_resp.json.return_value = {"data": {"name": "SO-001"}}
        mock_client.post.return_value = mock_resp

        payload = {
            "company": "Metal Forms",
            "items": [
                {"item_code": "SKU001", "warehouse": "Explicit WH"},
                {"item_code": "SKU002"}
            ]
        }
        res = create_document("Sales Order", payload)
        self.assertTrue(res["success"])
        mock_wh.assert_called_once_with("Metal Forms")
        posted = mock_client.post.call_args[1]["json"]
        self.assertEqual(posted["items"][0]["warehouse"], "Explicit WH")
        self.assertEqual(posted["items"][1]["warehouse"], "Resolved Default WH")

    # ---------------------------------------------------------
    # Characterization tests for read and workflow tools
    # (get_document, search_document, get_count, aggregate,
    #  cancel_document, submit_document)
    # ---------------------------------------------------------

    def _assert_envelope(self, res: dict, expected_status: str):
        self.assertEqual(set(res.keys()), {"status", "data", "error", "has_more", "success"})
        self.assertEqual(res["status"], expected_status)
        self.assertEqual(res["success"], expected_status in ("success", "empty"))

    # get_document tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_document_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "customer": "Acme Corp"}}
        mock_client.get.return_value = mock_resp

        res = get_document("Sales Order", "SO-001")
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["name"], "SO-001")
        self.assertFalse(res["has_more"])
        self.assertIsNone(res["error"])

    def test_get_document_whitelist_rejection(self):
        res = get_document("User", "admin")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Document not found"}
        mock_client.get.return_value = mock_resp

        res = get_document("Sales Order", "SO-NONEXISTENT")
        self._assert_envelope(res, "not_found")
        self.assertIn("not found", res["error"].lower())

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_document_permission_denied_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Forbidden access"}
        mock_client.get.return_value = mock_resp

        res = get_document("Sales Order", "SO-001")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("Forbidden", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_document_system_error_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "Internal Server Error"}
        mock_client.get.return_value = mock_resp

        res = get_document("Sales Order", "SO-001")
        self._assert_envelope(res, "system_error")
        self.assertIn("Internal Server Error", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_document_transport_exception(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("Failed to connect")

        res = get_document("Sales Order", "SO-001")
        self._assert_envelope(res, "system_error")
        self.assertIn("Failed to connect", res["error"])

    # search_document tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_search_document_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {
            "data": [{"name": "CUST-001", "customer_name": "West View Store", "customer_group": "Commercial", "territory": "All Territories"}]
        }
        mock_client.get.return_value = mock_resp

        res = search_document("Customer", "West")
        self._assert_envelope(res, "success")
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["customer_name"], "West View Store")
        # Verify filters and fields mapping
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params["filters"], '[["customer_name", "like", "%West%"]]')
        self.assertEqual(params["fields"], '["name", "customer_name", "customer_group", "territory"]')

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_search_document_empty(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": []}
        mock_client.get.return_value = mock_resp

        res = search_document("Customer", "Nonexistent")
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], [])
        self.assertTrue(res["success"])

    def test_search_document_whitelist_rejection(self):
        res = search_document("User", "admin")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_search_document_system_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "Database query failed"}
        mock_client.get.return_value = mock_resp

        res = search_document("Customer", "West")
        self._assert_envelope(res, "system_error")
        self.assertIn("Database query failed", res["error"])

    # get_count tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_count_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": [{"name": "SO-001"}, {"name": "SO-002"}, {"name": "SO-003"}]}
        mock_client.get.return_value = mock_resp

        res = get_count("Sales Order")
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"], 3)
        self.assertTrue(res["success"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_count_empty(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": []}
        mock_client.get.return_value = mock_resp

        res = get_count("Sales Order", filters={"status": "Draft"})
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], 0)
        self.assertTrue(res["success"])

    def test_get_count_whitelist_rejection(self):
        res = get_count("User")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_count_error_propagation(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "Server error counting"}
        mock_client.get.return_value = mock_resp

        res = get_count("Sales Order")
        self._assert_envelope(res, "system_error")
        self.assertFalse(res["success"])

    # aggregate tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_aggregate_success_sum_and_sort(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {
            "data": [
                {"name": "SO-001", "customer": "Acme", "grand_total": 100.0},
                {"name": "SO-002", "customer": "Acme", "grand_total": 50.0},
                {"name": "SO-003", "customer": "Beta", "grand_total": 200.0},
            ]
        }
        mock_client.get.return_value = mock_resp

        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum", sort="grand_total desc", limit=2)
        self._assert_envelope(res, "success")
        self.assertEqual(len(res["data"]), 2)
        self.assertEqual(res["data"][0], {"customer": "Beta", "grand_total": 200.0})
        self.assertEqual(res["data"][1], {"customer": "Acme", "grand_total": 150.0})

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_aggregate_empty(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": []}
        mock_client.get.return_value = mock_resp

        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum")
        self._assert_envelope(res, "empty")
        self.assertEqual(res["data"], [])
        self.assertTrue(res["success"])

    def test_aggregate_whitelist_rejection(self):
        res = aggregate("User", group_by="role", metric="id", aggregation_fn="count")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_aggregate_error_propagation(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Resource not found"}
        mock_client.get.return_value = mock_resp

        res = aggregate("Sales Order", group_by="customer", metric="grand_total", aggregation_fn="sum")
        self._assert_envelope(res, "not_found")
        self.assertFalse(res["success"])

    # cancel_document tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_cancel_document_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "docstatus": 2}}
        mock_client.put.return_value = mock_resp

        res = cancel_document("Sales Order", "SO-001")
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["docstatus"], 2)
        # Verify PUT body has docstatus = 2
        _, kwargs = mock_client.put.call_args
        self.assertEqual(kwargs.get("json"), {"docstatus": 2})

    def test_cancel_document_whitelist_rejection(self):
        res = cancel_document("User", "admin")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_cancel_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Document not found"}
        mock_client.put.return_value = mock_resp

        res = cancel_document("Sales Order", "SO-NONEXISTENT")
        self._assert_envelope(res, "not_found")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_cancel_document_permission_denied(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Not permitted to cancel"}
        mock_client.put.return_value = mock_resp

        res = cancel_document("Sales Order", "SO-001")
        self._assert_envelope(res, "permission_denied")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_cancel_document_system_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "ERPNext cancel failed"}
        mock_client.put.return_value = mock_resp

        res = cancel_document("Sales Order", "SO-001")
        self._assert_envelope(res, "system_error")

    # submit_document tests
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_submit_document_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "docstatus": 1}}
        mock_client.put.return_value = mock_resp

        res = submit_document("Sales Order", "SO-001")
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"]["docstatus"], 1)
        # Verify PUT body has docstatus = 1
        _, kwargs = mock_client.put.call_args
        self.assertEqual(kwargs.get("json"), {"docstatus": 1})

    def test_submit_document_whitelist_rejection(self):
        res = submit_document("User", "admin")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_submit_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Document not found"}
        mock_client.put.return_value = mock_resp

        res = submit_document("Sales Order", "SO-NONEXISTENT")
        self._assert_envelope(res, "not_found")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_submit_document_permission_denied(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Not permitted to submit"}
        mock_client.put.return_value = mock_resp

        res = submit_document("Sales Order", "SO-001")
        self._assert_envelope(res, "permission_denied")

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_submit_document_system_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "ERPNext submit failed"}
        mock_client.put.return_value = mock_resp

        res = submit_document("Sales Order", "SO-001")
        self._assert_envelope(res, "system_error")

    # ---------------------------------------------------------
    # Characterization tests Part 0b: write tools, delivery_date sync,
    # and get_count limit pinning
    # ---------------------------------------------------------

    # update_document error cases
    def test_update_document_whitelist_rejection(self):
        res = update_document("User", "admin", {"email": "test@example.com"})
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_update_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Document not found"}
        mock_client.put.return_value = mock_resp

        res = update_document("Customer", "NONEXISTENT", {"customer_name": "New"})
        self._assert_envelope(res, "not_found")
        self.assertIn("not found", res["error"].lower())

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_update_document_permission_denied_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Forbidden update"}
        mock_client.put.return_value = mock_resp

        res = update_document("Customer", "CUST-001", {"customer_name": "New"})
        self._assert_envelope(res, "permission_denied")
        self.assertIn("Forbidden", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_update_document_system_error_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "Server update failure"}
        mock_client.put.return_value = mock_resp

        res = update_document("Customer", "CUST-001", {"customer_name": "New"})
        self._assert_envelope(res, "system_error")
        self.assertIn("Server update failure", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_update_document_transport_exception(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.put.side_effect = httpx.ConnectError("Connection refused")

        res = update_document("Customer", "CUST-001", {"customer_name": "New"})
        self._assert_envelope(res, "system_error")
        self.assertIn("Connection refused", res["error"])

    # delete_document error cases
    def test_delete_document_whitelist_rejection(self):
        res = delete_document("User", "admin")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("not permitted by whitelist", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_delete_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Document not found"}
        mock_client.delete.return_value = mock_resp

        res = delete_document("Customer", "NONEXISTENT")
        self._assert_envelope(res, "not_found")
        self.assertIn("not found", res["error"].lower())

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_delete_document_permission_denied_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Forbidden deletion"}
        mock_client.delete.return_value = mock_resp

        res = delete_document("Customer", "CUST-001")
        self._assert_envelope(res, "permission_denied")
        self.assertIn("Forbidden", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_delete_document_system_error_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=500)
        mock_resp.json.return_value = {"message": "Server delete failure"}
        mock_client.delete.return_value = mock_resp

        res = delete_document("Customer", "CUST-001")
        self._assert_envelope(res, "system_error")
        self.assertIn("Server delete failure", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_delete_document_transport_exception(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.delete.side_effect = httpx.ConnectError("Connection reset")

        res = delete_document("Customer", "CUST-001")
        self._assert_envelope(res, "system_error")
        self.assertIn("Connection reset", res["error"])

    # create_document additional failure cases (403, 404, transport exception)
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_permission_denied_http(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=403)
        mock_resp.json.return_value = {"message": "Forbidden creation"}
        mock_client.post.return_value = mock_resp

        res = create_document("Customer", {"customer_name": "New Co"})
        self._assert_envelope(res, "permission_denied")
        self.assertIn("Forbidden", res["error"])

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_not_found(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=False, status_code=404)
        mock_resp.json.return_value = {"message": "Endpoint not found"}
        mock_client.post.return_value = mock_resp

        res = create_document("Customer", {"customer_name": "New Co"})
        self._assert_envelope(res, "not_found")
        self.assertIn("not found", res["error"].lower())

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_transport_exception(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("Connection timed out")

        res = create_document("Customer", {"customer_name": "New Co"})
        self._assert_envelope(res, "system_error")
        self.assertIn("Connection timed out", res["error"])

    # Sales Order delivery_date synchronization in update_document (pinning CURRENT behavior)
    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_document")
    def test_update_sales_order_delivery_date_syncs_child_items_when_get_doc_succeeds(self, mock_get_doc, mock_client_cls):
        """When delivery_date is updated and get_document succeeds, child items receive updated delivery_date."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "delivery_date": "2026-11-01"}}
        mock_client.put.return_value = mock_resp

        mock_get_doc.return_value = {
            "status": "success",
            "data": {
                "name": "SO-001",
                "items": [
                    {"name": "row-1", "item_code": "ITEM-1", "delivery_date": "2026-10-01"},
                    {"name": "row-2", "item_code": "ITEM-2", "delivery_date": "2026-10-01"}
                ]
            }
        }

        res = update_document("Sales Order", "SO-001", {"delivery_date": "2026-11-01"})
        self._assert_envelope(res, "success")
        _, kwargs = mock_client.put.call_args
        put_payload = kwargs.get("json", {})
        self.assertIn("items", put_payload)
        self.assertEqual(len(put_payload["items"]), 2)
        for row in put_payload["items"]:
            self.assertEqual(row["delivery_date"], "2026-11-01")

    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_document")
    def test_update_sales_order_delivery_date_get_doc_failure_silent_fallthrough_known_behavior(self, mock_get_doc, mock_client_cls):
        """Documents KNOWN silent fall-through: if get_document returns not_found or system_error, PUT proceeds WITHOUT items."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "delivery_date": "2026-11-01"}}
        mock_client.put.return_value = mock_resp

        mock_get_doc.return_value = {"status": "not_found", "error": "Sales Order SO-001 not found"}

        res = update_document("Sales Order", "SO-001", {"delivery_date": "2026-11-01"})
        self._assert_envelope(res, "success")
        _, kwargs = mock_client.put.call_args
        put_payload = kwargs.get("json", {})
        # Note: items is NOT in payload because the non-success status was silently ignored
        self.assertNotIn("items", put_payload)
        self.assertEqual(put_payload["delivery_date"], "2026-11-01")

    @patch("agent.tools.generic_tools.httpx.Client")
    @patch("agent.tools.generic_tools.get_document")
    def test_update_sales_order_delivery_date_get_doc_raises_silent_fallthrough_and_logs_warning(self, mock_get_doc, mock_client_cls):
        """Documents KNOWN silent fall-through: if get_document raises an exception, warning is logged and PUT proceeds WITHOUT items."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": {"name": "SO-001", "delivery_date": "2026-11-01"}}
        mock_client.put.return_value = mock_resp

        mock_get_doc.side_effect = RuntimeError("Simulated timeout fetching Sales Order")

        with self.assertLogs("generic_tools", level="WARNING") as cm:
            res = update_document("Sales Order", "SO-001", {"delivery_date": "2026-11-01"})

        self._assert_envelope(res, "success")
        self.assertTrue(any("Failed to synchronize items delivery_date" in msg for msg in cm.output))
        _, kwargs = mock_client.put.call_args
        put_payload = kwargs.get("json", {})
        self.assertNotIn("items", put_payload)

    # get_count limit pinning
    @patch("agent.tools.generic_tools.httpx.Client")
    def test_get_count_pins_limit_and_page_length_param(self, mock_client_cls):
        """Pins that get_count passes limit=999999 to get_list, which requests limit_page_length=1000000."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": [{"name": f"SO-{i}"} for i in range(5)]}
        mock_client.get.return_value = mock_resp

        res = get_count("Sales Order", filters={"status": "Draft"})
        self._assert_envelope(res, "success")
        self.assertEqual(res["data"], 5)
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        self.assertEqual(params["limit_page_length"], "1000000")
        self.assertEqual(params["fields"], '["name"]')
        self.assertEqual(params["filters"], '{"status": "Draft"}')

    # -------------------------------------------------------------
    # Missing credentials and resolver caching tests
    # -------------------------------------------------------------
    def test_missing_credentials_read_returns_system_error(self):
        """Missing credentials on read operations return system_error envelope with original message."""
        with patch.dict(os.environ, {"ERPNEXT_API_KEY": "", "ERPNEXT_API_SECRET": ""}):
            res = get_document("Customer", "Cust-1")
            self._assert_envelope(res, "system_error")
            self.assertIn("ERPNext credentials not configured", res["error"])

    def test_missing_credentials_write_returns_system_error(self):
        """Missing credentials on write operations return system_error envelope with original message."""
        with patch.dict(os.environ, {"ERPNEXT_API_KEY": "", "ERPNEXT_API_SECRET": ""}):
            res = create_document("Customer", {"customer_name": "Acme"})
            self._assert_envelope(res, "system_error")
            self.assertIn("ERPNext credentials not configured", res["error"])

    def test_missing_credentials_resolver_raises_runtime_error(self):
        """Missing credentials in resolver raises RuntimeError."""
        with patch.dict(os.environ, {"ERPNEXT_API_KEY": "", "ERPNEXT_API_SECRET": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                get_default_company()
            self.assertIn("ERPNext credentials not configured", str(ctx.exception))

    @patch("mcp_erpnext.client.httpx.Client")
    def test_resolver_cache_shared_between_tools_and_agent_tools(self, mock_client_cls):
        """Resolver cache is shared between tools.generic_tools and agent.tools.generic_tools."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": [{"name": "Shared Company"}]}
        mock_client.get.return_value = mock_resp

        import tools.generic_tools as t_gt
        import agent.tools.generic_tools as a_gt

        c1 = t_gt.get_default_company()
        self.assertEqual(c1, "Shared Company")
        self.assertEqual(mock_client.get.call_count, 1)

        c2 = a_gt.get_default_company()
        self.assertEqual(c2, "Shared Company")
        self.assertEqual(mock_client.get.call_count, 1)

    @patch("mcp_erpnext.client.httpx.Client")
    def test_resolver_cache_distinct_credentials_never_share(self, mock_client_cls):
        """Two different credentials produce different cache keys and never share entries."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_resp1 = MagicMock(is_success=True, status_code=200)
        mock_resp1.json.return_value = {"data": [{"name": "Company User1"}]}

        mock_resp2 = MagicMock(is_success=True, status_code=200)
        mock_resp2.json.return_value = {"data": [{"name": "Company User2"}]}

        mock_client.get.side_effect = [mock_resp1, mock_resp2]

        with patch.dict(os.environ, {"ERPNEXT_API_KEY": "user1_key", "ERPNEXT_API_SECRET": "user1_sec"}):
            c1 = get_default_company()
            self.assertEqual(c1, "Company User1")
            self.assertEqual(mock_client.get.call_count, 1)

        with patch.dict(os.environ, {"ERPNEXT_API_KEY": "user2_key", "ERPNEXT_API_SECRET": "user2_sec"}):
            c2 = get_default_company()
            self.assertEqual(c2, "Company User2")
            self.assertEqual(mock_client.get.call_count, 2)

    @patch("mcp_erpnext.client.httpx.Client")
    def test_reset_resolver_caches_clears_cache(self, mock_client_cls):
        """reset_resolver_caches() clears all cached entries so next lookup re-fetches."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock(is_success=True, status_code=200)
        mock_resp.json.return_value = {"data": [{"name": "Company A"}]}
        mock_client.get.return_value = mock_resp

        c1 = get_default_company()
        self.assertEqual(c1, "Company A")
        self.assertEqual(mock_client.get.call_count, 1)

        # Call again without reset: hits cache
        c2 = get_default_company()
        self.assertEqual(c2, "Company A")
        self.assertEqual(mock_client.get.call_count, 1)

        # Reset caches
        reset_resolver_caches()

        # Call after reset: triggers new HTTP GET
        c3 = get_default_company()
        self.assertEqual(c3, "Company A")
        self.assertEqual(mock_client.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
