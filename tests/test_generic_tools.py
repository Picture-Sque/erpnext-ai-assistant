import os
import sys
import unittest
import json
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
    is_doctype_allowed,
    get_allowed_doctypes,
    extract_erpnext_error
)


class TestGenericTools(unittest.TestCase):

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

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_success(self, mock_client_cls):
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

    @patch("agent.tools.generic_tools.httpx.Client")
    def test_create_document_validation_error(self, mock_client_cls):
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


if __name__ == "__main__":
    unittest.main()
