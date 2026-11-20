"""
Item writes at the registry and tool layers.

Master data with a deliberately short write list. The item code is the item's name (and unique), the
unit of measure is fixed at creation, and prices, valuation, stock and the sales, purchase and stock
switches, the disabled flag, variants, barcodes, unit conversions, tax and per-company defaults are
not writable. Field names and behaviour come from a live Frappe 16.24 instance.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry


def user(*roles):
    return RequestContext(user_id="s@example.com", roles=roles or ("Stock Manager",), read_only=False)


def client_with():
    client = MagicMock()
    ok = MagicMock(is_success=True, status_code=200)
    ok.json.return_value = {"data": {"name": "WIDGET-100"}}
    client.post.return_value = ok
    client.put.return_value = ok
    return client


class TestRegistry:
    def test_roles_are_the_item_master_roles(self):
        for op, fields in (("create", {"item_code": "W", "item_group": "Products"}), ("update", {"brand": "Ergo"})):
            assert wr.check_write(op, "Item", fields, ctx=user("Stock Manager"))
            assert wr.check_write(op, "Item", fields, ctx=user("Item Manager"))
            assert wr.check_write(op, "Item", fields, ctx=user("System Manager"))
            assert not wr.check_write(op, "Item", fields, ctx=user("Sales User", "Sales Manager", "Purchase User", "Purchase Manager", "Stock User", "Accounts User"))

    @pytest.mark.parametrize("operation", ["submit", "cancel", "delete"])
    def test_an_item_cannot_be_submitted_cancelled_or_deleted(self, operation):
        for roles in (("Stock Manager",), ("Item Manager",), ("System Manager", "Administrator")):
            assert not wr.check_write(operation, "Item", ctx=user(*roles))

    def test_create_fields(self):
        full = {"item_code": "W-1", "item_name": "Widget", "item_group": "Products", "stock_uom": "Nos", "description": "A widget", "brand": "Acme"}
        assert wr.check_write("create", "Item", full, ctx=user())

    @pytest.mark.parametrize(
        "field",
        ["standard_rate", "valuation_rate", "opening_stock", "is_stock_item", "is_sales_item", "is_purchase_item", "disabled", "has_variants",
         "has_batch_no", "has_serial_no", "reorder_levels", "safety_stock", "item_defaults", "uoms", "barcodes", "taxes", "weight_per_unit",
         "country_of_origin", "valuation_method", "is_fixed_asset", "image", "docstatus", "owner", "name", "naming_series"],
    )
    def test_prices_stock_and_control_fields_are_not_writable(self, field):
        value = [{"x": 1}] if field in ("item_defaults", "uoms", "barcodes", "taxes", "reorder_levels") else "x"
        assert not wr.check_write("create", "Item", {"item_code": "W", "item_group": "G", field: value}, ctx=user())
        assert not wr.check_write("update", "Item", {"brand": "Ergo", field: value}, ctx=user())

    def test_update_cannot_change_the_code_or_the_unit(self):
        assert not wr.check_write("update", "Item", {"item_code": "RENAMED"}, ctx=user())
        assert not wr.check_write("update", "Item", {"stock_uom": "Kg"}, ctx=user())

    def test_update_fields(self):
        assert wr.check_write("update", "Item", {"item_name": "Standing Desk", "item_group": "Products", "description": "d", "brand": "Ergo"}, ctx=user())

    def test_nested_values_are_refused(self):
        assert not wr.check_write("create", "Item", {"item_code": "W", "item_group": {"a": 1}}, ctx=user())
        assert not wr.check_write("update", "Item", {"description": ["a"]}, ctx=user())

    def test_registry_facts(self):
        assert wr.is_confirm_always("Item", "create") is True
        assert wr.is_confirm_always("Item", "update") is False
        assert wr.get_similar_by("Item", "create") == {"field": "item_code", "slot": "item_code"}
        assert wr.get_identity_slot("Item", "update") == "item_code"
        assert wr.get_identity_slot("Item", "create") is None
        assert wr.get_duplicate_keys("Item") == ["item_code"]
        assert wr.get_duplicate_slot("Item", "item_code") == "item_code"
        assert set(wr.get_confirm_fields("Item", "update")) == set(wr.get_writable_fields("Item", "update"))
        assert wr.get_writable_fields("Item", "update") == ["item_name", "item_group", "description", "brand"]

    def test_item_rows_on_orders_are_untouched(self):
        # Registering the Item doctype does not change what an item row on a document may carry.
        rows = [{"item_code": "W", "qty": 1}]
        assert wr.check_write("create", "Quotation", {"quotation_to": "Customer", "party_name": "A", "items": rows}, ctx=RequestContext(user_id="u", roles=("Sales User",), read_only=False))
        assert not wr.check_write("create", "Quotation", {"quotation_to": "Customer", "party_name": "A", "items": [{"item_code": "W", "qty": 1, "standard_rate": 5}]}, ctx=RequestContext(user_id="u", roles=("Sales User",), read_only=False))


class TestSkillValidation:
    def skill(self, **over):
        s = {
            "name": "update-item", "tool": "update_document", "doctype": "Item",
            "allowed_roles": ["Stock Manager", "Item Manager", "System Manager", "Administrator"],
            "required_fields": ["item_code"], "optional_fields": ["item_group", "brand"], "require_any_of": ["item_group", "brand"], "payload_structure": {},
        }
        s.update(over)
        return s

    def test_the_identity_slot_is_not_counted_as_a_field_to_change(self):
        assert wr.validate_write_skill(self.skill()) == []

    def test_an_update_skill_must_ask_for_the_item_code(self):
        assert any("item_code" in p and "must require" in p for p in wr.validate_write_skill(self.skill(required_fields=["name"])))

    def test_price_and_stock_fields_are_reported_if_a_skill_lists_them(self):
        assert any("standard_rate" in p for p in wr.validate_write_skill(self.skill(optional_fields=["item_group", "standard_rate"])))

    def test_the_shipped_item_skills_load(self):
        from agent.workflow import graph

        names = {s["name"] for s in graph.loaded_skills}
        assert {"create-item", "update-item", "item-lookup"} <= names


class TestCrud:
    def test_create_sends_exactly_what_it_was_given(self):
        client = client_with()
        fields = {"item_code": "WIDGET-100", "item_group": "Products", "item_name": "Widget"}
        with set_request_context(user()):
            res = crud.create_document("Item", dict(fields), client=client)
        assert res["status"] == "success"
        assert client.post.call_args.args[0] == "/api/resource/Item"
        assert client.post.call_args.kwargs["json"] == fields, "no company, date or warehouse default is added to an Item"

    def test_update_is_a_header_write_with_no_read_and_carries_the_version(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Item", "WIDGET-100", {"item_group": "Consumable"}, client=client, expected_modified="v1")
        assert res["status"] == "success"
        assert client.put.call_args.args[0] == "/api/resource/Item/WIDGET-100"
        assert client.put.call_args.kwargs["json"] == {"item_group": "Consumable", "modified": "v1"}
        client.get.assert_not_called()

    @pytest.mark.parametrize("fields", [{"standard_rate": 9}, {"is_stock_item": 0}, {"disabled": 1}, {"item_code": "X"}, {"stock_uom": "Kg"}, {"barcodes": [{"barcode": "1"}]}, {"valuation_rate": 1}])
    def test_refused_fields_never_reach_erpnext(self, fields):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Item", "WIDGET-100", fields, client=client)
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()

    def test_delete_never_reaches_erpnext(self):
        client = client_with()
        with set_request_context(user("System Manager")):
            assert crud.delete_document("Item", "WIDGET-100", client=client)["status"] == "permission_denied"
        client.delete.assert_not_called()

    def test_a_role_without_rights_is_refused_before_any_call(self):
        client = client_with()
        with set_request_context(user("Sales User", "Stock User")):
            assert crud.create_document("Item", {"item_code": "W", "item_group": "G"}, client=client)["status"] == "permission_denied"
            assert crud.update_document("Item", "W", {"brand": "B"}, client=client)["status"] == "permission_denied"
        client.post.assert_not_called()
        client.put.assert_not_called()
