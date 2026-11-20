"""
Sales Order edits beyond the delivery date: customer PO reference, and the quantity and
rate of items that are already on the order.

ERPNext replaces a child table with whatever rows it is sent, so an item edit must read the
current rows, change only the named ones and send every row back. These tests pin that.
"""

import copy
from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry

MODIFIED = "2026-11-20 10:15:30.123456"


def sales_user():
    return RequestContext(user_id="s@example.com", roles=("Sales User",), read_only=False)


def current_doc():
    return {
        "name": "SO-1",
        "docstatus": 0,
        "delivery_date": "2026-11-01",
        "items": [
            {"name": "row-a", "idx": 1, "item_code": "DESK", "qty": 2, "rate": 100, "delivery_date": "2026-11-01"},
            {"name": "row-b", "idx": 2, "item_code": "CHAIR", "qty": 4, "rate": 50, "delivery_date": "2026-11-01"},
            {"name": "row-c", "idx": 3, "item_code": "LAMP", "qty": 1, "rate": 20, "delivery_date": "2026-11-01"},
        ],
    }


def make_client(doc=None, put_ok=True):
    client = MagicMock()
    get_resp = MagicMock(is_success=True, status_code=200)
    get_resp.json.return_value = {"data": doc if doc is not None else current_doc()}
    client.get.return_value = get_resp
    put_resp = MagicMock(is_success=put_ok, status_code=200 if put_ok else 500)
    put_resp.json.return_value = {"data": {"name": "SO-1"}}
    client.put.return_value = put_resp
    return client


def update(fields, client=None, **kw):
    client = client or make_client()
    with set_request_context(sales_user()):
        res = crud.update_document("Sales Order", "SO-1", fields, client=client, **kw)
    return res, client


def sent(client):
    return client.put.call_args.kwargs["json"]


class TestRowEdits:
    def test_only_the_named_row_changes_and_every_row_is_sent_back(self):
        res, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]})
        assert res["status"] == "success"
        items = sent(client)["items"]
        assert [r["item_code"] for r in items] == ["DESK", "CHAIR", "LAMP"]
        assert items[0]["qty"] == 5 and items[0]["rate"] == 100
        assert items[1] == current_doc()["items"][1]
        assert items[2] == current_doc()["items"][2]

    def test_rows_keep_their_names_so_erpnext_updates_them_in_place(self):
        _, client = update({"item_updates": [{"item_code": "CHAIR", "rate": 55.5}]})
        items = sent(client)["items"]
        assert [r["name"] for r in items] == ["row-a", "row-b", "row-c"]
        assert items[1]["rate"] == 55.5

    def test_several_rows_and_both_fields(self):
        _, client = update(
            {"item_updates": [{"item_code": "DESK", "qty": 3, "rate": 90}, {"item_code": "LAMP", "qty": 10}]}
        )
        items = {r["item_code"]: r for r in sent(client)["items"]}
        assert (items["DESK"]["qty"], items["DESK"]["rate"]) == (3, 90)
        assert (items["LAMP"]["qty"], items["LAMP"]["rate"]) == (10, 20)
        assert items["CHAIR"]["qty"] == 4

    def test_numeric_strings_become_numbers(self):
        _, client = update({"item_updates": [{"item_code": "DESK", "qty": "7", "rate": "12.50"}]})
        row = sent(client)["items"][0]
        assert row["qty"] == 7 and row["rate"] == 12.5
        assert isinstance(row["qty"], int)

    def test_the_edit_field_itself_is_not_sent_to_erpnext(self):
        _, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]})
        assert "item_updates" not in sent(client)

    def test_the_current_document_is_not_changed_in_place(self):
        doc = current_doc()
        original = copy.deepcopy(doc)
        update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, client=make_client(doc))
        assert doc == original

    def test_item_not_on_the_order_changes_nothing(self):
        res, client = update({"item_updates": [{"item_code": "SOFA", "qty": 5}]})
        assert res["status"] == "system_error"
        assert "SOFA is not on Sales Order SO-1" in res["error"]
        client.put.assert_not_called()

    def test_item_on_several_rows_is_ambiguous_and_changes_nothing(self):
        doc = current_doc()
        doc["items"].append({"name": "row-d", "idx": 4, "item_code": "DESK", "qty": 9, "rate": 100})
        res, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, client=make_client(doc))
        assert res["status"] == "system_error"
        assert "2 rows" in res["error"] and "unclear which to change" in res["error"]
        client.put.assert_not_called()

    def test_one_bad_item_stops_the_whole_update(self):
        res, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}, {"item_code": "SOFA", "qty": 1}]})
        assert res["status"] == "system_error"
        client.put.assert_not_called()

    def test_an_order_without_item_rows_is_refused(self):
        res, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, client=make_client({"name": "SO-1", "items": []}))
        assert res["status"] == "system_error"
        assert "no item rows" in res["error"]
        client.put.assert_not_called()

    def test_unreadable_document_is_refused_not_guessed(self):
        client = make_client()
        client.get.return_value = MagicMock(is_success=False, status_code=500, json=MagicMock(return_value={"message": "boom"}), text="boom")
        res, _ = update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, client=client)
        assert res["status"] == "system_error"
        assert "Cannot apply item changes to Sales Order SO-1" in res["error"]
        client.put.assert_not_called()

    def test_permission_error_on_the_read_is_reported_as_permission_denied(self):
        client = make_client()
        client.get.return_value = MagicMock(is_success=False, status_code=403, json=MagicMock(return_value={"message": "no"}), text="no")
        res, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, client=client)
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()

    def test_modified_travels_with_the_edit(self):
        _, client = update({"item_updates": [{"item_code": "DESK", "qty": 5}]}, expected_modified=MODIFIED)
        assert sent(client)["modified"] == MODIFIED


class TestCombinedWithOtherFields:
    def test_delivery_date_and_item_edit_in_one_write(self):
        _, client = update(
            {"delivery_date": "2026-12-01", "item_updates": [{"item_code": "DESK", "qty": 5}]}
        )
        body = sent(client)
        assert body["delivery_date"] == "2026-12-01"
        assert all(r["delivery_date"] == "2026-12-01" for r in body["items"])
        assert body["items"][0]["qty"] == 5

    def test_po_reference_alone_does_not_read_or_send_rows(self):
        res, client = update({"po_no": "PO-8841", "po_date": "2026-11-15"})
        assert res["status"] == "success"
        assert sent(client) == {"po_no": "PO-8841", "po_date": "2026-11-15"}
        client.get.assert_not_called()

    def test_delivery_date_alone_still_syncs_every_row(self):
        _, client = update({"delivery_date": "2026-12-01"})
        assert all(r["delivery_date"] == "2026-12-01" for r in sent(client)["items"])
        assert len(sent(client)["items"]) == 3


class TestRegistryRules:
    admin = RequestContext(user_id="a", roles=("System Manager",), read_only=False)

    def check(self, fields):
        return wr.check_write("update", "Sales Order", fields, ctx=self.admin)

    def test_declared_fields_pass(self):
        assert self.check({"po_no": "PO-1", "po_date": "2026-11-15", "delivery_date": "2026-12-01"})
        assert self.check({"item_updates": [{"item_code": "A", "qty": 2, "rate": 0}]})

    @pytest.mark.parametrize("bad", ["2026-13-01", "2026-02-30", "01/12/2026", "tomorrow", "2026-1-5", "", None, 20261201])
    def test_dates_must_be_real_iso_dates(self, bad):
        assert not self.check({"delivery_date": bad})
        assert not self.check({"po_date": bad})

    @pytest.mark.parametrize(
        "row",
        [
            {"item_code": "A"},
            {"qty": 2},
            {"item_code": "", "qty": 2},
            {"item_code": 5, "qty": 2},
            {"item_code": "A", "qty": 0},
            {"item_code": "A", "qty": -1},
            {"item_code": "A", "qty": "lots"},
            {"item_code": "A", "qty": True},
            {"item_code": "A", "qty": float("nan")},
            {"item_code": "A", "qty": float("inf")},
            {"item_code": "A", "rate": -0.01},
            {"item_code": "A", "rate": "free"},
            {"item_code": "A", "qty": 1, "amount": 99},
            {"item_code": "A", "qty": 1, "docstatus": 1},
            {"item_code": "A", "qty": 1, "name": "row-x"},
            {"item_code": "A", "qty": 1, "warehouse": "W"},
        ],
    )
    def test_bad_item_changes_are_denied(self, row):
        assert not self.check({"item_updates": [row]})

    def test_item_updates_must_be_a_non_empty_list_of_limited_size(self):
        assert not self.check({"item_updates": []})
        assert not self.check({"item_updates": "DESK"})
        assert not self.check({"item_updates": {"item_code": "A", "qty": 1}})
        assert not self.check({"item_updates": [{"item_code": f"I{i}", "qty": 1} for i in range(wr.MAX_ROW_EDITS + 1)]})
        assert self.check({"item_updates": [{"item_code": f"I{i}", "qty": 1} for i in range(wr.MAX_ROW_EDITS)]})

    def test_the_same_item_twice_is_denied(self):
        assert not self.check({"item_updates": [{"item_code": "A", "qty": 1}, {"item_code": "A", "qty": 2}]})

    def test_zero_rate_is_allowed_but_zero_quantity_is_not(self):
        assert self.check({"item_updates": [{"item_code": "A", "rate": 0}]})
        assert not self.check({"item_updates": [{"item_code": "A", "qty": 0}]})

    def test_other_fields_are_still_denied(self):
        for field in ("customer", "grand_total", "status", "docstatus", "items"):
            assert not self.check({field: "x" if field != "items" else [{"item_code": "A", "qty": 1}]}), field

    def test_registry_reports_what_needs_a_yes_and_what_is_draft_only(self):
        assert wr.get_confirm_fields("Sales Order", "update") == ["item_updates"]
        assert wr.get_draft_only_fields("Sales Order", "update") == ["item_updates"]
        assert wr.get_confirm_fields("Purchase Order", "update") == []
        assert set(wr.get_row_edits("Sales Order", "update")) == {"item_updates"}
        assert wr.get_row_edits("Purchase Order", "update") == {}

    def test_purchase_order_update_is_unchanged(self):
        assert wr.check_write("update", "Purchase Order", {"schedule_date": "2026-12-01"}, ctx=self.admin)
        assert not wr.check_write("update", "Purchase Order", {"po_no": "X"}, ctx=self.admin)
        assert not wr.check_write("update", "Purchase Order", {"item_updates": [{"item_code": "A", "qty": 1}]}, ctx=self.admin)

    def test_roles_for_the_widened_update_are_unchanged(self):
        assert wr.check_write("update", "Sales Order", {"po_no": "X"}, ctx=RequestContext(user_id="u", roles=("Sales User",), read_only=False))
        assert not wr.check_write("update", "Sales Order", {"po_no": "X"}, ctx=RequestContext(user_id="u", roles=("Purchase User",), read_only=False))

    def test_to_number(self):
        assert wr.to_number("5") == 5 and isinstance(wr.to_number("5"), int)
        assert wr.to_number(" 2.5 ") == 2.5
        assert wr.to_number(3.0) == 3 and isinstance(wr.to_number(3.0), int)
        for bad in (True, False, None, "", "  ", "x", float("nan"), float("inf"), [1], {"a": 1}):
            assert wr.to_number(bad) is None, bad


class TestSanitizeKeepsEdits:
    def test_item_updates_rows_are_reduced_to_declared_keys(self):
        clean, dropped = wr.sanitize_payload(
            "Sales Order", "update", {"item_updates": [{"item_code": "A", "qty": 1, "amount": 9, "docstatus": 1}], "status": "x"}
        )
        assert clean == {"item_updates": [{"item_code": "A", "qty": 1}]}
        assert "status" in dropped and "item_updates[].amount" in dropped
