import os
import sys
import unittest
import inspect
from unittest.mock import MagicMock, patch

# Ensure agent and root directories are in path
agent_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if agent_path not in sys.path:
    sys.path.insert(0, agent_path)
if root_path not in sys.path:
    sys.path.insert(0, root_path)

# Import functions for testing
import agent.tools.generic_tools as generic_tools
import agent.workflow.graph as graph
from agent.audit_logger import init_audit_db
init_audit_db()
from agent.workflow.graph import (
    apply_skill_defaults,
    evaluate_validation_rules,
    collect_parameters_node,
    validate_parameters_node,
    call_generic_tool_node
)


class TestGenericPipelineGenericity(unittest.TestCase):

    def test_static_scan_for_hardcoded_literals(self):
        """
        Verify that none of the shared generic tools or pipeline execution nodes
        contain hardcoded doctype names or field name literals from the denylist.
        """
        denylist = [
            "Sales Order",
            "transaction_date",
            "delivery_date",
            "Customer",
            "Item",
            "Bin"
        ]

        # Functions to scan inside generic_tools.py
        tools_functions = [
            generic_tools.create_document,
            generic_tools.get_list,
            generic_tools.update_document,
            generic_tools.delete_document,
            generic_tools.is_doctype_allowed
        ]

        # Pipeline nodes to scan inside graph.py
        pipeline_nodes = [
            collect_parameters_node,
            validate_parameters_node,
            call_generic_tool_node
        ]

        # Scan each tool function body
        import re
        for func in tools_functions:
            source = inspect.getsource(func)
            for literal in denylist:
                pattern = r"\b" + re.escape(literal) + r"\b"
                match = re.search(pattern, source, re.IGNORECASE)
                self.assertIsNone(
                    match,
                    f"Found forbidden hardcoded literal '{literal}' in function '{func.__name__}' inside generic_tools.py!"
                )

        # Scan each graph pipeline node function body
        for node in pipeline_nodes:
            source = inspect.getsource(node)
            for literal in denylist:
                pattern = r"\b" + re.escape(literal) + r"\b"
                match = re.search(pattern, source, re.IGNORECASE)
                self.assertIsNone(
                    match,
                    f"Found forbidden hardcoded literal '{literal}' in graph node '{node.__name__}'!"
                )

    def test_schema_driven_defaults_with_fixture(self):
        """
        Verify the generic defaults calculation engine using a custom, non-Sales-Order test fixture.
        """
        from datetime import datetime, timedelta

        # Custom purchase order skill defaults
        defaults_fixture = [
            {"field": "order_date", "value": "today"},
            {"field": "expected_arrival", "value": "order_date + 5d"},
            {"field": "static_field", "value": "some-default-value"}
        ]

        fields = {}
        updated = apply_skill_defaults(fields, defaults_fixture)

        # 1. Verify static field value
        self.assertEqual(updated.get("static_field"), "some-default-value")

        # 2. Verify order_date defaults to today
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(updated.get("order_date"), today_str)

        # 3. Verify expected_arrival defaults to today + 5 days
        expected_str = (datetime.now().date() + timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertEqual(updated.get("expected_arrival"), expected_str)

    def test_schema_driven_validation_success_with_fixture(self):
        """
        Verify the generic validation engine returns success for valid fields.
        """
        rules_fixture = [
            {
                "field": "expected_arrival",
                "must_be_after": "order_date",
                "on_fail": "must be strictly after order_date"
            }
        ]

        collected = {
            "order_date": "2026-08-08",
            "expected_arrival": "2026-08-12"
        }

        all_ok, missing = evaluate_validation_rules(collected, rules_fixture)
        self.assertTrue(all_ok)
        self.assertEqual(len(missing), 0)
        self.assertIn("expected_arrival", collected)

    def test_schema_driven_validation_failure_with_fixture(self):
        """
        Verify the generic validation engine fails and clears the invalid field for an invalid pair.
        """
        rules_fixture = [
            {
                "field": "expected_arrival",
                "must_be_after": "order_date",
                "on_fail": "must be strictly after order_date"
            }
        ]

        # Case 1: same day (should fail, as it must be strictly after)
        collected1 = {
            "order_date": "2026-08-08",
            "expected_arrival": "2026-08-08"
        }
        all_ok1, missing1 = evaluate_validation_rules(collected1, rules_fixture)
        self.assertFalse(all_ok1)
        self.assertNotIn("expected_arrival", collected1)
        self.assertIn("expected_arrival (must be strictly after order_date)", missing1)

        # Case 2: past day (should fail)
        collected2 = {
            "order_date": "2026-08-08",
            "expected_arrival": "2026-08-05"
        }
        all_ok2, missing2 = evaluate_validation_rules(collected2, rules_fixture)
        self.assertFalse(all_ok2)
        self.assertNotIn("expected_arrival", collected2)
        self.assertIn("expected_arrival (must be strictly after order_date)", missing2)

    @patch("agent.workflow.graph.invoke_llm")
    def test_customer_lookup_empty_result_formatting(self, mock_invoke_llm):
        """
        Verify that the empty-result case for customer-lookup produces a "not found" style message
        when the LLM is online, and does not leak raw tool output if the LLM fails.
        """
        from agent.workflow.graph import format_agent_message_node
        
        # Scenario 1: LLM is online and synthesizes the response using SKILL instructions
        mock_invoke_llm.return_value = "Customer not found in database."
        state_online = {
            "detected_intent": "customer-lookup",
            "messages": [],
            "tool_raw_response": {
                "success": True,
                "data": []
            }
        }
        res_online = format_agent_message_node(state_online)
        self.assertIn("not found", res_online["final_response"].lower())
        self.assertNotIn("Data: []", res_online["final_response"])
        
        # Scenario 2: LLM is offline/rate-limited (fails)
        mock_invoke_llm.return_value = "Error: Rate limit reached."
        state_offline = {
            "detected_intent": "customer-lookup",
            "messages": [],
            "tool_raw_response": {
                "success": True,
                "data": []
            }
        }
        res_offline = format_agent_message_node(state_offline)
        # Verify it returns our clean formatting error fallback instead of f"Tool execution succeeded. Data: []"
        self.assertEqual(res_offline["final_response"], "I encountered an error trying to format the results. Please try again.")
        self.assertNotIn("Data:", res_offline["final_response"])

    @patch("agent.workflow.graph.invoke_structured_llm")
    def test_customer_lookup_wildcard_filter(self, mock_invoke_structured_llm):
        """
        Verify that customer-lookup skill produces a 'like' filter with wildcards.
        """
        from agent.workflow.graph import call_generic_tool_node
        
        # Scenario 1: LLM is offline, resolving via _offline_fallback_tool_resolver
        mock_invoke_structured_llm.return_value = None
        state_offline = {
            "detected_intent": "customer-lookup",
            "collected_fields": {
                "customer_name": "Nimbus Traders"
            }
        }
        with patch("agent.workflow.graph.get_list") as mock_list:
            mock_list.return_value = {"status": "success", "data": []}
            res = call_generic_tool_node(state_offline)
            
            mock_list.assert_called_once()
            args, kwargs = mock_list.call_args
            called_doctype = args[0]
            called_params = kwargs
            self.assertEqual(called_doctype, "Customer")
            filters = called_params.get("filters", [])
            self.assertEqual(filters, [["customer_name", "like", "%Nimbus Traders%"]])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage, HumanMessage

import agent.workflow.graph as graph
from agent.audit_logger import init_audit_db, get_audit_db_connection, AUDIT_DB_PATH

class TestAuditAndDestructiveSkills(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_audit_db()

    def setUp(self):
        import os
        if os.path.exists(AUDIT_DB_PATH):
            try:
                os.remove(AUDIT_DB_PATH)
            except Exception:
                pass
        init_audit_db()
        self.conn = get_audit_db_connection()
        self.conn.execute("DELETE FROM audit_log;")
        self.conn.commit()
        
    def tearDown(self):
        self.conn.close()

    def _base_state(self, intent, role="Sales Manager", msg="do it"):
        return {
            "messages": [HumanMessage(content=msg)],
            "detected_intent": intent,
            "user_roles": [role],
            "collected_fields": {"name": "SO-0001", "delivery_date": "2026-10-15"},
            "resolved_entities": {"name": {"name": "SO-0001", "match_type": "exact"}},
            "preconditions_validated": True,
            "target_doctype": "Sales Order",
            "write_rbac_operation": None
        }

    @patch("agent.workflow.graph.get_document")
    def test_cancel_sales_order_valid(self, mock_get):
        """(a) cancel-sales-order end-to-end on a valid draft SO."""
        state = self._base_state("cancel-sales-order")
        # Step 5
        res_rbac = graph.write_rbac_gate_node(state)
        self.assertTrue(res_rbac["write_rbac_passed"])
        state.update(res_rbac)
        
        # Step 6 Ask
        res_conf = graph.confirmation_node(state)
        self.assertIn("pending_confirmation", res_conf)
        state.update(res_conf)
        
        # Step 6 Reply
        state["messages"].append(AIMessage(content=res_conf["final_response"]))
        state["messages"].append(HumanMessage(content="yes"))
        res_conf_reply = graph.confirmation_node(state)
        self.assertEqual(res_conf_reply.get("confirmation_result"), "confirmed")
        state.update(res_conf_reply)
        
        # Step 7 (Simulate tool response)
        state["tool_raw_response"] = {"success": True, "data": {"name": "SO-0001"}}
        
        # Step 8
        mock_get.return_value = {"status": "success", "data": {"name": "SO-0001", "docstatus": 2}}
        res_verify = graph.result_validation_node(state)
        self.assertTrue(res_verify["write_verified"])

        # Check DB (g)
        cursor = self.conn.cursor()
        cursor.execute("SELECT operation, target_name, outcome FROM audit_log ORDER BY id")
        rows = cursor.fetchall()
        # Should have: pending, confirmed, executed
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["outcome"], "pending")
        self.assertEqual(rows[1]["outcome"], "confirmed")
        self.assertEqual(rows[2]["outcome"], "executed")

    @patch("agent.workflow.graph.get_document")
    def test_cancel_already_cancelled(self, mock_get):
        """(b) cancel attempt on an already-cancelled SO."""
        state = self._base_state("cancel-sales-order")
        state.update({"write_rbac_operation": "cancel", "write_rbac_passed": True, "target_doctype": "Sales Order"})
        mock_get.return_value = {"status": "success", "data": {"name": "SO-0001", "docstatus": 2}}
        
        res = graph.precondition_validation_node(state)
        self.assertFalse(res["preconditions_validated"])
        self.assertEqual(res["failure_classification"], "validation_error")

    @patch("agent.workflow.graph.get_document")
    def test_update_sales_order(self, mock_get):
        """(c) update-sales-order changing delivery_date — no confirmation asked."""
        state = self._base_state("update-sales-order")
        res_rbac = graph.write_rbac_gate_node(state)
        self.assertTrue(res_rbac["write_rbac_passed"])
        state.update(res_rbac)
        
        # Confirmation node should skip
        res_conf = graph.confirmation_node(state)
        self.assertEqual(res_conf, {})
        
        state["tool_raw_response"] = {"success": True, "data": {"name": "SO-0001"}}
        mock_get.return_value = {"status": "success", "data": {"name": "SO-0001", "delivery_date": "2026-10-15"}}
        res_verify = graph.result_validation_node(state)
        self.assertTrue(res_verify["write_verified"])
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT operation, outcome FROM audit_log ORDER BY id")
        rows = cursor.fetchall()
        # Only "executed" because no confirmation ask/reply
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "executed")

    @patch("agent.workflow.graph.get_document")
    def test_submit_sales_order_valid(self, mock_get):
        """(d) submit-sales-order with full pipeline including confirmation."""
        state = self._base_state("submit-sales-order")
        # Step 5: Write RBAC
        res_rbac = graph.write_rbac_gate_node(state)
        self.assertTrue(res_rbac["write_rbac_passed"])
        state.update(res_rbac)

        # Step 5.5: Precondition Validation (draft SO has docstatus 0)
        mock_get.return_value = {"status": "success", "data": {"name": "SO-0001", "docstatus": 0}}
        res_precond = graph.precondition_validation_node(state)
        self.assertTrue(res_precond["preconditions_validated"])
        state.update(res_precond)

        # Step 6: Confirmation Ask
        res_conf = graph.confirmation_node(state)
        self.assertIn("pending_confirmation", res_conf)
        self.assertEqual(res_conf["pending_confirmation"]["operation"], "submit")
        state.update(res_conf)

        # Step 6: Confirmation Reply (User says 'confirm')
        state["messages"].append(AIMessage(content=res_conf["final_response"]))
        state["messages"].append(HumanMessage(content="confirm"))
        res_conf_reply = graph.confirmation_node(state)
        self.assertEqual(res_conf_reply.get("confirmation_result"), "confirmed")
        state.update(res_conf_reply)

        # Step 7: Tool Execution
        state["tool_raw_response"] = {"success": True, "data": {"name": "SO-0001", "docstatus": 1}}

        # Step 8: Write Verification (submitted SO has docstatus 1)
        mock_get.return_value = {"status": "success", "data": {"name": "SO-0001", "docstatus": 1}}
        res_verify = graph.result_validation_node(state)
        self.assertTrue(res_verify["write_verified"])

        # Check DB (g)
        cursor = self.conn.cursor()
        cursor.execute("SELECT operation, target_name, outcome FROM audit_log ORDER BY id")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["operation"], "submit")
        self.assertEqual(rows[0]["outcome"], "pending")
        self.assertEqual(rows[1]["operation"], "submit")
        self.assertEqual(rows[1]["outcome"], "confirmed")
        self.assertEqual(rows[2]["operation"], "submit")
        self.assertEqual(rows[2]["outcome"], "executed")

    def test_submit_unauthorized(self):
        """(e) user without right role attempting submit — denied at Write RBAC."""
        state = self._base_state("submit-sales-order", role="Guest")
        res_rbac = graph.write_rbac_gate_node(state)
        self.assertFalse(res_rbac["write_rbac_passed"])
        self.assertEqual(res_rbac["failure_classification"], "permission_denied_write")
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT outcome FROM audit_log")
        row = cursor.fetchone()
        self.assertEqual(row["outcome"], "denied")

    @patch("agent.workflow.graph.invoke_structured_llm")
    def test_intent_classification(self, mock_llm):
        """(f) intent classification distinguishing all 3 new skills + create-sales-order from natural phrasing."""
        # 1. Natural phrasing matched by keywords without LLM:
        # cancel-sales-order keyword: 'cancel sales order'
        s1 = {"messages": [HumanMessage(content="please cancel sales order SO-0001")]}
        r1 = graph.classify_intent_node(s1)
        self.assertEqual(r1["detected_intent"], "cancel-sales-order")

        # submit-sales-order keyword: 'submit sales order'
        s2 = {"messages": [HumanMessage(content="submit sales order SO-0002 for approval")]}
        r2 = graph.classify_intent_node(s2)
        self.assertEqual(r2["detected_intent"], "submit-sales-order")

        # create-sales-order keyword: 'create sales order'
        s3 = {"messages": [HumanMessage(content="create sales order for customer Acme")]}
        r3 = graph.classify_intent_node(s3)
        self.assertEqual(r3["detected_intent"], "create-sales-order")

        # 2. Conversational/colloquial phrasing resolved via LLM fallback:
        # update-sales-order: "change delivery date"
        mock_llm.return_value = {"intent": "update-sales-order"}
        s4 = {"messages": [HumanMessage(content="change delivery to next friday for SO-0003")]}
        r4 = graph.classify_intent_node(s4)
        self.assertEqual(r4["detected_intent"], "update-sales-order")

    def test_audit_log_query_order_and_format(self):
        """(g) direct audit log query showing entries in correct order (pending -> confirmed -> executed)."""
        # Run cancel end-to-end to generate the full sequence
        self.test_cancel_sales_order_valid()
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, timestamp, operation, doctype, target_name, outcome, details FROM audit_log ORDER BY id ASC")
        rows = [dict(r) for r in cursor.fetchall()]
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0]["outcome"], "pending")
        self.assertEqual(rows[1]["outcome"], "confirmed")
        self.assertEqual(rows[2]["outcome"], "executed")

