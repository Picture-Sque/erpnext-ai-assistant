"""
Supplier writes at the registry and tool layers.

Master data like a Customer: not submittable, named by its supplier name, with payment holds, tax,
payment terms, bank accounts and the disabled, frozen and purchasing switches deliberately not
writable. The supplier is asked for as "the supplier", so the create slot and the update's identity
slot are `supplier`. Field names come from a live Frappe 16.24 instance.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry


def user(*roles):
    return RequestContext(user_id="p@example.com", roles=roles or ("Purchase User",), read_only=False)


def client_with():
    client = MagicMock()
    ok = MagicMock(is_success=True, status_code=200)
    ok.json.return_value = {"data": {"name": "Summit Traders Ltd."}}
    client.post.return_value = ok
    client.put.return_value = ok
    return client


class TestRegistry:
    def test_roles_are_the_purchasing_roles(self):
        for op, fields in (("create", {"supplier_name": "A"}), ("update", {"country": "India"})):
            assert wr.check_write(op, "Supplier", fields, ctx=user("Purchase User"))
            assert wr.check_write(op, "Supplier", fields, ctx=user("Purchase Manager"))
            assert wr.check_write(op, "Supplier", fields, ctx=user("System Manager"))
            assert not wr.check_write(op, "Supplier", fields, ctx=user("Sales User", "Sales Manager", "Accounts User", "Stock User"))

    @pytest.mark.parametrize("operation", ["submit", "cancel", "delete"])
    def test_a_supplier_cannot_be_submitted_cancelled_or_deleted(self, operation):
        for roles in (("Purchase User",), ("Purchase Manager",), ("System Manager", "Administrator")):
            assert not wr.check_write(operation, "Supplier", ctx=user(*roles))

    def test_create_fields(self):
        full = {"supplier_name": "A", "supplier_type": "Individual", "supplier_group": "Hardware", "country": "India", "website": "https://a.example"}
        assert wr.check_write("create", "Supplier", full, ctx=user())

    @pytest.mark.parametrize(
        "field",
        ["on_hold", "hold_type", "release_date", "payment_terms", "tax_id", "tax_category", "tax_withholding_category", "default_bank_account",
         "default_price_list", "default_currency", "disabled", "is_frozen", "is_transporter", "is_internal_supplier", "represents_company",
         "allow_purchase_invoice_creation_without_purchase_order", "allow_purchase_invoice_creation_without_purchase_receipt",
         "accounts", "companies", "portal_users", "supplier_primary_address", "email_id", "mobile_no", "docstatus", "owner", "name", "naming_series"],
    )
    def test_payment_tax_and_control_fields_are_not_writable(self, field):
        value = [{"x": 1}] if field in ("accounts", "companies", "portal_users") else "x"
        assert not wr.check_write("create", "Supplier", {"supplier_name": "A", field: value}, ctx=user())
        assert not wr.check_write("update", "Supplier", {"country": "India", field: value}, ctx=user())

    def test_update_cannot_rename(self):
        assert not wr.check_write("update", "Supplier", {"supplier_name": "Renamed"}, ctx=user())

    def test_update_fields(self):
        assert wr.check_write("update", "Supplier", {"supplier_group": "Hardware", "country": "India", "website": "x.example", "supplier_type": "Company"}, ctx=user())

    @pytest.mark.parametrize("bad", ["Charity", "company", "", None, 5, ["Company"]])
    def test_supplier_type_must_be_a_known_value(self, bad):
        assert not wr.check_write("create", "Supplier", {"supplier_name": "A", "supplier_type": bad}, ctx=user())
        assert not wr.check_write("update", "Supplier", {"supplier_type": bad}, ctx=user())

    def test_registry_facts(self):
        assert wr.is_confirm_always("Supplier", "create") is True
        assert wr.is_confirm_always("Supplier", "update") is False
        assert wr.get_similar_by("Supplier", "create") == {"field": "supplier_name", "slot": "supplier"}
        assert wr.get_identity_slot("Supplier", "update") == "supplier"
        assert wr.get_duplicate_keys("Supplier") == ["supplier_name"]
        assert wr.get_duplicate_slot("Supplier", "supplier_name") == "supplier"
        assert wr.get_choices("Supplier", "update") == {"supplier_type": ["Company", "Individual", "Partnership"]}
        assert set(wr.get_confirm_fields("Supplier", "update")) == set(wr.get_writable_fields("Supplier", "update"))

    def test_the_customer_and_supplier_entries_do_not_leak_into_each_other(self):
        assert not wr.check_write("update", "Supplier", {"customer_group": "x"}, ctx=user())
        assert not wr.check_write("update", "Customer", {"supplier_group": "x"}, ctx=RequestContext(user_id="s", roles=("Sales User",), read_only=False))
        assert not wr.check_write("update", "Customer", {"territory": "India"}, ctx=user("Purchase User"))


class TestSkillValidation:
    def skill(self, **over):
        s = {
            "name": "create-supplier", "tool": "create_document", "doctype": "Supplier",
            "allowed_roles": ["Purchase User", "Purchase Manager", "System Manager", "Administrator"],
            "required_fields": ["supplier"], "optional_fields": ["supplier_type", "country"],
            "payload_structure": {"fixed": {}, "field_mappings": {"supplier_name": "supplier", "supplier_type": "supplier_type", "country": "country"}},
        }
        s.update(over)
        return s

    def test_a_slot_that_fills_a_differently_named_field_is_accepted(self):
        assert wr.validate_write_skill(self.skill()) == []

    def test_sales_roles_may_not_be_added_to_a_supplier_skill(self):
        problems = wr.validate_write_skill(self.skill(allowed_roles=["Purchase User", "Sales User"]))
        assert any("Sales User" in p for p in problems)

    def test_an_unwritable_mapping_is_reported(self):
        ps = {"fixed": {}, "field_mappings": {"supplier_name": "supplier", "on_hold": "supplier"}}
        assert any("on_hold" in p for p in wr.validate_write_skill(self.skill(payload_structure=ps)))

    def test_the_shipped_supplier_skills_load(self):
        from agent.workflow import graph

        names = {s["name"] for s in graph.loaded_skills}
        assert {"create-supplier", "update-supplier", "supplier-lookup"} <= names


class TestCrud:
    def test_create_sends_exactly_what_it_was_given(self):
        client = client_with()
        fields = {"supplier_name": "Harbour Metals", "supplier_type": "Company", "supplier_group": "Hardware"}
        with set_request_context(user()):
            res = crud.create_document("Supplier", dict(fields), client=client)
        assert res["status"] == "success"
        assert client.post.call_args.args[0] == "/api/resource/Supplier"
        assert client.post.call_args.kwargs["json"] == fields, "no company, date or warehouse default is added to a Supplier"

    def test_update_is_a_header_write_with_no_read_and_carries_the_version(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Supplier", "Summit Traders Ltd.", {"supplier_group": "Hardware"}, client=client, expected_modified="v1")
        assert res["status"] == "success"
        assert client.put.call_args.args[0] == "/api/resource/Supplier/Summit Traders Ltd."
        assert client.put.call_args.kwargs["json"] == {"supplier_group": "Hardware", "modified": "v1"}
        client.get.assert_not_called()

    @pytest.mark.parametrize("fields", [{"on_hold": 1}, {"payment_terms": "x"}, {"tax_id": "1"}, {"disabled": 1}, {"supplier_name": "X"}, {"supplier_type": "Charity"}, {"accounts": [{"a": 1}]}])
    def test_refused_fields_never_reach_erpnext(self, fields):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Supplier", "Summit Traders Ltd.", fields, client=client)
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()

    def test_delete_never_reaches_erpnext(self):
        client = client_with()
        with set_request_context(user("System Manager")):
            assert crud.delete_document("Supplier", "Summit Traders Ltd.", client=client)["status"] == "permission_denied"
        client.delete.assert_not_called()

    def test_a_sales_role_is_refused_before_any_call(self):
        client = client_with()
        with set_request_context(user("Sales User", "Sales Manager")):
            assert crud.create_document("Supplier", {"supplier_name": "A"}, client=client)["status"] == "permission_denied"
            assert crud.update_document("Supplier", "A", {"country": "India"}, client=client)["status"] == "permission_denied"
        client.post.assert_not_called()
        client.put.assert_not_called()
