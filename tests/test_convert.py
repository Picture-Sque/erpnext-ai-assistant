"""
Creating a document from another (Quotation to Sales Order) at the registry and tool layers.

The draft is produced by ERPNext's own mapping method under the caller's session, so what it contains
is ERPNext's business rule, not something the conversation chose. The registry decides which pairs are
allowed and who may do it. Behaviour of the mapper (needs a submitted source; the mapped order is
refused for a missing delivery date; the quotation is kept on each order row) was seen live on
Frappe 16.24.
"""

import copy
from unittest.mock import MagicMock

import pytest

from mcp_erpnext import crud, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context

pytestmark = pytest.mark.real_write_registry

METHOD = "erpnext.selling.doctype.quotation.quotation.make_sales_order"


def user(*roles, read_only=False):
    return RequestContext(user_id="u@example.com", roles=roles or ("Sales User",), read_only=read_only)


def mapped_order(**over):
    doc = {
        "doctype": "Sales Order", "name": None, "docstatus": 0, "__islocal": 1, "__unsaved": 1, "__onload": {"x": 1},
        "owner": "someone@example.com", "creation": None, "modified": None, "modified_by": None, "idx": 0,
        "customer": "Acme Corp", "customer_name": "Acme Corp", "company": "Acme Ltd", "transaction_date": "2026-11-21",
        "order_type": "Sales", "currency": "INR", "selling_price_list": "Standard Selling", "delivery_date": None,
        "grand_total": 500.0, "taxes": [],
        "items": [
            {"doctype": "Sales Order Item", "item_code": "DESK", "qty": 4.0, "rate": 100.0, "warehouse": "Stores",
             "prevdoc_docname": "SAL-QTN-1", "quotation_item": "row-a", "delivery_date": None},
            {"doctype": "Sales Order Item", "item_code": "CHAIR", "qty": 2.0, "rate": 50.0, "warehouse": "Stores",
             "prevdoc_docname": "SAL-QTN-1", "quotation_item": "row-b"},
        ],
    }
    doc.update(over)
    return doc


def resp(status=200, body=None, ok=None):
    r = MagicMock(status_code=status, is_success=(200 <= status < 300) if ok is None else ok)
    r.json.return_value = body if body is not None else {}
    r.text = str(body)
    return r


def client_with(mapper_response=None, post_response=None):
    client = MagicMock()
    client.get.return_value = mapper_response or resp(200, {"message": mapped_order()})
    client.post.return_value = post_response or resp(200, {"data": {"name": "SAL-ORD-1", "docstatus": 0}})
    return client


def convert(client=None, source="Quotation", name="SAL-QTN-1", ctx=None, target="Sales Order"):
    client = client or client_with()
    with set_request_context(ctx or user()):
        res = crud.convert_document(target, source, name, client=client)
    return res, client


class TestRegistry:
    def test_a_sales_order_may_be_made_from_a_quotation(self):
        assert wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user("Sales User"))
        assert wr.check_write("convert", "Sales Order", {"source_doctype": "quotation"}, ctx=user("Sales Manager"))
        assert wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user("System Manager"))

    @pytest.mark.parametrize("roles", [("Purchase User",), ("Purchase Manager",), ("Stock Manager",), ("Accounts User",), ("Stock User",)])
    def test_other_roles_may_not(self, roles):
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user(*roles))

    @pytest.mark.parametrize("source", ["Purchase Order", "Sales Order", "Customer", "", None, 5, "Quotation Item"])
    def test_only_registered_sources_are_allowed(self, source):
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": source}, ctx=user())

    def test_a_missing_or_malformed_source_is_refused(self):
        assert not wr.check_write("convert", "Sales Order", None, ctx=user())
        assert not wr.check_write("convert", "Sales Order", {}, ctx=user())
        assert not wr.check_write("convert", "Sales Order", "Quotation", ctx=user())

    @pytest.mark.parametrize("target", ["Quotation", "Purchase Order", "Customer", "Supplier", "Item", "Bin", "Delivery Note", "Purchase Receipt"])
    def test_a_quotation_can_only_become_a_sales_order(self, target):
        # Delivery Note and Purchase Receipt are convertible, but only from a Sales Order and a Purchase Order.
        assert not wr.check_write("convert", target, {"source_doctype": "Quotation"}, ctx=user("System Manager"))

    @pytest.mark.parametrize("target", ["Quotation", "Purchase Order", "Customer", "Supplier", "Item", "Bin", "Sales Invoice", "Purchase Invoice", "Stock Entry"])
    def test_nothing_else_can_be_converted_into_yet(self, target):
        for source in ("Quotation", "Sales Order", "Purchase Order", "Delivery Note", "Purchase Receipt"):
            assert not wr.check_write("convert", target, {"source_doctype": source}, ctx=user("System Manager")), (target, source)

    def test_a_read_only_session_cannot_convert(self):
        d = wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user("System Manager", read_only=True))
        assert not d and "read-only" in d.public_message

    def test_the_emergency_switch_blocks_it(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_WRITES_DISABLED", "true")
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user("System Manager"))

    def test_registry_facts(self):
        assert "convert" in wr.WRITE_OPERATIONS
        assert wr.TOOL_TO_OPERATION["convert_document"] == "convert"
        assert wr.is_confirm_always("Sales Order", "convert") is True
        assert wr.get_identity_slot("Sales Order", "convert") == "quotation"
        spec = wr.get_convert_spec("Sales Order", "quotation")
        assert spec["method"] == METHOD and spec["source_docstatus"] == 1 and spec["source_doctype"] == "Quotation"
        assert spec["linked_by"] == {"child_doctype": "Sales Order Item", "parent_doctype": "Sales Order", "field": "prevdoc_docname"}
        assert wr.get_convert_spec("Sales Order", "Purchase Order") is None
        assert wr.get_convert_spec("Purchase Order", "Quotation") is None
        assert wr.get_convert_sources("Customer") == {}

    def test_a_source_with_a_malformed_method_is_not_usable(self, monkeypatch):
        reg = copy.deepcopy(wr.WRITE_REGISTRY)
        reg["Sales Order"]["operations"]["convert"]["sources"]["Quotation"]["method"] = "os.system('x')"
        monkeypatch.setattr(wr, "WRITE_REGISTRY", reg)
        assert wr.get_convert_spec("Sales Order", "Quotation") is None
        assert not wr.check_write("convert", "Sales Order", {"source_doctype": "Quotation"}, ctx=user())

    def test_convert_is_not_something_a_create_or_update_can_smuggle_in(self):
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "items": [], "prevdoc_docname": "SAL-QTN-1"}, ctx=user())
        assert not wr.check_write("create", "Sales Order", {"customer": "A", "sources": {}}, ctx=user())


class TestSkillValidation:
    def skill(self, **over):
        s = {
            "name": "convert-quotation-to-sales-order", "tool": "convert_document", "doctype": "Sales Order", "source_doctype": "Quotation",
            "allowed_roles": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
            "required_fields": ["quotation"], "optional_fields": [], "payload_structure": {},
        }
        s.update(over)
        return s

    def test_a_consistent_conversion_skill_has_no_problems(self):
        assert wr.validate_write_skill(self.skill()) == []

    def test_an_unregistered_source_is_reported(self):
        assert any("Purchase Order" in p for p in wr.validate_write_skill(self.skill(source_doctype="Purchase Order")))
        assert wr.validate_write_skill(self.skill(source_doctype=""))

    def test_the_skill_must_ask_for_the_quotation(self):
        assert any("quotation" in p and "must require" in p for p in wr.validate_write_skill(self.skill(required_fields=["name"])))

    def test_extra_roles_are_reported(self):
        assert any("Purchase User" in p for p in wr.validate_write_skill(self.skill(allowed_roles=["Sales User", "Purchase User"])))

    def test_the_shipped_skill_loads(self):
        from agent.workflow import graph

        skill = next(s for s in graph.loaded_skills if s["name"] == "convert-quotation-to-sales-order")
        assert skill["tool"] == "convert_document" and skill["doctype"] == "Sales Order" and skill["source_doctype"] == "Quotation"


class TestCrudConvert:
    def test_it_asks_the_registered_mapper_for_the_given_source_and_nothing_else(self):
        res, client = convert()
        assert res["status"] == "success" and res["data"]["name"] == "SAL-ORD-1"
        assert client.get.call_args.args[0] == f"/api/method/{METHOD}"
        assert client.get.call_args.kwargs["params"] == {"source_name": "SAL-QTN-1"}

    def test_the_saved_draft_has_no_client_markers_or_server_owned_fields(self):
        _, client = convert()
        assert client.post.call_args.args[0] == "/api/resource/Sales Order"
        body = client.post.call_args.kwargs["json"]
        assert not [k for k in body if k.startswith("__")]
        for field in ("name", "owner", "creation", "modified", "modified_by", "idx"):
            assert field not in body
        assert body["docstatus"] == 0 and body["doctype"] == "Sales Order"

    def test_what_erpnext_mapped_is_kept(self):
        _, client = convert()
        body = client.post.call_args.kwargs["json"]
        assert body["customer"] == "Acme Corp" and body["company"] == "Acme Ltd" and body["currency"] == "INR"
        assert [r["item_code"] for r in body["items"]] == ["DESK", "CHAIR"]
        assert [(r["qty"], r["rate"]) for r in body["items"]] == [(4.0, 100.0), (2.0, 50.0)]
        assert all(r["prevdoc_docname"] == "SAL-QTN-1" for r in body["items"]), "the link back to the quotation is kept"
        assert [r["quotation_item"] for r in body["items"]] == ["row-a", "row-b"]

    def test_the_missing_delivery_date_is_filled_on_the_header_and_every_row(self):
        # ERPNext refuses the mapped order as it comes ("Please enter Delivery Date"), seen live.
        _, client = convert()
        body = client.post.call_args.kwargs["json"]
        assert body["delivery_date"]
        assert all(r["delivery_date"] == body["delivery_date"] for r in body["items"])

    def test_a_delivery_date_the_mapper_did_supply_is_kept(self):
        mapped = mapped_order(delivery_date="2026-12-15")
        mapped["items"][0]["delivery_date"] = "2026-12-20"
        _, client = convert(client_with(resp(200, {"message": mapped})))
        body = client.post.call_args.kwargs["json"]
        assert body["delivery_date"] == "2026-12-15"
        assert body["items"][0]["delivery_date"] == "2026-12-20"
        assert body["items"][1]["delivery_date"] == "2026-12-15"

    def test_the_mapped_draft_is_not_changed_in_place(self):
        mapped = mapped_order()
        original = copy.deepcopy(mapped)
        convert(client_with(resp(200, {"message": mapped})))
        assert mapped == original

    def test_a_forged_docstatus_in_the_mapped_draft_cannot_create_a_submitted_order(self):
        _, client = convert(client_with(resp(200, {"message": mapped_order(docstatus=1)})))
        assert client.post.call_args.kwargs["json"]["docstatus"] == 0

    # ---- refusals before anything is sent -----------------------------------------------------
    def test_a_role_without_rights_is_refused_before_any_call(self):
        res, client = convert(ctx=user("Purchase User"))
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()
        client.post.assert_not_called()

    def test_a_read_only_session_is_refused_before_any_call(self):
        res, client = convert(ctx=user("System Manager", read_only=True))
        assert res["status"] == "permission_denied" and "read-only" in res["error"]
        client.get.assert_not_called()

    @pytest.mark.parametrize("source", ["Purchase Order", "Customer", "Sales Order"])
    def test_an_unregistered_source_is_refused(self, source):
        res, client = convert(source=source)
        assert res["status"] == "permission_denied"
        client.get.assert_not_called()

    def test_a_doctype_outside_the_whitelist_is_refused(self):
        res, client = convert(source="SecretDoctype")
        assert res["status"] == "permission_denied" and "whitelist" in res["error"]
        res, client = convert(target="SecretDoctype")
        assert res["status"] == "permission_denied" and "whitelist" in res["error"]
        client.get.assert_not_called()

    @pytest.mark.parametrize("name", ["", "   ", None, 5, ["x"]])
    def test_a_missing_source_name_is_refused(self, name):
        res, client = convert(name=name)
        assert res["status"] == "system_error"
        client.get.assert_not_called()

    def test_the_source_name_is_a_parameter_never_part_of_the_path(self):
        _, client = convert(name="SAL-QTN-1/../../Customer")
        assert client.get.call_args.args[0] == f"/api/method/{METHOD}"
        assert client.get.call_args.kwargs["params"]["source_name"] == "SAL-QTN-1/../../Customer"

    # ---- ERPNext's answers ---------------------------------------------------------------------
    def test_a_source_erpnext_will_not_map_is_reported_with_its_reason(self):
        body = {"exc_type": "ValidationError", "exception": "frappe.exceptions.ValidationError: Cannot map because following condition fails: docstatus=1"}
        res, client = convert(client_with(resp(417, body)))
        assert res["status"] == "system_error" and "docstatus=1" in res["error"]
        client.post.assert_not_called()

    def test_a_missing_source_is_not_found(self):
        res, client = convert(client_with(resp(404, {"exc_type": "DoesNotExistError", "exception": "Quotation SAL-QTN-9 not found"})))
        assert res["status"] == "not_found"
        client.post.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    def test_no_access_to_the_source_is_permission_denied(self, status):
        res, client = convert(client_with(resp(status, {"exc_type": "PermissionError", "exception": "no"})))
        assert res["status"] == "permission_denied"
        client.post.assert_not_called()

    def test_nothing_left_to_convert_is_reported_not_forced(self):
        res, client = convert(client_with(resp(200, {"message": mapped_order(items=[])})))
        assert res["status"] == "system_error" and "Nothing is left to convert" in res["error"]
        client.post.assert_not_called()

    @pytest.mark.parametrize("message", [None, "text", ["x"], {"doctype": "Delivery Note", "items": [{"item_code": "A"}]}, {}])
    def test_an_answer_that_is_not_the_asked_for_draft_is_refused(self, message):
        res, client = convert(client_with(resp(200, {"message": message})))
        assert res["status"] == "system_error"
        client.post.assert_not_called()

    def test_a_body_that_is_not_json_is_an_error(self):
        bad = resp(200)
        bad.json.side_effect = ValueError("no json")
        res, client = convert(client_with(bad))
        assert res["status"] == "system_error"
        client.post.assert_not_called()

    def test_a_refusal_when_saving_is_reported(self):
        res, _ = convert(client_with(post_response=resp(417, {"exc_type": "ValidationError", "exception": "frappe.exceptions.ValidationError: Customer is disabled"})))
        assert res["status"] == "system_error" and "Customer is disabled" in res["error"]
        res, _ = convert(client_with(post_response=resp(403, {"exc_type": "PermissionError", "exception": "no"})))
        assert res["status"] == "permission_denied"

    def test_a_transport_error_is_an_error_envelope_and_nothing_is_retried(self):
        client = client_with()
        client.post.side_effect = RuntimeError("boom")
        res, _ = convert(client)
        assert res["status"] == "system_error"
        assert client.post.call_count == 1

    def test_the_envelope_keeps_its_five_keys(self):
        res, _ = convert()
        assert set(res) == {"status", "data", "error", "has_more", "success"}
        res, _ = convert(ctx=user("Purchase User"))
        assert set(res) == {"status", "data", "error", "has_more", "success"}
