"""
Sales Invoice from a Sales Order and Purchase Invoice from a Purchase Order, as Drafts only.

Same mechanism as the other conversions (ERPNext's mapper builds the draft under the caller's session; the
registry decides the pair, the roles and that it always asks). What differs: an invoice posts to ACCOUNTS when
submitted, so submit and cancel are deliberately not registered (who may do that from chat is a compliance
decision), and "how much is done" is the billed percentage on the order. The mapper method names and link
fields below are ERPNext's; they have NOT yet been run against the live instance (see WRITE_ACCESS_PLAN 6m).
"""

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import set_request_context

from test_convert import client_with, resp, user

pytestmark = pytest.mark.real_write_registry

PAIRS = [
    pytest.param("Sales Invoice", "Sales Order", "SAL-ORD-1", "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
                 "Sales Invoice Item", "sales_order", "so_detail", ("Accounts User", "Accounts Manager", "Sales User"),
                 ("Stock User", "Purchase User", "Purchase Manager"), id="sales-order-to-sales-invoice"),
    pytest.param("Purchase Invoice", "Purchase Order", "PUR-ORD-1", "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
                 "Purchase Invoice Item", "purchase_order", "po_detail", ("Accounts User", "Accounts Manager", "Purchase User"),
                 ("Stock User", "Sales User", "Sales Manager"), id="purchase-order-to-purchase-invoice"),
]


def mapped_invoice(target, name, link_field, detail_field):
    return {
        "doctype": target, "name": None, "docstatus": 0, "__islocal": 1, "owner": "x", "creation": None, "modified": None,
        "company": "Acme Ltd", "posting_date": "2026-11-21", "customer": "Acme Corp", "supplier": "Zuckerman",
        "items": [
            {"doctype": f"{target} Item", "item_code": "DESK", "qty": 3.0, "rate": 100.0, link_field: name, detail_field: "row-a"},
            {"doctype": f"{target} Item", "item_code": "CHAIR", "qty": 1.0, "rate": 50.0, link_field: name, detail_field: "row-b"},
        ],
    }


@pytest.mark.parametrize("target,source,name,method,child,link_field,detail_field,allowed,denied", PAIRS)
class TestInvoiceConversions:
    def run(self, target, source, name, link_field, detail_field, ctx=None, mapped=None, items=None, post=None):
        mapped = mapped if mapped is not None else mapped_invoice(target, name, link_field, detail_field)
        client = client_with(resp(200, {"message": mapped}), post or resp(200, {"data": {"name": "NEW-1", "docstatus": 0}}))
        with set_request_context(ctx or user("Accounts User")):
            res = crud.convert_document(target, source, name, client=client, **({"items": items} if items is not None else {}))
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
        assert spec["done_percent_field"] == "per_billed" and spec["done_label"] == "billed"
        assert "Nothing is posted to accounts" in spec["note"] and "{names}" in spec["existing_note"]
        assert wr.is_confirm_always(target, "convert") is True
        assert wr.get_partial_rows(target)["slot"] == "items"

    def test_the_invoice_can_only_be_made_this_way_and_never_submitted_from_chat(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        for op, fields in (("create", {"customer": "A"}), ("update", {"posting_date": "2026-11-21"}), ("cancel", None), ("delete", None)):
            assert not wr.check_write(op, target, fields, ctx=user("System Manager", "Accounts Manager")), op

    def test_it_is_a_draft_made_by_the_registered_mapper_with_the_link_kept(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field)
        assert res["status"] == "success"
        assert client.get.call_args.args[0] == f"/api/method/{method}"
        assert client.get.call_args.kwargs["params"] == {"source_name": name}
        body = client.post.call_args.kwargs["json"]
        assert client.post.call_args.args[0] == f"/api/resource/{target}"
        assert body["docstatus"] == 0 and body["doctype"] == target and "name" not in body and not [k for k in body if k.startswith("__")]
        assert all(r[link_field] == name and r[detail_field] for r in body["items"])

    def test_a_partial_invoice_keeps_only_the_named_row_and_quantity(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        _, client = self.run(target, source, name, link_field, detail_field, items=[{"item_code": "DESK", "qty": 2}])
        rows = client.post.call_args.kwargs["json"]["items"]
        assert [(r["item_code"], r["qty"], r[link_field]) for r in rows] == [("DESK", 2.0, name)]

    def test_more_than_what_is_left_is_refused(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field, items=[{"item_code": "DESK", "qty": 4}])
        assert res["status"] == "system_error" and "only 3 is left" in res["error"]
        client.post.assert_not_called()

    def test_a_fully_billed_order_gives_an_empty_draft_and_nothing_is_saved(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        mapped = mapped_invoice(target, name, link_field, detail_field)
        mapped["items"] = []
        res, client = self.run(target, source, name, link_field, detail_field, mapped=mapped)
        assert res["status"] == "system_error" and "Nothing is left to convert" in res["error"]
        client.post.assert_not_called()

    def test_the_wrong_roles_and_the_other_source_are_refused_before_any_call(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field, ctx=user(denied[0]))
        assert res["status"] == "permission_denied"
        other = "Purchase Order" if source == "Sales Order" else "Sales Order"
        res2, client2 = self.run(target, other, name, link_field, detail_field, ctx=user("System Manager"))
        assert res2["status"] == "permission_denied"
        client.get.assert_not_called()
        client2.get.assert_not_called()

    def test_a_refusal_when_saving_is_reported_and_not_retried(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        res, client = self.run(target, source, name, link_field, detail_field,
                               post=resp(417, {"exc_type": "ValidationError", "exception": "frappe.exceptions.ValidationError: Debit To is required"}))
        assert res["status"] == "system_error" and "Debit To is required" in res["error"]
        assert client.post.call_count == 1

    def test_the_child_and_parent_doctypes_may_be_read_for_the_existing_documents_note(self, target, source, name, method, child, link_field, detail_field, allowed, denied):
        assert crud.is_doctype_allowed(child) and crud.is_doctype_allowed(target)


@pytest.mark.parametrize("doctype,others", [
    ("Sales Invoice", ("Accounts User", "Sales User", "Sales Manager", "Stock Manager")),
    ("Purchase Invoice", ("Accounts User", "Purchase User", "Purchase Manager", "Stock Manager")),
])
def test_only_the_accounts_manager_may_submit_and_nobody_may_cancel_from_chat(doctype, others):
    assert wr.check_write("submit", doctype, None, ctx=user("Accounts Manager"))
    assert wr.check_write("submit", doctype, None, ctx=user("System Manager"))
    for role in others:
        assert not wr.check_write("submit", doctype, None, ctx=user(role)), role
    assert not wr.check_write("cancel", doctype, None, ctx=user("Accounts Manager", "System Manager"))
