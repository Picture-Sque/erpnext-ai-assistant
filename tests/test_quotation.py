"""
Quotation writes at the registry and tool layers.

A Quotation keeps its customer in party_name (with quotation_to = "Customer"), asks the user
for the "customer", has no per-row date or warehouse, and needs a company and a date on
create. Field names here were read from a live Frappe 16.24 instance.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry


def user(*roles):
    return RequestContext(user_id="u@example.com", roles=roles or ("Sales User",), read_only=False)


def ok_response(data=None):
    resp = MagicMock(is_success=True, status_code=200)
    resp.json.return_value = {"data": data or {"name": "SAL-QTN-2026-00001"}}
    return resp


def client_with(doc=None):
    client = MagicMock()
    client.post.return_value = ok_response()
    client.put.return_value = ok_response()
    client.get.return_value = ok_response(doc if doc is not None else current_doc())
    return client


def current_doc():
    return {
        "name": "QTN-1", "docstatus": 0, "valid_till": "2026-12-31",
        "items": [
            {"name": "q-a", "item_code": "DESK", "qty": 2, "rate": 100},
            {"name": "q-b", "item_code": "CHAIR", "qty": 4, "rate": 50},
        ],
    }


CREATE = {"quotation_to": "Customer", "party_name": "Acme", "items": [{"item_code": "DESK", "qty": 2, "rate": 100}]}


class TestRegistry:
    def test_roles(self):
        for op, fields in (("create", CREATE), ("update", {"valid_till": "2027-01-01"})):
            assert wr.check_write(op, "Quotation", fields, ctx=user("Sales User"))
            assert wr.check_write(op, "Quotation", fields, ctx=user("Sales Manager"))
            assert not wr.check_write(op, "Quotation", fields, ctx=user("Purchase User", "Purchase Manager"))
        assert not wr.check_write("submit", "Quotation", ctx=user("Sales User"))
        assert wr.check_write("submit", "Quotation", ctx=user("Sales Manager"))
        assert not wr.check_write("cancel", "Quotation", ctx=user("Sales User"))
        assert wr.check_write("cancel", "Quotation", ctx=user("Sales Manager"))
        assert wr.check_write("cancel", "Quotation", ctx=user("System Manager"))

    def test_delete_is_not_available(self):
        assert not wr.check_write("delete", "Quotation", ctx=user("System Manager"))

    def test_the_create_payload_the_skill_builds_is_allowed(self):
        assert wr.check_write("create", "Quotation", dict(CREATE, transaction_date="2026-11-21", valid_till="2026-12-31", company="Acme Ltd"), ctx=user())

    @pytest.mark.parametrize("field", ["grand_total", "status", "customer_name", "party_type", "order_type", "currency", "docstatus", "owner", "name"])
    def test_other_create_fields_are_denied(self, field):
        assert not wr.check_write("create", "Quotation", dict(CREATE, **{field: "x"}), ctx=user())

    @pytest.mark.parametrize("row_field", ["warehouse", "delivery_date", "schedule_date", "amount", "item_name", "uom", "docstatus", "name"])
    def test_item_rows_take_only_code_quantity_and_rate(self, row_field):
        rows = [{"item_code": "DESK", "qty": 1, row_field: "x"}]
        assert not wr.check_write("create", "Quotation", dict(CREATE, items=rows), ctx=user())

    @pytest.mark.parametrize("field", ["valid_till", "transaction_date"])
    @pytest.mark.parametrize("bad", ["2026-02-30", "next month", "31/12/2026", "", None])
    def test_dates_must_be_real_iso_dates(self, field, bad):
        assert not wr.check_write("create", "Quotation", dict(CREATE, **{field: bad}), ctx=user())
        if field == "valid_till":
            assert not wr.check_write("update", "Quotation", {field: bad}, ctx=user())

    def test_update_can_change_validity_and_item_rows_only(self):
        assert wr.check_write("update", "Quotation", {"valid_till": "2027-01-01"}, ctx=user())
        assert wr.check_write("update", "Quotation", {"item_updates": [{"item_code": "DESK", "qty": 3, "rate": 90}]}, ctx=user())
        for field in ("party_name", "quotation_to", "transaction_date", "company", "grand_total", "docstatus", "po_no", "delivery_date"):
            assert not wr.check_write("update", "Quotation", {field: "x"}, ctx=user()), field

    @pytest.mark.parametrize("row", [{"item_code": "A", "qty": 0}, {"item_code": "A", "rate": -1}, {"item_code": "A"}, {"item_code": "A", "qty": "x"}])
    def test_bad_item_changes_are_denied(self, row):
        assert not wr.check_write("update", "Quotation", {"item_updates": [row]}, ctx=user())

    def test_registry_facts(self):
        assert wr.get_confirm_fields("Quotation", "update") == ["item_updates"]
        assert wr.get_draft_only_fields("Quotation", "update") == ["item_updates"]
        assert set(wr.get_row_edits("Quotation", "update")) == {"item_updates"}
        assert wr.get_row_sync("Quotation", "update") == {}, "a Quotation has no header date derived from its rows"
        assert wr.get_duplicate_keys("Quotation") == ["party_name"]
        assert wr.get_duplicate_slot("Quotation", "party_name") == "customer"
        assert wr.get_duplicate_slot("Sales Order", "customer") == "customer"
        assert wr.get_duplicate_slot("Bin", "x") == "x"

    def test_quotation_is_on_the_read_whitelist(self):
        assert crud.is_doctype_allowed("Quotation")


class TestSkillValidationWithMappedSlots:
    def skill(self, **over):
        s = {
            "name": "create-quotation", "tool": "create_document", "doctype": "Quotation",
            "allowed_roles": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
            "required_fields": ["customer", "items"], "optional_fields": ["transaction_date", "valid_till"],
            "payload_structure": {
                "fixed": {"quotation_to": "Customer"},
                "field_mappings": {"party_name": "customer", "transaction_date": "transaction_date", "valid_till": "valid_till", "items": "items"},
            },
        }
        s.update(over)
        return s

    def test_a_slot_that_fills_a_different_field_is_accepted(self):
        assert wr.validate_write_skill(self.skill()) == []

    def test_an_unmapped_unknown_slot_is_still_reported(self):
        problems = wr.validate_write_skill(self.skill(optional_fields=["transaction_date", "grand_total"]))
        assert any("grand_total" in p for p in problems)

    def test_a_fixed_field_outside_the_registry_is_reported(self):
        ps = dict(self.skill()["payload_structure"], fixed={"quotation_to": "Customer", "status": "Open"})
        assert any("status" in p for p in wr.validate_write_skill(self.skill(payload_structure=ps)))

    def test_a_mapping_into_a_field_outside_the_registry_is_reported(self):
        ps = dict(self.skill()["payload_structure"])
        ps["field_mappings"] = dict(ps["field_mappings"], grand_total="customer")
        assert any("grand_total" in p for p in wr.validate_write_skill(self.skill(payload_structure=ps)))

    def test_the_shipped_quotation_skills_pass(self):
        from agent.workflow import graph

        names = {s["name"] for s in graph.loaded_skills}
        assert {"create-quotation", "update-quotation", "submit-quotation", "cancel-quotation", "quotation-lookup"} <= names


class TestCrud:
    def create(self, fields, **kw):
        client = client_with()
        with set_request_context(user()):
            res = crud.create_document("Quotation", fields, client=client,
                                       default_company_resolver=kw.get("company", lambda: "Acme Ltd"),
                                       default_warehouse_resolver=kw.get("warehouse", lambda company=None: "Stores"))
        return res, client

    def test_create_adds_the_company_and_the_date_and_nothing_else(self):
        res, client = self.create(CREATE)
        assert res["status"] == "success"
        body = client.post.call_args.kwargs["json"]
        assert client.post.call_args.args[0] == "/api/resource/Quotation"
        assert body["company"] == "Acme Ltd"
        assert body["transaction_date"]
        assert body["quotation_to"] == "Customer" and body["party_name"] == "Acme"
        assert body["items"] == [{"item_code": "DESK", "qty": 2, "rate": 100}], "no warehouse or row date is added"
        assert "delivery_date" not in body and "schedule_date" not in body and "valid_till" not in body

    def test_a_given_company_and_date_are_kept(self):
        res, client = self.create(dict(CREATE, company="Other Ltd", transaction_date="2026-10-01"))
        body = client.post.call_args.kwargs["json"]
        assert body["company"] == "Other Ltd" and body["transaction_date"] == "2026-10-01"

    def test_the_warehouse_resolver_is_never_asked_for_a_quotation(self):
        def boom(company=None):
            raise AssertionError("a Quotation does not need a warehouse")
        res, _ = self.create(CREATE, warehouse=boom)
        assert res["status"] == "success"

    def test_a_missing_default_company_is_reported_and_nothing_is_sent(self):
        def no_company():
            raise RuntimeError("Default company is not configured")
        res, client = self.create(CREATE, company=no_company)
        assert res["status"] == "system_error"
        assert "Default company is not configured" in res["error"]
        client.post.assert_not_called()

    def test_a_role_without_rights_is_refused_before_any_call(self):
        client = client_with()
        with set_request_context(user("Purchase User")):
            res = crud.create_document("Quotation", CREATE, client=client,
                                       default_company_resolver=lambda: "A", default_warehouse_resolver=lambda c=None: "W")
        assert res["status"] == "permission_denied"
        client.post.assert_not_called()

    def test_item_edit_keeps_every_row_and_changes_only_the_named_ones(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Quotation", "QTN-1", {"item_updates": [{"item_code": "DESK", "qty": 7}, {"item_code": "CHAIR", "rate": 55}]}, client=client)
        assert res["status"] == "success"
        body = client.put.call_args.kwargs["json"]
        assert [r["name"] for r in body["items"]] == ["q-a", "q-b"]
        assert (body["items"][0]["qty"], body["items"][0]["rate"]) == (7, 100)
        assert (body["items"][1]["qty"], body["items"][1]["rate"]) == (4, 55)
        assert "item_updates" not in body

    def test_validity_alone_is_a_header_only_write_with_no_read(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Quotation", "QTN-1", {"valid_till": "2027-01-01"}, client=client, expected_modified="2026-11-20 10:00:00.000001")
        assert res["status"] == "success"
        assert client.put.call_args.kwargs["json"] == {"valid_till": "2027-01-01", "modified": "2026-11-20 10:00:00.000001"}
        client.get.assert_not_called()

    def test_an_item_that_is_not_on_the_quotation_changes_nothing(self):
        client = client_with()
        with set_request_context(user()):
            res = crud.update_document("Quotation", "QTN-1", {"item_updates": [{"item_code": "SOFA", "qty": 1}]}, client=client)
        assert res["status"] == "system_error" and "SOFA is not on Quotation QTN-1" in res["error"]
        client.put.assert_not_called()

    def test_submit_and_cancel_send_the_state_and_the_version(self):
        client = client_with()
        with set_request_context(user("Sales Manager")):
            crud.submit_document("Quotation", "QTN-1", client=client, expected_modified="v1")
            assert client.put.call_args.kwargs["json"] == {"docstatus": 1, "modified": "v1"}
            crud.cancel_document("Quotation", "QTN-1", client=client, expected_modified="v2")
            assert client.put.call_args.kwargs["json"] == {"docstatus": 2, "modified": "v2"}
        with set_request_context(user("Sales User")):
            assert crud.submit_document("Quotation", "QTN-1", client=client)["status"] == "permission_denied"


class TestSearch:
    def test_a_quotation_search_asks_only_for_fields_a_quotation_has(self):
        # A Quotation has no "customer" field (it has party_name and customer_name). Asking for one
        # made ERPNext refuse the whole query ("Field not permitted in query: customer"), seen live.
        assert "customer" not in crud.DISPLAY_FIELDS_MAP["Quotation"]
        assert {"name", "party_name", "customer_name", "status"} <= set(crud.DISPLAY_FIELDS_MAP["Quotation"])

    def test_the_other_display_fields_are_unchanged(self):
        assert crud.DISPLAY_FIELDS_MAP["Sales Order"] == ["name", "customer", "status"]
        assert crud.DISPLAY_FIELDS_MAP["Purchase Order"] == ["name", "supplier", "status"]
