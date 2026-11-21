"""
Sales Order to Delivery Note and Purchase Order to Purchase Receipt at the registry and tool layers.

Same mechanism as Quotation to Sales Order (see test_convert.py): ERPNext's own mapper builds the draft
under the caller's session, and the registry decides the pair, the roles and that it always asks. What is
different here: the new document is a stock document (nothing is posted until it is submitted, which is
not offered), the mapper includes only what is still pending, an order with nothing left maps to an empty
draft, and the roles are the ones ERPNext gives for creating these documents. Role lists, field names and
the empty-draft behaviour were read from a live Frappe 16.24 instance.
"""

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import set_request_context

from test_convert import client_with, resp, user

pytestmark = pytest.mark.real_write_registry

DN_METHOD = "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"
PR_METHOD = "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt"

PAIRS = [
    pytest.param("Delivery Note", "Sales Order", "SAL-ORD-1", DN_METHOD, "Delivery Note Item", "against_sales_order", "so_detail",
                 ("Stock User", "Stock Manager", "Sales User"), ("Purchase User", "Purchase Manager", "Accounts User"), id="sales-order-to-delivery-note"),
    pytest.param("Purchase Receipt", "Purchase Order", "PUR-ORD-1", PR_METHOD, "Purchase Receipt Item", "purchase_order", "purchase_order_item",
                 ("Stock User", "Stock Manager", "Purchase User"), ("Sales User", "Sales Manager", "Accounts User"), id="purchase-order-to-purchase-receipt"),
]


def mapped_stock_doc(target, source_name, link_field, detail_field):
    return {
        "doctype": target, "name": None, "docstatus": 0, "__islocal": 1, "__onload": {}, "owner": "x", "creation": None, "modified": None,
        "company": "Acme Ltd", "posting_date": "2026-11-21", "customer": "Acme Corp", "supplier": "Zuckerman",
        "items": [
            {"doctype": f"{target} Item", "item_code": "DESK", "qty": 3.0, "rate": 100.0, "warehouse": "Stores", link_field: source_name, detail_field: "row-a"},
            {"doctype": f"{target} Item", "item_code": "CHAIR", "qty": 1.0, "rate": 50.0, "warehouse": "Stores", link_field: source_name, detail_field: "row-b"},
        ],
    }


@pytest.mark.parametrize("target,source,name,method,child,link_field,detail_field,allowed,denied", PAIRS)
class TestStockDocumentConversions:
    def run(self, target, source, name, link_field, detail_field, ctx=None, mapped=None, post=None):
        mapped = mapped if mapped is not None else mapped_stock_doc(target, name, link_field, detail_field)
        client = client_with(resp(200, {"message": mapped}), post or resp(200, {"data": {"name": "NEW-1", "docstatus": 0}}))
        with set_request_context(ctx or user("Stock User")):
            res = crud.convert_document(target, source, name, client=client)
        return res, client

    def test_registry_allows_exactly_this_pair_for_the_right_roles(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        for role in allowed:
            assert wr.check_write("convert", target, {"source_doctype": source}, ctx=user(role)), role
        assert wr.check_write("convert", target, {"source_doctype": source}, ctx=user("System Manager"))
        for role in denied:
            assert not wr.check_write("convert", target, {"source_doctype": source}, ctx=user(role)), role

    def test_the_spec_names_the_mapper_the_link_and_what_counts_as_done(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        spec = wr.get_convert_spec(target, source)
        assert spec["method"] == method and spec["source_docstatus"] == 1
        assert spec["linked_by"] == {"child_doctype": child, "parent_doctype": target, "field": link_field}
        assert spec["done_percent_field"] in ("per_delivered", "per_received")
        assert spec["done_qty_field"] in ("delivered_qty", "received_qty")
        assert spec["done_label"] in ("delivered", "received")
        assert "Nothing is posted to stock" in spec["note"] and "{names}" in spec["existing_note"]
        assert wr.is_confirm_always(target, "convert") is True
        assert wr.get_identity_slot(target, "convert") in ("sales_order", "purchase_order")

    def test_the_document_can_only_be_made_this_way(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        # Creating, updating, submitting, cancelling and deleting these directly are not registered.
        for op, fields in (("create", {"customer": "A"}), ("update", {"posting_date": "2026-11-21"}), ("cancel", None), ("delete", None)):
            assert not wr.check_write(op, target, fields, ctx=user("System Manager", "Stock Manager")), op

    def test_it_is_a_draft_made_by_the_registered_mapper_with_the_link_kept(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field)
        assert res["status"] == "success" and res["data"]["name"] == "NEW-1"
        assert client.get.call_args.args[0] == f"/api/method/{method}"
        assert client.get.call_args.kwargs["params"] == {"source_name": name}
        assert client.post.call_args.args[0] == f"/api/resource/{target}"
        body = client.post.call_args.kwargs["json"]
        assert body["docstatus"] == 0 and body["doctype"] == target
        assert not [k for k in body if k.startswith("__")] and "owner" not in body and "name" not in body
        assert all(r[link_field] == name and r[detail_field] for r in body["items"])
        assert [(r["item_code"], r["qty"]) for r in body["items"]] == [("DESK", 3.0), ("CHAIR", 1.0)]

    def test_no_delivery_or_schedule_date_is_invented(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        # Only an order needs a date made up; these documents carry their own posting date.
        _, client = self.run(target, source, name, link_field, detail_field)
        body = client.post.call_args.kwargs["json"]
        assert "delivery_date" not in body and "schedule_date" not in body
        assert body["posting_date"] == "2026-11-21"

    def test_a_fully_done_order_gives_an_empty_draft_and_nothing_is_saved(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        mapped = mapped_stock_doc(target, name, link_field, detail_field)
        mapped["items"] = []
        res, client = self.run(target, source, name, link_field, detail_field, mapped=mapped)
        assert res["status"] == "system_error" and f"Nothing is left to convert from {source} {name}" in res["error"]
        client.post.assert_not_called()

    def test_the_wrong_roles_are_refused_before_any_call(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field, ctx=user(denied[0]))
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_the_other_pair_is_not_accepted_for_this_target(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        other_source = "Purchase Order" if source == "Sales Order" else "Sales Order"
        res, client = self.run(target, other_source, name, link_field, detail_field, ctx=user("System Manager"))
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()

    def test_a_refusal_when_saving_is_reported_and_nothing_is_retried(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field,
                               post=resp(417, {"exc_type": "ValidationError", "exception": "frappe.exceptions.ValidationError: Warehouse is required"}))
        assert res["status"] == "system_error" and "Warehouse is required" in res["error"]
        assert client.post.call_count == 1


@pytest.mark.parametrize("doctype,manager,others", [
    ("Delivery Note", "Stock Manager", ("Stock User", "Sales User", "Sales Manager", "Accounts Manager")),
    ("Purchase Receipt", "Stock Manager", ("Stock User", "Purchase User", "Purchase Manager", "Accounts Manager")),
])
def test_only_the_stock_manager_may_submit_and_nobody_may_cancel_from_chat(doctype, manager, others):
    assert wr.check_write("submit", doctype, None, ctx=user(manager))
    assert wr.check_write("submit", doctype, None, ctx=user("System Manager"))
    for role in others:
        assert not wr.check_write("submit", doctype, None, ctx=user(role)), role
    assert not wr.check_write("cancel", doctype, None, ctx=user(manager, "System Manager"))
