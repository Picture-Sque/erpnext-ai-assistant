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
            generic_tools.add_doctype,
            generic_tools.list_doctype,
            generic_tools.update_doctype,
            generic_tools.delete_doctype,
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
        with patch("agent.workflow.graph.list_doctype") as mock_list:
            mock_list.return_value = {"success": True, "data": []}
            res = call_generic_tool_node(state_offline)
            
            mock_list.assert_called_once()
            args, kwargs = mock_list.call_args
            called_doctype, called_params = args
            self.assertEqual(called_doctype, "Customer")
            filters = called_params.get("filters", [])
            self.assertEqual(filters, [["customer_name", "like", "%Nimbus Traders%"]])


if __name__ == "__main__":
    unittest.main()
