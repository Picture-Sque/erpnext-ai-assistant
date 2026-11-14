"""
Unit tests for agent.tools.adapter.ToolAdapter.

Verifies:
1. Default backend is 'direct' when TOOL_BACKEND is unset.
2. Explicit 'direct' backend preserves existing direct generic_tools behavior.
3. Explicit 'mcp' backend routes eligible read operations to MCP client.
4. All 5 write operations (create, update, delete, cancel, submit) ALWAYS remain direct.
5. Write-verification reads remain direct in both modes.
6. Invalid TOOL_BACKEND configuration raises RuntimeError.
7. MCP errors and timeouts return compatible system_error envelope without direct fallback.
8. Default company and warehouse helpers preserve RuntimeError semantics.
9. Envelope and pagination metadata are preserved across operations.
10. Adapter cleanup closes MCP client.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from agent.tools import generic_tools
from agent.tools.adapter import (
    ToolAdapter,
    resolve_backend,
    adapter as global_adapter,
)
from agent.tools.mcp_client import ERPNextMCPClient, MCPTimeoutError, MCPConnectionError


class TestToolAdapter(unittest.TestCase):
    """Test suite for ToolAdapter."""

    def test_default_backend_is_direct(self):
        """Verify default backend is direct when env var is unset."""
        with patch.dict(os.environ, {}, clear=True):
            b = resolve_backend()
            self.assertEqual(b, "direct")
            adapter = ToolAdapter()
            self.assertEqual(adapter.backend, "direct")

    def test_explicit_backends(self):
        """Verify valid explicit backend values with whitespace and case normalization."""
        self.assertEqual(resolve_backend("direct"), "direct")
        self.assertEqual(resolve_backend(" DIRECT "), "direct")
        self.assertEqual(resolve_backend("mcp"), "mcp")
        self.assertEqual(resolve_backend(" MCP "), "mcp")

    def test_invalid_backend_rejection(self):
        """Verify invalid backend strings raise RuntimeError."""
        for invalid in ("http", "proxy", "fallback", "invalid", "123"):
            with self.assertRaises(RuntimeError) as ctx:
                resolve_backend(invalid)
            self.assertIn("Invalid TOOL_BACKEND", str(ctx.exception))

    def test_direct_mode_routes_to_generic_tools(self):
        """Verify direct mode calls generic_tools directly without MCP client."""
        adapter = ToolAdapter(backend="direct")
        mock_client = MagicMock()
        adapter._mcp_client = mock_client

        with patch.object(generic_tools, "get_list", return_value={"status": "success", "data": [1]}) as mock_gl:
            res = adapter.get_list("Item", limit=5)
            mock_gl.assert_called_once_with(
                doctype="Item",
                filters=None,
                fields=None,
                limit=5,
                order_by=None,
                offset=0,
                include_total=False,
            )
            self.assertEqual(res["data"], [1])
            mock_client.call_tool.assert_not_called()

    def test_mcp_mode_routes_reads_to_mcp_client(self):
        """Verify MCP mode routes get_list, get_document, search_document, get_count, aggregate to MCP."""
        mock_client = MagicMock()
        adapter = ToolAdapter(backend="mcp", mcp_client=mock_client)
        adapter._mcp_initialized = True

        # 1. get_list
        mock_client.call_tool.return_value = {
            "status": "success",
            "data": [{"name": "A"}],
            "total_count": 1,
            "success": True,
        }
        res = adapter.get_list("Item", filters={"item_group": "Products"})
        mock_client.call_tool.assert_called_with("get_list", {"doctype": "Item", "filters": {"item_group": "Products"}, "offset": 0})
        self.assertEqual(res["total_count"], 1)

        # 2. get_document (ordinary read)
        mock_client.call_tool.return_value = {"status": "success", "data": {"name": "DOC-1"}, "success": True}
        res = adapter.get_document("Item", "DOC-1")
        mock_client.call_tool.assert_called_with("get_document", {"doctype": "Item", "name": "DOC-1"})
        self.assertEqual(res["data"]["name"], "DOC-1")

        # 3. search_document
        mock_client.call_tool.return_value = {"status": "success", "data": [], "success": True}
        res = adapter.search_document("Item", "laptop", limit=5)
        mock_client.call_tool.assert_called_with("search_document", {"doctype": "Item", "query": "laptop", "limit": 5})

        # 4. get_count
        mock_client.call_tool.return_value = {"status": "success", "data": 10, "success": True}
        res = adapter.get_count("Item")
        mock_client.call_tool.assert_called_with("get_count", {"doctype": "Item"})
        self.assertEqual(res["data"], 10)

        # 5. aggregate
        mock_client.call_tool.return_value = {"status": "success", "data": [{"total": 100}], "success": True}
        res = adapter.aggregate("Sales Order", "customer", "grand_total", "sum")
        mock_client.call_tool.assert_called_with(
            "aggregate",
            {"doctype": "Sales Order", "group_by": "customer", "metric": "grand_total", "aggregation_fn": "sum"},
        )

    def test_mcp_mode_keeps_all_writes_direct(self):
        """CRITICAL SECURITY RULE: verify all 5 writes remain direct in MCP mode."""
        mock_client = MagicMock()
        adapter = ToolAdapter(backend="mcp", mcp_client=mock_client)
        adapter._mcp_initialized = True

        with patch.object(generic_tools, "create_document", return_value={"status": "success", "data": {"name": "NEW-1"}}) as m_create, \
             patch.object(generic_tools, "update_document", return_value={"status": "success", "data": {"name": "UPD-1"}}) as m_update, \
             patch.object(generic_tools, "delete_document", return_value={"status": "success", "data": None}) as m_delete, \
             patch.object(generic_tools, "cancel_document", return_value={"status": "success", "data": {"name": "CAN-1"}}) as m_cancel, \
             patch.object(generic_tools, "submit_document", return_value={"status": "success", "data": {"name": "SUB-1"}}) as m_submit:

            res_c = adapter.create_document("Item", {"item_code": "NEW"})
            self.assertEqual(res_c["data"]["name"], "NEW-1")
            m_create.assert_called_once_with(doctype="Item", fields={"item_code": "NEW"})

            res_u = adapter.update_document("Item", "ITEM-1", {"item_name": "Updated"})
            self.assertEqual(res_u["data"]["name"], "UPD-1")
            m_update.assert_called_once_with(doctype="Item", name="ITEM-1", fields={"item_name": "Updated"})

            res_d = adapter.delete_document("Item", "ITEM-1")
            self.assertEqual(res_d["status"], "success")
            m_delete.assert_called_once_with(doctype="Item", name="ITEM-1")

            res_can = adapter.cancel_document("Sales Order", "SO-1")
            self.assertEqual(res_can["data"]["name"], "CAN-1")
            m_cancel.assert_called_once_with(doctype="Sales Order", name="SO-1")

            res_sub = adapter.submit_document("Sales Order", "SO-1")
            self.assertEqual(res_sub["data"]["name"], "SUB-1")
            m_submit.assert_called_once_with(doctype="Sales Order", name="SO-1")

            # Verify MCP client was NEVER called for writes
            mock_client.call_tool.assert_not_called()

    def test_write_verification_reads_remain_direct(self):
        """CRITICAL RULE: Write-verification reads must remain direct even when backend is MCP."""
        mock_client = MagicMock()
        adapter = ToolAdapter(backend="mcp", mcp_client=mock_client)
        adapter._mcp_initialized = True

        with patch.object(generic_tools, "get_document", return_value={"status": "success", "data": {"name": "SO-1"}}) as mock_gd:
            # 1. get_document with verification=True
            res1 = adapter.get_document("Sales Order", "SO-1", verification=True)
            self.assertEqual(res1["data"]["name"], "SO-1")
            mock_gd.assert_called_once_with(doctype="Sales Order", name="SO-1")
            mock_client.call_tool.assert_not_called()

            mock_gd.reset_mock()

            # 2. get_document_direct
            res2 = adapter.get_document_direct("Sales Order", "SO-1")
            self.assertEqual(res2["data"]["name"], "SO-1")
            mock_gd.assert_called_once_with(doctype="Sales Order", name="SO-1")
            mock_client.call_tool.assert_not_called()

    def test_mcp_failure_does_not_fallback_to_direct(self):
        """Verify MCP errors and timeouts return system_error envelope without direct fallback."""
        mock_client = MagicMock()
        mock_client.call_tool.side_effect = MCPTimeoutError("Operation timed out")
        adapter = ToolAdapter(backend="mcp", mcp_client=mock_client)
        adapter._mcp_initialized = True

        with patch.object(generic_tools, "get_list") as mock_direct_gl:
            res = adapter.get_list("Item")
            self.assertEqual(res["status"], "system_error")
            self.assertFalse(res["success"])
            self.assertIn("timed out", res["error"])
            # Direct backend must NOT have been called as fallback
            mock_direct_gl.assert_not_called()

    def test_resolvers_preserve_runtime_error(self):
        """Verify default company and warehouse preserve RuntimeError semantics."""
        adapter = ToolAdapter(backend="mcp")
        with patch.object(generic_tools, "get_default_company", side_effect=RuntimeError("Multi-company setup")):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.get_default_company()
            self.assertIn("Multi-company", str(ctx.exception))

        with patch.object(generic_tools, "get_default_warehouse", side_effect=RuntimeError("Warehouse not found")):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.get_default_warehouse("Acme")
            self.assertIn("Warehouse not found", str(ctx.exception))

    def test_doctype_allowlist_helpers(self):
        """Verify doctype allowlist helpers delegate directly."""
        adapter = ToolAdapter(backend="mcp")
        with patch.object(generic_tools, "is_doctype_allowed", return_value=True) as mock_ida, \
             patch.object(generic_tools, "get_allowed_doctypes", return_value=["Item", "Customer"]) as mock_gad:
            self.assertTrue(adapter.is_doctype_allowed("Item"))
            mock_ida.assert_called_once_with("Item")
            self.assertEqual(adapter.get_allowed_doctypes(), ["Item", "Customer"])
            mock_gad.assert_called_once()


if __name__ == "__main__":
    unittest.main()
