"""
Customer writes at the registry and tool layers.

A Customer is master data: not submittable, named by its customer name, and an exact duplicate
name is NOT refused by ERPNext over REST (it creates a second record named "<name> - 1", seen
live), so the assistant has to refuse it. Credit limits, accounts, tax, payment terms and the
disabled flag are deliberately not writable. Field names come from a live Frappe 16.24 instance.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry


def user(*roles):
    return RequestContext(user_id="u@example.com", roles=roles or ("Sales User",), read_only=False)


def client_with():
    client = MagicMock()
    ok = MagicMock(is_success=True, status_code=200)
    ok.json.return_value = {"data": {"name": "Acme Industries"}}
    client.post.return_value = ok
    client.put.return_value = ok
    return client


class TestRegistry:
    def test_roles(self):
        for op, fields in (("create", {"customer_name": "A"}), ("update", {"territory": "India"})):
            assert wr.check_write(op, "Customer", fields, ctx=user("Sales User"))
            assert wr.check_write(op, "Customer", fields, ctx=user("Sales Manager"))
            assert wr.check_write(op, "Customer", fields, ctx=user("System Manager"))
            assert not wr.check_write(op, "Customer", fields, ctx=user("Accounts User", "Stock User", "Purchase User"))

    @pytest.mark.parametrize("operation", ["submit", "cancel", "delete"])
    def test_a_customer_cannot_be_submitted_cancelled_or_deleted(self, operation):
        for roles in (("Sales User",), ("Sales Manager",), ("System Manager", "Administrator")):
            assert not wr.check_write(operation, "Customer", ctx=user(*roles))

    def test_create_fields(self):
        full = {"customer_name": "A", "customer_type": "Individual", "customer_group": "Commercial", "territory": "India", "website": "https://a.example"}
        assert wr.check_write("create", "Customer", full, ctx=user())

    @pytest.mark.parametrize(
        "field",
        ["credit_limits", "accounts", "default_price_list", "default_currency", "payment_terms", "tax_id", "tax_category",
         "disabled", "is_frozen", "account_manager", "sales_team", "customer_primary_address", "email_id", "mobile_no",
         "represents_company", "is_internal_customer", "so_required", "docstatus", "owner", "name", "naming_series"],
    )
    def test_finance_and_other_fields_are_not_writable(self, field):
        value = [{"x": 1}] if field in ("credit_limits", "accounts", "sales_team") else "x"
        assert not wr.check_write("create", "Customer", {"customer_name": "A", field: value}, ctx=user())
        assert not wr.check_write("update", "Customer", {"territory": "India", field: value}, ctx=user())

    def test_update_cannot_rename_or_change_the_identity_field(self):
        assert not wr.check_write("update", "Customer", {"customer_name": "Renamed"}, ctx=user())

    def test_update_fields(self):
        assert wr.check_write("update", "Customer", {"customer_group": "Government", "territory": "India", "website": "x.example", "customer_type": "Company"}, ctx=user())

    @pytest.mark.parametrize("bad", ["Charity", "company", "", None, 5, ["Company"]])
    def test_customer_type_must_be_one_of_the_known_values(self, bad):
        assert not wr.check_write("create", "Customer", {"customer_name": "A", "customer_type": bad}, ctx=user())
        assert not wr.check_write("update", "Customer", {"customer_type": bad}, ctx=user())

    @pytest.mark.parametrize("good", ["Company", "Individual", "Partnership"])
    def test_known_customer_types_pass(self, good):
        assert wr.check_write("create", "Customer", {"customer_name": "A", "customer_type": good}, ctx=user())

    def test_nested_values_are_refused(self):
        assert not wr.check_write("create", "Customer", {"customer_name": {"a": 1}}, ctx=user())
        assert not wr.check_write("update", "Customer", {"territory": ["India"]}, ctx=user())

    def test_registry_facts(self):
        assert wr.is_confirm_always("Customer", "create") is True
        assert wr.is_confirm_always("Customer", "update") is False
        assert wr.is_confirm_always("Sales Order", "create") is False
        assert wr.get_similar_by("Customer", "create") == {"field": "customer_name", "slot": "customer_name"}
        assert wr.get_similar_by("Customer", "update") is None
        assert wr.get_similar_by("Sales Order", "create") is None
        assert wr.get_identity_slot("Customer", "update") == "customer_name"
        assert wr.get_identity_slot("Customer", "create") is None
        assert wr.get_identity_slot("Sales Order", "update") is None
        assert set(wr.get_confirm_fields("Customer", "update")) == {"customer_type", "customer_group", "territory", "website"}
        assert wr.get_choices("Customer", "create") == {"customer_type": ["Company", "Individual", "Partnership"]}
        assert wr.get_choices("Sales Order", "create") == {}

    def test_every_updatable_field_asks_for_confirmation(self):
        assert set(wr.get_confirm_fields("Customer", "update")) == set(wr.get_writable_fields("Customer", "update"))


class TestSkillValidation:
    def skill(self, **over):
        s = {
            "name": "update-customer", "tool": "update_document", "doctype": "Customer",
            "allowed_roles": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
            "required_fields": ["customer_name"], "optional_fields": ["customer_group", "territory"],
            "require_any_of": ["customer_group", "territory"], "payload_structure": {},
        }
        s.update(over)
        return s

    def test_the_identity_slot_is_not_counted_as_a_field_to_change(self):
        assert wr.validate_write_skill(self.skill()) == []

    def test_an_update_skill_must_ask_for_its_identity_slot(self):
        problems = wr.validate_write_skill(self.skill(required_fields=["name"]))
        assert any("customer_name" in p and "must require" in p for p in problems)

    def test_other_fields_are_still_checked(self):
        problems = wr.validate_write_skill(self.skill(optional_fields=["customer_group", "credit_limits"]))
        assert any("credit_limits" in p for p in problems)

    def test_the_shipped_customer_skills_load(self):
        from agent.workflow import graph

        names = {s["name"] for s in graph.loaded_skills}
        assert {"create-customer", "update-customer", "customer-lookup"} <= names


class TestCrud:
    def test_create_sends_exactly_what_it_was_given(self):
        client = client_with()
        fields = {"customer_name": "Northwind", "customer_type": "Company", "customer_group": "Commercial"}
        with set_request_context(user()):
            res = crud.create_document("Customer", dict(fields), client=client)
        assert res["status"] == "success"
        assert client.post.call_args.args[0] == "/api/resource/Customer"
        assert client.post.call_args.kwargs["json"] == fields, "no company, date or warehouse default is added to a Customer"

    def test_update_is_a_header_write_with_no_read_and_carries_the_version(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Customer", "Acme Industries", {"customer_group": "Government"}, client=client, expected_modified="2026-11-20 10:00:00.000001")
        assert res["status"] == "success"
        assert client.put.call_args.args[0] == "/api/resource/Customer/Acme Industries"
        assert client.put.call_args.kwargs["json"] == {"customer_group": "Government", "modified": "2026-11-20 10:00:00.000001"}
        client.get.assert_not_called()

    @pytest.mark.parametrize("fields", [{"credit_limits": [{"credit_limit": 1}]}, {"tax_id": "1"}, {"disabled": 1}, {"customer_name": "X"}, {"customer_type": "Charity"}])
    def test_refused_fields_never_reach_erpnext(self, fields):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Customer", "Acme Industries", fields, client=client)
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()

    def test_delete_never_reaches_erpnext(self):
        client = client_with()
        with set_request_context(user("System Manager")):
            assert crud.delete_document("Customer", "Acme Industries", client=client)["status"] == "permission_denied"
        client.delete.assert_not_called()

    def test_a_role_without_rights_is_refused_before_any_call(self):
        client = client_with()
        with set_request_context(user("Accounts User")):
            assert crud.create_document("Customer", {"customer_name": "A"}, client=client)["status"] == "permission_denied"
            assert crud.update_document("Customer", "A", {"territory": "India"}, client=client)["status"] == "permission_denied"
        client.post.assert_not_called()
        client.put.assert_not_called()
