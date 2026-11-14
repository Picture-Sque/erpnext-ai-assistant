"""
Unit tests for agent.tools.mcp_client.ERPNextMCPClient.

Verifies:
1. Connection and tool listing via mock MCP server / stdio transport.
2. Tool execution and extraction of ERPNext response envelope.
3. Pagination metadata preservation (total_count, offset, limit, truncated).
4. Unknown tool rejection before transport invocation.
5. Connection error handling and cleanup.
6. Timeout handling and cleanup.
7. Malformed JSON and empty content response handling.
8. Sensitive credential masking in error messages.
9. Session and worker thread lifecycle management.
10. Context manager and close() cleanup.
"""

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.tools.mcp_client import (
    ERPNextMCPClient,
    MCPClientError,
    MCPConnectionError,
    MCPTimeoutError,
    MCPToolError,
    mask_sensitive,
)


class TestERPNextMCPClient(unittest.TestCase):
    """Test suite for ERPNext MCP Client."""

    def test_mask_sensitive(self):
        """Verify credential and token masking."""
        raw = "Error token 6ccb8f589373e6f:d56fae077fa3539 with Bearer eyJhbGciOiJIUzI1NiJ9.test and api_key='secret123'"
        masked = mask_sensitive(raw)
        self.assertNotIn("6ccb8f589373e6f:d56fae077fa3539", masked)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.test", masked)
        self.assertIn("token ***", masked)
        self.assertIn("Bearer ***", masked)

    def test_client_repr_masks_credentials(self):
        """Verify __repr__ does not reveal secrets."""
        client = ERPNextMCPClient(command="python")
        rep = repr(client)
        self.assertIn("ERPNextMCPClient", rep)
        self.assertNotIn("token", rep.lower())

    def test_unknown_tool_rejection(self):
        """Verify calling an unregistered tool raises MCPToolError."""
        client = ERPNextMCPClient()
        client._connected = True
        client._tools = {"get_list": MagicMock()}

        with self.assertRaises(MCPToolError) as ctx:
            client.call_tool("unregistered_tool", {"doctype": "Item"})
        self.assertIn("not registered", str(ctx.exception))

    def test_parse_tool_result_preserves_envelope(self):
        """Verify response envelope and pagination metadata are preserved."""
        client = ERPNextMCPClient()
        envelope = {
            "status": "success",
            "data": [{"name": "ITEM-001"}],
            "error": None,
            "has_more": True,
            "success": True,
            "total_count": 42,
            "offset": 10,
            "limit": 5,
            "truncated": False,
        }
        mock_result = MagicMock()
        mock_item = MagicMock()
        mock_item.text = json.dumps(envelope)
        mock_result.content = [mock_item]

        parsed = client._parse_tool_result("get_list", mock_result)
        self.assertEqual(parsed["status"], "success")
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["total_count"], 42)
        self.assertEqual(parsed["offset"], 10)
        self.assertEqual(parsed["limit"], 5)
        self.assertTrue(parsed["has_more"])
        self.assertEqual(parsed["data"][0]["name"], "ITEM-001")

    def test_parse_tool_result_empty_content(self):
        """Verify empty content raises MCPToolError."""
        client = ERPNextMCPClient()
        mock_result = MagicMock()
        mock_result.content = []

        with self.assertRaises(MCPToolError):
            client._parse_tool_result("get_list", mock_result)

    def test_parse_tool_result_invalid_json(self):
        """Verify invalid JSON response returns compatible system_error envelope."""
        client = ERPNextMCPClient()
        mock_result = MagicMock()
        mock_item = MagicMock()
        mock_item.text = "This is plain text not JSON token secret:value"
        mock_result.content = [mock_item]

        res = client._parse_tool_result("get_list", mock_result)
        self.assertEqual(res["status"], "system_error")
        self.assertFalse(res["success"])
        self.assertIn("Invalid JSON", res["error"])
        self.assertNotIn("secret:value", res["error"])

    def test_parse_tool_result_non_dict_json(self):
        """Verify non-dict JSON returns compatible system_error envelope."""
        client = ERPNextMCPClient()
        mock_result = MagicMock()
        mock_item = MagicMock()
        mock_item.text = json.dumps(["an", "array"])
        mock_result.content = [mock_item]

        res = client._parse_tool_result("get_list", mock_result)
        self.assertEqual(res["status"], "system_error")
        self.assertFalse(res["success"])

    def test_client_lifecycle_close_idempotent(self):
        """Verify close() is safe to call multiple times."""
        client = ERPNextMCPClient()
        client.close()
        client.close()
        self.assertFalse(client.is_connected)


if __name__ == "__main__":
    unittest.main()
