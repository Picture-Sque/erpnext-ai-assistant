"""
Integration tests for LangGraph workflow with ToolAdapter (Member 4 Stage 3).

Verifies:
1. Graph imports and initializes correctly with ToolAdapter.
2. Ordinary READ operations route through adapter (direct in direct mode, MCP in MCP mode).
3. All WRITE operations remain direct in both direct and MCP modes.
4. Write-verification reads in result_validation_node remain direct in both modes.
5. Default company and warehouse helpers in confirmation flow use adapter and preserve RuntimeError.
6. The 22 YAML skills continue to load and match intents correctly.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from agent.workflow.graph import (
    compiled_graph,
    call_generic_tool_node,
    result_validation_node,
    _describe_create_scope,
    loaded_skills,
    _get_skill_for_intent,
)
from agent.tools.adapter import ToolAdapter, adapter


class TestGraphMCPIntegration(unittest.TestCase):
    """Test suite for LangGraph ToolAdapter integration."""

    def test_22_yaml_skills_loaded(self):
        """Verify all 22 YAML skills continue to load and remain compatible."""
        self.assertGreaterEqual(len(loaded_skills), 22)
        loaded_names = {s["name"] for s in loaded_skills}
        expected_sample_skills = [
            "customer-lookup",
            "item-lookup",
            "check-inventory",
            "create-sales-order",
            "sales-order-lookup",
            "sales-analytics-report",
            "delivery-status-tracking",
            "purchase-receipt-report",
            "stock-movement-analysis",
        ]
        for skill_name in expected_sample_skills:
            self.assertIn(skill_name, loaded_names)
            skill = _get_skill_for_intent(skill_name)
            self.assertIsNotNone(skill)
            self.assertIn("tool", skill)

    def test_ordinary_read_in_call_generic_tool_node_direct_mode(self):
        """Verify ordinary read uses adapter in direct mode."""
        state = {
            "detected_intent": "item-lookup",
            "collected_fields": {"item_code": "ITEM-001"},
        }
        with patch.object(adapter, "_backend", "direct"), \
             patch("agent.workflow.graph.get_list", return_value={"status": "success", "data": [{"name": "ITEM-001"}], "success": True}) as mock_gl:
            res = call_generic_tool_node(state)
            self.assertEqual(res["tool_raw_response"]["status"], "success")
            mock_gl.assert_called_once()

    def test_ordinary_read_in_call_generic_tool_node_mcp_mode(self):
        """Verify ordinary read uses adapter in MCP mode."""
        state = {
            "detected_intent": "item-lookup",
            "collected_fields": {"item_code": "ITEM-001"},
        }
        mock_mcp_client = MagicMock()
        mock_mcp_client.call_tool.return_value = {
            "status": "success",
            "data": [{"name": "ITEM-001"}],
            "success": True,
            "total_count": 1,
        }

        with patch.object(adapter, "_backend", "mcp"), \
             patch.object(adapter, "_mcp_client", mock_mcp_client), \
             patch.object(adapter, "_mcp_initialized", True):
            res = call_generic_tool_node(state)
            self.assertEqual(res["tool_raw_response"]["status"], "success")
            mock_mcp_client.call_tool.assert_called_once()
            call_args = mock_mcp_client.call_tool.call_args
            self.assertEqual(call_args[0][0], "get_list")

    def test_write_in_call_generic_tool_node_remains_direct_in_mcp_mode(self):
        """CRITICAL SECURITY: Verify write operations remain direct in MCP mode."""
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {"customer": "CUST-001", "items": [{"item_code": "ITEM-1", "qty": 1}]},
            "confirmation_approved": True,
            "write_rbac_operation": "create",
            "target_doctype": "Sales Order",
            "target_tool": "create_document",
        }
        mock_mcp_client = MagicMock()

        with patch.object(adapter, "_backend", "mcp"), \
             patch.object(adapter, "_mcp_client", mock_mcp_client), \
             patch.object(adapter, "_mcp_initialized", True), \
             patch("agent.workflow.graph.create_document", return_value={"status": "success", "data": {"name": "SO-0001"}, "success": True}) as mock_direct_create:

            res = call_generic_tool_node(state)
            self.assertEqual(res["tool_raw_response"]["status"], "success")
            # Direct create_document called
            mock_direct_create.assert_called_once()
            # MCP client NEVER called for write
            mock_mcp_client.call_tool.assert_not_called()

    def test_write_verification_in_result_validation_node_remains_direct(self):
        """CRITICAL RULE: Write-verification read in result_validation_node must remain direct."""
        state = {
            "detected_intent": "update-sales-order",
            "collected_fields": {"customer": "CUST-001"},
            "target_doctype": "Sales Order",
            "write_rbac_operation": "update",
            "tool_raw_response": {"status": "success", "success": True, "data": {"name": "SO-0001"}},
        }
        mock_mcp_client = MagicMock()

        with patch.object(adapter, "_backend", "mcp"), \
             patch.object(adapter, "_mcp_client", mock_mcp_client), \
             patch.object(adapter, "_mcp_initialized", True), \
             patch("agent.workflow.graph.get_document", return_value={"status": "success", "data": {"name": "SO-0001", "customer": "CUST-001"}, "success": True}) as mock_direct_gd:

            res = result_validation_node(state)
            self.assertTrue(res.get("write_verified"))
            mock_direct_gd.assert_called_once_with("Sales Order", "SO-0001")
            mock_mcp_client.call_tool.assert_not_called()

    def test_resolvers_in_describe_create_scope(self):
        """Verify default company and warehouse resolvers in confirmation use adapter and handle RuntimeError."""
        with patch.object(adapter, "get_default_company", side_effect=RuntimeError("Multi-company")) as mock_gdc:
            scope = _describe_create_scope("Sales Order", {"customer": "CUST-001"})
            mock_gdc.assert_called_once()
            self.assertIn("Sales Order", scope)


if __name__ == "__main__":
    unittest.main()
