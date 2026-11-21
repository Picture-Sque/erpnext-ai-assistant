"""
Converting only some of the items, or fewer units, of a source document.

ERPNext's mapper still builds the draft; the conversation can only REDUCE it. A named item must match
exactly one mapped row, a quantity cannot exceed what ERPNext mapped (the pending quantity), a row not
named is left out, and nothing can be added. The rules live in the registry so every caller gets them.
"""

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import set_request_context

from test_convert import client_with, mapped_order, resp, user

pytestmark = pytest.mark.real_write_registry


def run(items, mapped=None, target="Sales Order", source="Quotation", name="SAL-QTN-1", ctx=None):
    client = client_with(mapper_response=resp(200, {"message": mapped}) if mapped else None)
    with set_request_context(ctx or user()):
        res = crud.convert_document(target, source, name, client=client, items=items)
    return res, client


def posted_rows(client):
    return [(r["item_code"], r["qty"]) for r in client.post.call_args.kwargs["json"]["items"]]


class TestRegistryRows:
    @pytest.mark.parametrize("doctype", ["Sales Order", "Delivery Note", "Purchase Receipt"])
    def test_every_conversion_offers_the_items_slot(self, doctype):
        assert wr.get_partial_rows(doctype) == {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"}

    def test_a_doctype_without_conversion_has_none(self):
        assert wr.get_partial_rows("Customer") is None

    @pytest.mark.parametrize("rows", [
        [{"item_code": "DESK", "qty": 2}],
        [{"item_code": "DESK", "qty": "2"}, {"item_code": "CHAIR", "qty": 0.5}],
    ])
    def test_well_formed_rows_pass(self, rows):
        assert wr.check_partial_rows("Sales Order", rows)

    @pytest.mark.parametrize("rows", [
        [], None, "DESK", {"item_code": "DESK", "qty": 1},
        [{"item_code": "DESK"}], [{"qty": 1}], [{"item_code": "", "qty": 1}], [{"item_code": 5, "qty": 1}],
        [{"item_code": "DESK", "qty": 0}], [{"item_code": "DESK", "qty": -1}], [{"item_code": "DESK", "qty": "many"}],
        [{"item_code": "DESK", "qty": True}], [{"item_code": "DESK", "qty": float("nan")}],
        [{"item_code": "DESK", "qty": 1, "rate": 1}], [{"item_code": "DESK", "qty": 1}, {"item_code": "DESK", "qty": 2}],
        ["DESK"], [{"item_code": "I%d" % i, "qty": 1} for i in range(wr.MAX_ROW_EDITS + 1)],
    ])
    def test_bad_rows_are_refused(self, rows):
        assert not wr.check_partial_rows("Sales Order", rows)

    def test_check_write_refuses_bad_rows_and_keeps_the_role_rule(self):
        assert wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation", "items": [{"item_code": "DESK", "qty": 1}]}, ctx=user("Sales User"))
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation", "items": [{"item_code": "DESK", "qty": 0}]}, ctx=user("Sales User"))
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation", "items": [{"item_code": "DESK", "qty": 1}]}, ctx=user("Accounts User"))


class TestCrudPartial:
    def test_only_the_named_item_with_the_lower_quantity_is_saved(self):
        res, client = run([{"item_code": "DESK", "qty": 2}])
        assert res["status"] == "success"
        assert posted_rows(client) == [("DESK", 2.0)]

    def test_the_kept_row_still_carries_the_source_link(self):
        _, client = run([{"item_code": "CHAIR", "qty": 1}])
        row = client.post.call_args.kwargs["json"]["items"][0]
        assert row["prevdoc_docname"] == "SAL-QTN-1" and row["quotation_item"] == "row-b" and row["rate"] == 50.0

    def test_the_full_pending_quantity_is_allowed(self):
        res, client = run([{"item_code": "DESK", "qty": 4}, {"item_code": "CHAIR", "qty": 2}])
        assert res["status"] == "success" and posted_rows(client) == [("DESK", 4.0), ("CHAIR", 2.0)]

    def test_the_order_of_the_request_is_kept(self):
        _, client = run([{"item_code": "CHAIR", "qty": 1}, {"item_code": "DESK", "qty": 1}])
        assert posted_rows(client) == [("CHAIR", 1.0), ("DESK", 1.0)]

    def test_no_items_means_everything_ERPNext_mapped(self):
        _, client = run(None)
        assert posted_rows(client) == [("DESK", 4.0), ("CHAIR", 2.0)]

    def test_a_quantity_above_the_mapped_one_is_refused_and_nothing_is_posted(self):
        res, client = run([{"item_code": "DESK", "qty": 5}])
        assert res["status"] == "system_error" and "only 4 is left" in res["error"]
        client.post.assert_not_called()

    def test_an_item_that_was_not_mapped_cannot_be_added(self):
        res, client = run([{"item_code": "SOFA", "qty": 1}])
        assert res["status"] == "system_error" and "SOFA is not among the items left" in res["error"]
        client.post.assert_not_called()

    def test_an_item_on_two_rows_is_ambiguous(self):
        doc = mapped_order()
        doc["items"].append(dict(doc["items"][0], quotation_item="row-c"))
        res, client = run([{"item_code": "DESK", "qty": 1}], mapped=doc)
        assert res["status"] == "system_error" and "more than one row" in res["error"]
        client.post.assert_not_called()

    def test_bad_rows_are_refused_before_any_call(self):
        res, client = run([{"item_code": "DESK", "qty": -3}])
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_the_role_rule_still_applies(self):
        res, client = run([{"item_code": "DESK", "qty": 1}], ctx=user("Accounts User"))
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()

    def test_the_mapped_document_is_not_mutated_by_the_selection(self):
        doc = mapped_order()
        run([{"item_code": "DESK", "qty": 1}], mapped=doc)
        assert [r["qty"] for r in doc["items"]] == [4.0, 2.0]

    def test_an_empty_mapped_document_still_says_nothing_is_left(self):
        res, _ = run([{"item_code": "DESK", "qty": 1}], mapped=mapped_order(items=[]))
        assert "Nothing is left to convert" in res["error"]
