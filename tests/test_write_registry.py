"""
Tests for the write registry and its enforcement in crud.py and policy.py.

These run against the REAL registry (no legacy test doctypes), so they prove what the
assistant is and is not allowed to write.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context
from mcp_erpnext.policy import authorize_tool_call

pytestmark = pytest.mark.real_write_registry


def ctx(*roles, read_only=False, user="tester@example.com"):
    return RequestContext(user_id=user, roles=tuple(roles), read_only=read_only)


def ok_response(data=None):
    resp = MagicMock()
    resp.is_success = True
    resp.status_code = 200
    resp.json.return_value = {"data": data or {"name": "SO-0001"}}
    return resp


def mock_client():
    client = MagicMock()
    client.post.return_value = ok_response()
    client.put.return_value = ok_response()
    client.delete.return_value = ok_response()
    return client


SO_ITEMS = [{"item_code": "SKU001", "qty": 2, "rate": 10}]


class TestCheckWrite:
    def test_sales_user_can_create_and_update_but_not_submit_or_cancel_sales_order(self):
        c = ctx("Sales User")
        assert wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS}, ctx=c)
        assert wr.check_write("update", "Sales Order", {"delivery_date": "2026-12-01"}, ctx=c)
        assert not wr.check_write("submit", "Sales Order", ctx=c)
        assert not wr.check_write("cancel", "Sales Order", ctx=c)

    def test_sales_manager_can_submit_and_cancel(self):
        c = ctx("Sales Manager")
        assert wr.check_write("submit", "Sales Order", ctx=c)
        assert wr.check_write("cancel", "Sales Order", ctx=c)

    def test_purchase_roles_do_not_cross_over_to_sales_orders(self):
        c = ctx("Purchase Manager")
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS}, ctx=c)
        assert wr.check_write("create", "Purchase Order", {"supplier": "S", "items": SO_ITEMS}, ctx=c)

    def test_purchase_user_cannot_submit_purchase_order(self):
        assert not wr.check_write("submit", "Purchase Order", ctx=ctx("Purchase User"))
        assert wr.check_write("submit", "Purchase Order", ctx=ctx("Purchase Manager"))

    def test_system_manager_allowed(self):
        assert wr.check_write("submit", "Sales Order", ctx=ctx("System Manager"))

    @pytest.mark.parametrize("roles", [(), ("Guest",), ("Accounts User",), ("Stock User",)])
    def test_unrelated_or_empty_roles_denied(self, roles):
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS}, ctx=ctx(*roles))

    def test_read_only_context_denied_even_for_admin(self):
        d = wr.check_write("submit", "Sales Order", ctx=ctx("System Manager", read_only=True))
        assert not d
        assert "read-only" in d.public_message

    def test_read_only_flag_must_be_exactly_false(self):
        # RequestContext coerces non-bool read_only to True, so the fail-closed default holds.
        c = RequestContext(user_id="u", roles=("System Manager",), read_only="false")
        assert c.read_only is True
        assert not wr.check_write("submit", "Sales Order", ctx=c)

    @pytest.mark.parametrize("doctype", ["Supplier", "Item", "Bin", "Stock Ledger Entry", "Company", "Warehouse", "User", "", None])
    def test_doctypes_without_an_entry_cannot_be_written(self, doctype):
        for op in ("create", "update", "submit", "cancel", "delete"):
            assert not wr.check_write(op, doctype, {}, ctx=ctx("System Manager")), (doctype, op)

    def test_delete_is_not_registered_for_any_doctype(self):
        for doctype in wr.get_write_registry():
            assert not wr.check_write("delete", doctype, ctx=ctx("System Manager"))

    def test_unknown_operation_denied(self):
        assert not wr.check_write("drop", "Sales Order", ctx=ctx("System Manager"))
        assert not wr.check_write(None, "Sales Order", ctx=ctx("System Manager"))

    def test_doctype_lookup_is_case_insensitive(self):
        assert wr.check_write("submit", "sales order", ctx=ctx("Sales Manager"))


class TestFieldChecks:
    admin = ctx("System Manager")

    @pytest.mark.parametrize(
        "field",
        ["docstatus", "owner", "creation", "modified", "modified_by", "name", "parent", "parenttype", "amended_from", "idx"],
    )
    def test_reserved_fields_denied_on_create_and_update(self, field):
        assert not wr.check_write("create", "Sales Order", {"customer": "A", field: "x"}, ctx=self.admin)
        assert not wr.check_write("update", "Sales Order", {"delivery_date": "2026-12-01", field: "x"}, ctx=self.admin)

    @pytest.mark.parametrize("field", ["grand_total", "status", "per_billed", "total", "taxes_and_charges"])
    def test_undeclared_fields_denied(self, field):
        d = wr.check_write("update", "Sales Order", {field: 1}, ctx=self.admin)
        assert not d
        assert field in d.public_message

    def test_update_only_allows_declared_fields(self):
        assert wr.check_write("update", "Sales Order", {"delivery_date": "2026-12-01"}, ctx=self.admin)
        assert not wr.check_write("update", "Sales Order", {"customer": "Someone Else"}, ctx=self.admin)
        assert wr.check_write("update", "Purchase Order", {"schedule_date": "2026-12-01"}, ctx=self.admin)

    def test_child_row_fields_checked(self):
        good = [{"item_code": "A", "qty": 1, "rate": 5, "warehouse": "W", "delivery_date": "2026-12-01"}]
        assert wr.check_write("create", "Sales Order", {"customer": "A", "items": good}, ctx=self.admin)
        for bad_key in ("amount", "docstatus", "parent", "name", "idx", "item_name"):
            bad = [{"item_code": "A", "qty": 1, bad_key: "x"}]
            assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": bad}, ctx=self.admin), bad_key

    def test_malformed_values_denied(self):
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": "oops"}, ctx=self.admin)
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": ["x"]}, ctx=self.admin)
        assert not wr.check_write("create", "Sales Order", {"customer": {"a": 1}}, ctx=self.admin)
        assert not wr.check_write("create", "Sales Order", "not a dict", ctx=self.admin)
        assert not wr.check_write("create", "Sales Order", None, ctx=self.admin)

    def test_submit_and_cancel_do_not_look_at_fields(self):
        assert wr.check_write("submit", "Sales Order", None, ctx=ctx("Sales Manager"))


class TestContextHandling:
    def test_no_context_is_denied_under_strict_identity(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_STRICT_IDENTITY", "true")
        d = wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS})
        assert not d

    def test_no_context_allowed_when_not_strict_but_registry_still_applies(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_STRICT_IDENTITY", "false")
        assert wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS})
        assert not wr.check_write("create", "Supplier", {"supplier_name": "A"})
        assert not wr.check_write("update", "Sales Order", {"grand_total": 1})

    def test_ambient_context_is_used(self):
        with set_request_context(ctx("Sales User")):
            assert wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS})
            assert not wr.check_write("submit", "Sales Order")

    def test_emergency_switch_blocks_everything(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_WRITES_DISABLED", "true")
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": SO_ITEMS}, ctx=ctx("System Manager"))

    def test_roles_as_bare_string_or_garbage_fail_closed(self):
        assert not wr._role_decision("Sales User", ["Sales User"])
        assert not wr._role_decision(None, ["Sales User"])
        assert not wr._role_decision(["Sales User"], [])
        assert not wr._role_decision(["Sales User"], None or [])
        assert wr._role_decision(["Sales User"], ["Sales User"])


class TestRegistryIntegrity:
    def test_every_operation_declares_roles(self):
        for doctype, spec in wr.get_write_registry().items():
            for op, op_spec in spec["operations"].items():
                assert op in wr.WRITE_OPERATIONS
                assert wr.get_operation_roles(doctype, op), (doctype, op)

    def test_no_reserved_field_is_declared_writable(self):
        for doctype, spec in wr.get_write_registry().items():
            for op, op_spec in spec["operations"].items():
                assert not set(op_spec.get("fields", [])) & wr.RESERVED_WRITE_FIELDS
                for rows in op_spec.get("child_fields", {}).values():
                    assert not set(rows) & wr.RESERVED_WRITE_FIELDS

    def test_every_registered_doctype_is_on_the_read_whitelist(self):
        allowed = {d.lower() for d in crud.DEFAULT_WHITELIST}
        for doctype in wr.get_write_registry():
            assert doctype.lower() in allowed

    def test_duplicate_keys(self):
        assert wr.get_duplicate_keys("Sales Order") == ["customer"]
        assert wr.get_duplicate_keys("Purchase Order") == ["supplier"]
        assert wr.get_duplicate_keys("Bin") == []

    def test_bulk_cap_default_and_override(self, monkeypatch):
        monkeypatch.delenv("WRITE_BULK_MAX", raising=False)
        assert wr.get_bulk_max_records() == wr.DEFAULT_BULK_MAX_RECORDS
        monkeypatch.setenv("WRITE_BULK_MAX", "7")
        assert wr.get_bulk_max_records() == 7
        for bad in ("0", "-3", "abc", ""):
            monkeypatch.setenv("WRITE_BULK_MAX", bad)
            assert wr.get_bulk_max_records() == wr.DEFAULT_BULK_MAX_RECORDS


class TestSanitizePayload:
    def test_drops_undeclared_top_level_and_row_keys(self):
        clean, dropped = wr.sanitize_payload(
            "Sales Order",
            "create",
            {
                "customer": "A",
                "grand_total": 999,
                "items": [{"item_code": "X", "qty": 1, "item_name": "Widget", "docstatus": 1}],
            },
        )
        assert clean == {"customer": "A", "items": [{"item_code": "X", "qty": 1}]}
        assert "grand_total" in dropped
        assert "items[].item_name" in dropped
        assert "items[].docstatus" in dropped

    def test_update_keeps_only_declared_fields(self):
        clean, dropped = wr.sanitize_payload(
            "Sales Order", "update", {"delivery_date": "2026-12-01", "docstatus": 1, "filters": [["a", "=", "b"]]}
        )
        assert clean == {"delivery_date": "2026-12-01"}
        assert set(dropped) == {"docstatus", "filters"}

    def test_unregistered_doctype_yields_nothing(self):
        assert wr.sanitize_payload("Supplier", "create", {"supplier_name": "A"}) == ({}, ["supplier_name"])

    def test_non_dict_input(self):
        assert wr.sanitize_payload("Sales Order", "create", None) == ({}, [])


class TestCrudEnforcement:
    def test_create_denied_before_any_http_call(self):
        client = mock_client()
        with set_request_context(ctx("Accounts User")):
            res = crud.create_document("Sales Order", {"customer": "A", "items": SO_ITEMS}, client=client)
        assert res["status"] == "permission_denied"
        client.post.assert_not_called()

    def test_create_allowed_for_sales_user_reaches_erpnext(self):
        client = mock_client()
        with set_request_context(ctx("Sales User")):
            res = crud.create_document(
                "Sales Order",
                {"customer": "A", "items": [dict(i) for i in SO_ITEMS]},
                client=client,
                default_company_resolver=lambda: "Acme",
                default_warehouse_resolver=lambda company=None: "Stores",
            )
        assert res["status"] == "success"
        client.post.assert_called_once()

    def test_create_with_unknown_field_denied(self):
        client = mock_client()
        with set_request_context(ctx("System Manager")):
            res = crud.create_document(
                "Sales Order", {"customer": "A", "items": SO_ITEMS, "grand_total": 1}, client=client
            )
        assert res["status"] == "permission_denied"
        client.post.assert_not_called()

    def test_update_cannot_set_docstatus(self):
        client = mock_client()
        with set_request_context(ctx("System Manager")):
            res = crud.update_document("Sales Order", "SO-1", {"docstatus": 1}, client=client)
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()

    def test_update_with_declared_field_is_sent(self):
        client = mock_client()
        client.get.return_value = ok_response({"name": "SO-1", "items": []})
        with set_request_context(ctx("Sales User")):
            res = crud.update_document("Sales Order", "SO-1", {"delivery_date": "2026-12-01"}, client=client)
        assert res["status"] == "success"
        client.put.assert_called_once()

    def test_submit_sends_docstatus_one_for_manager_only(self):
        client = mock_client()
        with set_request_context(ctx("Sales User")):
            denied = crud.submit_document("Sales Order", "SO-1", client=client)
        assert denied["status"] == "permission_denied"
        client.put.assert_not_called()

        with set_request_context(ctx("Sales Manager")):
            res = crud.submit_document("Sales Order", "SO-1", client=client)
        assert res["status"] == "success"
        assert client.put.call_args.kwargs["json"] == {"docstatus": 1}

    def test_cancel_sends_docstatus_two_for_manager_only(self):
        client = mock_client()
        with set_request_context(ctx("Purchase User")):
            assert crud.cancel_document("Purchase Order", "PO-1", client=client)["status"] == "permission_denied"
        with set_request_context(ctx("Purchase Manager")):
            res = crud.cancel_document("Purchase Order", "PO-1", client=client)
        assert res["status"] == "success"
        assert client.put.call_args.kwargs["json"] == {"docstatus": 2}

    def test_delete_denied_for_every_role(self):
        client = mock_client()
        with set_request_context(ctx("System Manager", "Administrator")):
            res = crud.delete_document("Sales Order", "SO-1", client=client)
        assert res["status"] == "permission_denied"
        client.delete.assert_not_called()

    def test_read_only_session_gets_permission_denied_not_system_error(self):
        client = mock_client()
        with set_request_context(ctx("System Manager", read_only=True)):
            res = crud.submit_document("Sales Order", "SO-1", client=client)
        assert res["status"] == "permission_denied"
        assert "read-only" in res["error"]
        client.put.assert_not_called()

    def test_whitelist_denial_text_is_unchanged_for_unlisted_doctypes(self):
        res = crud.create_document("SecretDoctype", {"x": 1}, client=mock_client())
        assert res["status"] == "permission_denied"
        assert "not permitted by whitelist" in res["error"]

    def test_envelope_keeps_five_keys_on_denial(self):
        with set_request_context(ctx("Guest")):
            res = crud.submit_document("Sales Order", "SO-1", client=mock_client())
        assert set(res) == {"status", "data", "error", "has_more", "success"}
        assert res["success"] is False


class TestPolicyEnforcement:
    def test_write_tool_denied_for_wrong_role(self):
        dec = authorize_tool_call(
            ctx("Accounts User"), "create_document", {"doctype": "Sales Order", "fields": {"customer": "A"}}
        )
        assert not dec

    def test_write_tool_denied_for_unregistered_doctype(self):
        dec = authorize_tool_call(
            ctx("System Manager"), "create_document", {"doctype": "Supplier", "fields": {"supplier_name": "A"}}
        )
        assert not dec

    def test_write_tool_denied_for_undeclared_field(self):
        dec = authorize_tool_call(
            ctx("System Manager"),
            "update_document",
            {"doctype": "Sales Order", "name": "SO-1", "fields": {"docstatus": 1}},
        )
        assert not dec

    def test_write_tool_allowed_for_correct_role_and_fields(self):
        dec = authorize_tool_call(
            ctx("Sales User"),
            "update_document",
            {"doctype": "Sales Order", "name": "SO-1", "fields": {"delivery_date": "2026-12-01"}},
        )
        assert dec

    def test_submit_tool_follows_the_registry(self):
        assert not authorize_tool_call(ctx("Sales User"), "submit_document", {"doctype": "Sales Order", "name": "SO-1"})
        assert authorize_tool_call(ctx("Sales Manager"), "submit_document", {"doctype": "Sales Order", "name": "SO-1"})

    def test_delete_tool_denied(self):
        assert not authorize_tool_call(
            ctx("System Manager"), "delete_document", {"doctype": "Sales Order", "name": "SO-1"}
        )


class TestSkillValidation:
    def base_skill(self, **overrides):
        skill = {
            "name": "create-sales-order",
            "tool": "create_document",
            "doctype": "Sales Order",
            "allowed_roles": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
            "required_fields": ["customer", "items"],
            "optional_fields": ["transaction_date", "delivery_date"],
            "payload_structure": {"field_mappings": {"customer": "customer", "items": "items"}},
        }
        skill.update(overrides)
        return skill

    def test_consistent_skill_has_no_problems(self):
        assert wr.validate_write_skill(self.base_skill()) == []

    def test_read_skill_is_ignored(self):
        assert wr.validate_write_skill({"name": "x", "tool": "get_list", "doctype": "Customer"}) == []

    def test_unregistered_doctype_reported(self):
        assert wr.validate_write_skill(self.base_skill(doctype="Supplier"))

    def test_extra_role_reported(self):
        problems = wr.validate_write_skill(self.base_skill(allowed_roles=["Sales User", "Accounts User"]))
        assert any("Accounts User" in p for p in problems)

    def test_missing_roles_reported(self):
        assert wr.validate_write_skill(self.base_skill(allowed_roles=[]))

    def test_undeclared_field_reported(self):
        problems = wr.validate_write_skill(self.base_skill(optional_fields=["grand_total"]))
        assert any("grand_total" in p for p in problems)

    def test_undeclared_mapping_reported(self):
        problems = wr.validate_write_skill(
            self.base_skill(payload_structure={"field_mappings": {"status": "status"}})
        )
        assert any("status" in p for p in problems)
