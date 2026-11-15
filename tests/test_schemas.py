"""
Unit tests for MCP tool input schemas and response envelopes in mcp_erpnext/schemas.py.

Verifies Pydantic v2 strict schema enforcement, boundary conditions, extra field rejection
(preventing identity injection), and envelope formats.
"""

import ast
import os
import unittest
from pydantic import ValidationError

from mcp_erpnext.schemas import (
    BaseToolModel,
    GetListInput,
    GetDocumentInput,
    SearchDocumentInput,
    GetCountInput,
    AggregateInput,
    RunReportInput,
    CreateDocumentInput,
    UpdateDocumentInput,
    DeleteDocumentInput,
    CancelDocumentInput,
    SubmitDocumentInput,
    READ_TOOL_SCHEMAS,
    WRITE_TOOL_SCHEMAS,
    TOOL_INPUT_SCHEMAS,
    StandardEnvelope,
    PaginatedEnvelope,
    get_tool_schema,
    validate_tool_arguments,
)


class TestGetListSchema(unittest.TestCase):
    """Test GetListInput validation rules."""

    def test_valid_minimal(self):
        m = GetListInput(doctype="CustomDoc")
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertIsNone(m.filters)
        self.assertIsNone(m.fields)
        self.assertIsNone(m.limit)
        self.assertEqual(m.offset, 0)
        self.assertFalse(m.include_total)

    def test_valid_filters_list_and_empty_list(self):
        """Verify GetListInput accepts empty list and list of filter expressions."""
        m_empty = GetListInput(doctype="CustomDoc", filters=[])
        self.assertEqual(m_empty.filters, [])

        m_list = GetListInput(doctype="CustomDoc", filters=[["status", "=", "Draft"]])
        self.assertEqual(m_list.filters, [["status", "=", "Draft"]])

    def test_valid_full(self):
        m = GetListInput(
            doctype="CustomDoc",
            filters={"status": "Open"},
            fields=["name", "status"],
            limit=50,
            order_by="creation desc",
            offset=10,
            include_total=True,
            group_by="status",
            parent_doctype="ParentDoc",
            or_filters=[["status", "=", "Pending"]],
        )
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertEqual(m.limit, 50)
        self.assertEqual(m.offset, 10)
        self.assertTrue(m.include_total)

    def test_missing_doctype(self):
        with self.assertRaises(ValidationError):
            GetListInput()

    def test_whitespace_doctype_rejected(self):
        with self.assertRaises(ValidationError):
            GetListInput(doctype="   ")

    def test_negative_offset_rejected(self):
        with self.assertRaises(ValidationError):
            GetListInput(doctype="CustomDoc", offset=-1)

    def test_limit_zero_rejected(self):
        with self.assertRaises(ValidationError):
            GetListInput(doctype="CustomDoc", limit=0)

    def test_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError) as ctx:
            GetListInput(doctype="CustomDoc", user_roles=["admin"])
        self.assertIn("extra_forbidden", str(ctx.exception))


class TestGetDocumentSchema(unittest.TestCase):
    """Test GetDocumentInput validation rules."""

    def test_valid(self):
        m = GetDocumentInput(doctype="CustomDoc", name="DOC-001")
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertEqual(m.name, "DOC-001")

    def test_missing_required_fields(self):
        with self.assertRaises(ValidationError):
            GetDocumentInput(doctype="CustomDoc")
        with self.assertRaises(ValidationError):
            GetDocumentInput(name="DOC-001")

    def test_whitespace_name_rejected(self):
        with self.assertRaises(ValidationError):
            GetDocumentInput(doctype="CustomDoc", name="  ")


class TestSearchDocumentSchema(unittest.TestCase):
    """Test SearchDocumentInput validation rules."""

    def test_valid_defaults(self):
        m = SearchDocumentInput(doctype="CustomDoc", query="Widget")
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertEqual(m.query, "Widget")
        self.assertEqual(m.limit, 10)

    def test_custom_limit(self):
        m = SearchDocumentInput(doctype="CustomDoc", query="Widget", limit=25)
        self.assertEqual(m.limit, 25)

    def test_invalid_limit(self):
        with self.assertRaises(ValidationError):
            SearchDocumentInput(doctype="CustomDoc", query="Widget", limit=0)

    def test_empty_query_rejected(self):
        with self.assertRaises(ValidationError):
            SearchDocumentInput(doctype="CustomDoc", query="")


class TestGetCountSchema(unittest.TestCase):
    """Test GetCountInput validation rules."""

    def test_valid(self):
        m = GetCountInput(doctype="CustomDoc", filters={"docstatus": 1})
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertEqual(m.filters, {"docstatus": 1})

    def test_missing_doctype(self):
        with self.assertRaises(ValidationError):
            GetCountInput()


class TestAggregateSchema(unittest.TestCase):
    """Test AggregateInput validation rules."""

    def test_valid(self):
        for fn in ("sum", "avg", "count", "max", "min"):
            m = AggregateInput(
                doctype="CustomDoc",
                group_by="customer_group",
                metric="grand_total",
                aggregation_fn=fn,
                sort="metric desc",
                limit=10,
            )
            self.assertEqual(m.aggregation_fn, fn)

    def test_invalid_aggregation_fn(self):
        with self.assertRaises(ValidationError):
            AggregateInput(
                doctype="CustomDoc",
                group_by="customer_group",
                metric="grand_total",
                aggregation_fn="stddev",
            )

    def test_negative_limit_rejected(self):
        with self.assertRaises(ValidationError):
            AggregateInput(
                doctype="CustomDoc",
                group_by="customer_group",
                metric="grand_total",
                aggregation_fn="sum",
                limit=-1,
            )

    def test_missing_required_fields(self):
        with self.assertRaises(ValidationError):
            AggregateInput(doctype="CustomDoc", group_by="status", metric="qty")


class TestRunReportSchema(unittest.TestCase):
    """Test RunReportInput validation rules."""

    def test_valid(self):
        m = RunReportInput(
            report_name="Stock Projected Qty",
            filters={"company": "Test Co"},
            limit=100,
            aggregation_fn="sum",
        )
        self.assertEqual(m.report_name, "Stock Projected Qty")
        self.assertEqual(m.limit, 100)

    def test_missing_report_name(self):
        with self.assertRaises(ValidationError):
            RunReportInput(filters={"company": "Test Co"})

    def test_rejection_of_injected_identity_fields(self):
        # Clients must never supply user_roles or client in tool arguments
        with self.assertRaises(ValidationError) as ctx:
            RunReportInput(report_name="Stock Projected Qty", user_roles=["Stock Manager"])
        self.assertIn("extra_forbidden", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            RunReportInput(report_name="Stock Projected Qty", client="mock_client")
        self.assertIn("extra_forbidden", str(ctx.exception))


class TestWriteToolSchemas(unittest.TestCase):
    """Test write tool input schemas (contracts for write operations)."""

    def test_create_document_valid(self):
        m = CreateDocumentInput(doctype="CustomDoc", fields={"field1": "val1"})
        self.assertEqual(m.doctype, "CustomDoc")
        self.assertEqual(m.fields, {"field1": "val1"})

    def test_update_document_valid(self):
        m = UpdateDocumentInput(doctype="CustomDoc", name="DOC-001", fields={"status": "Closed"})
        self.assertEqual(m.name, "DOC-001")

    def test_lifecycle_tools_valid(self):
        for cls in (DeleteDocumentInput, CancelDocumentInput, SubmitDocumentInput):
            m = cls(doctype="CustomDoc", name="DOC-001")
            self.assertEqual(m.doctype, "CustomDoc")
            self.assertEqual(m.name, "DOC-001")

    def test_lifecycle_tools_missing_name(self):
        for cls in (DeleteDocumentInput, CancelDocumentInput, SubmitDocumentInput):
            with self.assertRaises(ValidationError):
                cls(doctype="CustomDoc")


class TestRegistryAndHelpers(unittest.TestCase):
    """Test schema registries and lookup helpers."""

    def test_read_and_write_registries(self):
        self.assertEqual(len(READ_TOOL_SCHEMAS), 6)
        self.assertEqual(len(WRITE_TOOL_SCHEMAS), 5)
        self.assertEqual(len(TOOL_INPUT_SCHEMAS), 11)
        self.assertTrue(set(READ_TOOL_SCHEMAS.keys()).isdisjoint(WRITE_TOOL_SCHEMAS.keys()))

    def test_get_tool_schema(self):
        self.assertIs(get_tool_schema("get_list"), GetListInput)
        self.assertIs(get_tool_schema("run_report"), RunReportInput)
        self.assertIs(get_tool_schema("create_document"), CreateDocumentInput)
        self.assertIsNone(get_tool_schema("non_existent_tool"))

    def test_validate_tool_arguments_valid(self):
        result = validate_tool_arguments("get_document", {"doctype": "DocA", "name": "REC-1"})
        self.assertIsInstance(result, GetDocumentInput)
        self.assertEqual(result.name, "REC-1")

    def test_validate_tool_arguments_invalid_args(self):
        with self.assertRaises(ValidationError):
            validate_tool_arguments("get_document", {"doctype": "DocA"})

    def test_validate_tool_arguments_unknown_tool(self):
        with self.assertRaises(ValueError):
            validate_tool_arguments("unknown_tool", {})


class TestOutputEnvelopes(unittest.TestCase):
    """Test standard and paginated response envelopes."""

    def test_standard_envelope_valid(self):
        env = StandardEnvelope(
            status="success",
            data={"id": "1"},
            error=None,
            has_more=False,
            success=True,
        )
        self.assertEqual(env.status, "success")
        self.assertTrue(env.success)

    def test_paginated_envelope_valid(self):
        env = PaginatedEnvelope(
            status="success",
            data=[{"id": "1"}],
            error=None,
            has_more=True,
            success=True,
            total_count=100,
            offset=0,
            limit=10,
            truncated=False,
        )
        self.assertEqual(env.total_count, 100)
        self.assertTrue(env.has_more)

    def test_standard_envelope_invalid_status(self):
        with self.assertRaises(ValidationError):
            StandardEnvelope(
                status="invalid_status",
                success=True,
            )


class TestImportHygiene(unittest.TestCase):
    """Verify package isolation and no hardcoded business doctype literals."""

    def test_schemas_has_no_forbidden_imports(self):
        forbidden_roots = {"agent", "tools", "workflow", "ai_assistant", "main", "dotenv"}
        schemas_path = os.path.join(os.path.dirname(__file__), "..", "mcp_erpnext", "schemas.py")
        with open(schemas_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=schemas_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_pkg = alias.name.split(".")[0]
                    self.assertNotIn(top_pkg, forbidden_roots)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_pkg = node.module.split(".")[0]
                    self.assertNotIn(top_pkg, forbidden_roots)

    def test_no_hardcoded_doctype_literals_in_schemas(self):
        denylist = ["Sales Order", "Customer", "Item", "Bin", "Sales Invoice", "Purchase Order"]
        schemas_path = os.path.join(os.path.dirname(__file__), "..", "mcp_erpnext", "schemas.py")
        with open(schemas_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for denied in denylist:
                    self.assertNotEqual(
                        node.value.lower(),
                        denied.lower(),
                        f"Found hardcoded doctype '{denied}' in schemas.py line {node.lineno}",
                    )


if __name__ == "__main__":
    unittest.main()
