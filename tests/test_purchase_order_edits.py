"""
Purchase Order edits beyond the dates: the quantity and rate of items already on a Draft order.

Same mechanism as the Sales Order edits (see test_sales_order_edits.py): read the current rows,
change only the named ones, send every row back. These tests pin the Purchase Order specifics:
its own fields and dates, its roles, and that a new schedule date is not copied onto item rows.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry

MODIFIED = "2026-11-20 10:15:30.123456"


def buyer(*roles):
    return RequestContext(user_id="p@example.com", roles=roles or ("Purchase User",), read_only=False)


def current_doc():
    return {
        "name": "PO-1",
        "docstatus": 0,
        "schedule_date": "2026-11-10",
        "items": [
            {"name": "po-row-a", "idx": 1, "item_code": "ALUM", "qty": 100, "rate": 300, "schedule_date": "2026-11-10"},
            {"name": "po-row-b", "idx": 2, "item_code": "STEEL", "qty": 40, "rate": 80, "schedule_date": "2026-11-12"},
        ],
    }


def make_client(doc=None):
    client = MagicMock()
    get_resp = MagicMock(is_success=True, status_code=200)
    get_resp.json.return_value = {"data": doc if doc is not None else current_doc()}
    client.get.return_value = get_resp
    put_resp = MagicMock(is_success=True, status_code=200)
    put_resp.json.return_value = {"data": {"name": "PO-1"}}
    client.put.return_value = put_resp
    return client


def update(fields, ctx=None, client=None, **kw):
    client = client or make_client()
    with set_request_context(ctx or buyer()):
        res = crud.update_document("Purchase Order", "PO-1", fields, client=client, **kw)
    return res, client


def sent(client):
    return client.put.call_args.kwargs["json"]


class TestPurchaseOrderRowEdits:
    def test_only_the_named_row_changes_and_every_row_is_sent_back(self):
        res, client = update({"item_updates": [{"item_code": "ALUM", "qty": 150}]})
        assert res["status"] == "success"
        items = sent(client)["items"]
        assert [r["name"] for r in items] == ["po-row-a", "po-row-b"]
        assert (items[0]["qty"], items[0]["rate"]) == (150, 300)
        assert items[1] == current_doc()["items"][1]
        assert "item_updates" not in sent(client)

    def test_both_fields_and_several_rows(self):
        _, client = update({"item_updates": [{"item_code": "ALUM", "rate": 310.5}, {"item_code": "STEEL", "qty": "45", "rate": "85"}]})
        items = {r["item_code"]: r for r in sent(client)["items"]}
        assert items["ALUM"]["rate"] == 310.5 and items["ALUM"]["qty"] == 100
        assert (items["STEEL"]["qty"], items["STEEL"]["rate"]) == (45, 85)

    def test_a_new_schedule_date_is_not_copied_onto_item_rows(self):
        # Unlike a Sales Order delivery date, the Purchase Order schedule date is sent as it is.
        _, client = update({"schedule_date": "2026-12-01", "item_updates": [{"item_code": "ALUM", "qty": 150}]})
        body = sent(client)
        assert body["schedule_date"] == "2026-12-01"
        assert [r["schedule_date"] for r in body["items"]] == ["2026-11-10", "2026-11-12"]

    def test_dates_alone_do_not_read_or_send_rows(self):
        res, client = update({"schedule_date": "2026-12-01", "transaction_date": "2026-11-20"})
        assert res["status"] == "success"
        assert sent(client) == {"schedule_date": "2026-12-01", "transaction_date": "2026-11-20"}
        client.get.assert_not_called()

    def test_item_not_on_the_order_changes_nothing(self):
        res, client = update({"item_updates": [{"item_code": "COPPER", "qty": 5}]})
        assert res["status"] == "system_error"
        assert "COPPER is not on Purchase Order PO-1" in res["error"]
        client.put.assert_not_called()

    def test_item_on_several_rows_is_ambiguous(self):
        doc = current_doc()
        doc["items"].append({"name": "po-row-c", "idx": 3, "item_code": "ALUM", "qty": 1, "rate": 1})
        res, client = update({"item_updates": [{"item_code": "ALUM", "qty": 150}]}, client=make_client(doc))
        assert res["status"] == "system_error" and "2 rows" in res["error"]
        client.put.assert_not_called()

    def test_modified_travels_with_the_edit(self):
        _, client = update({"item_updates": [{"item_code": "ALUM", "qty": 150}]}, expected_modified=MODIFIED)
        assert sent(client)["modified"] == MODIFIED

    def test_current_document_is_not_changed_in_place(self):
        doc = current_doc()
        update({"item_updates": [{"item_code": "ALUM", "qty": 150}]}, client=make_client(doc))
        assert doc == current_doc()


class TestPurchaseOrderRoles:
    def test_purchase_user_may_edit_but_a_sales_user_may_not(self):
        fields = {"item_updates": [{"item_code": "ALUM", "qty": 150}]}
        assert wr.check_write("update", "Purchase Order", fields, ctx=buyer("Purchase User"))
        assert wr.check_write("update", "Purchase Order", fields, ctx=buyer("Purchase Manager"))
        res, client = update(fields, ctx=buyer("Sales User", "Sales Manager"))
        assert res["status"] == "permission_denied"
        client.put.assert_not_called()
        client.get.assert_not_called()

    def test_submit_and_cancel_roles_are_unchanged(self):
        assert not wr.check_write("submit", "Purchase Order", ctx=buyer("Purchase User"))
        assert wr.check_write("submit", "Purchase Order", ctx=buyer("Purchase Manager"))
        assert not wr.check_write("cancel", "Purchase Order", ctx=buyer("Purchase User"))


class TestPurchaseOrderRegistry:
    admin = RequestContext(user_id="a", roles=("System Manager",), read_only=False)

    def check(self, fields):
        return wr.check_write("update", "Purchase Order", fields, ctx=self.admin)

    def test_declared_fields_pass(self):
        assert self.check({"schedule_date": "2026-12-01", "transaction_date": "2026-11-20"})
        assert self.check({"item_updates": [{"item_code": "ALUM", "qty": 2, "rate": 0}]})

    @pytest.mark.parametrize("field", ["schedule_date", "transaction_date"])
    @pytest.mark.parametrize("bad", ["2026-02-30", "next Friday", "30/11/2026", "", None])
    def test_dates_must_be_real_iso_dates(self, field, bad):
        assert not self.check({field: bad})

    @pytest.mark.parametrize("row", [{"item_code": "A", "qty": 0}, {"item_code": "A", "rate": -1}, {"item_code": "A"}, {"qty": 3}, {"item_code": "A", "qty": 1, "amount": 5}])
    def test_bad_item_changes_denied(self, row):
        assert not self.check({"item_updates": [row]})

    @pytest.mark.parametrize("field", ["po_no", "po_date", "delivery_date", "supplier", "docstatus", "grand_total"])
    def test_other_fields_denied(self, field):
        assert not self.check({field: "2026-12-01" if "date" in field else "x"})

    def test_registry_facts(self):
        assert wr.get_confirm_fields("Purchase Order", "update") == ["item_updates"]
        assert wr.get_draft_only_fields("Purchase Order", "update") == ["item_updates"]
        assert set(wr.get_row_edits("Purchase Order", "update")) == {"item_updates"}
        assert wr.get_date_fields("Purchase Order", "update") == ["schedule_date", "transaction_date"]
        # Sales Order keeps its own fields and its own date list.
        assert wr.get_date_fields("Sales Order", "update") == ["delivery_date", "po_date"]
