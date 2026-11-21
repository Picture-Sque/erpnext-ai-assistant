"""
Sales Order to Delivery Note and Purchase Order to Purchase Receipt through the conversation pipeline,
from node level to a full /chat run (harness of test_mixed_chains.py).

Always asks first, shows only what is still pending, refuses an order with nothing left before asking,
mentions documents that already exist for the order (a draft does not reduce what is pending), creates a
Draft, and does NOT offer to submit it: submitting posts to stock, and that is not offered from chat.
"""

import copy
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import audit_logger
import chat_store
import main
import workflow.followup_router as followup_router
import workflow.graph as graph
from test_mixed_chains import SECRET, FakeERP, ScriptedLLM, Driver, _res

SO, SO_FULL, SO_PART, SO_DRAFT = "SAL-ORD-2026-00088", "SAL-ORD-2026-00005", "SAL-ORD-2026-00090", "SAL-ORD-2026-00091"
PO, PO_FULL, PO_PART, PO_DRAFT = "PUR-ORD-2026-00028", "PUR-ORD-2026-00010", "PUR-ORD-2026-00030", "PUR-ORD-2026-00031"


def sales_order(name, docstatus=1, done=0.0, per=0.0, delivered=(0, 0)):
    return {"name": name, "docstatus": docstatus, "status": "To Deliver and Bill", "modified": "2026-11-20 10:00:00.000001",
            "customer": "Beacon Healthcare Group", "customer_name": "Beacon Healthcare Group", "grand_total": 2000.0, "per_delivered": per,
            "items": [{"item_code": "DESK", "qty": 10.0, "rate": 100.0, "delivered_qty": float(delivered[0])},
                      {"item_code": "CHAIR", "qty": 20.0, "rate": 50.0, "delivered_qty": float(delivered[1])}]}


def purchase_order(name, docstatus=1, per=0.0, received=(0, 0)):
    return {"name": name, "docstatus": docstatus, "status": "To Receive and Bill", "modified": "2026-11-20 10:00:00.000001",
            "supplier": "Zuckerman Security Ltd.", "supplier_name": "Zuckerman Security Ltd.", "grand_total": 3000.0, "per_received": per,
            "items": [{"item_code": "DESK", "qty": 10.0, "rate": 200.0, "received_qty": float(received[0])},
                      {"item_code": "CHAIR", "qty": 20.0, "rate": 50.0, "received_qty": float(received[1])}]}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "NEW-1"}, "error": None, "has_more": False}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


DN_SKILL, PR_SKILL = "convert-sales-order-to-delivery-note", "convert-purchase-order-to-purchase-receipt"


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def pre(self, skill, slot, name, doc):
        state = {"detected_intent": skill, "write_rbac_operation": "convert", "collected_fields": {slot: name}, "resolved_entities": {}}
        with patch.object(graph, "get_document", return_value=ok(doc)) as gd:
            return graph.precondition_validation_node(state), gd

    def confirm(self, skill, slot, name, snapshot, existing=None, roles=("Stock User",)):
        state = {"detected_intent": skill, "preconditions_validated": True, "write_rbac_operation": "convert", "collected_fields": {slot: name},
                 "resolved_entities": {}, "messages": [], "user_sub": "u", "user_roles": list(roles), "last_read_snapshot": snapshot}
        found = {"status": "success", "data": [{"parent": p} for p in existing]} if existing else {"status": "empty", "data": []}
        with patch.object(graph, "get_list", return_value=found) as gl:
            return graph.confirmation_node(state), gl

    # ---- the skills ----------------------------------------------------------------------------
    def test_the_skills_target_the_right_source_and_ask_for_the_order(self):
        dn, pr = graph._get_skill_for_intent(DN_SKILL), graph._get_skill_for_intent(PR_SKILL)
        assert (dn["doctype"], dn["source_doctype"], dn["required_fields"]) == ("Delivery Note", "Sales Order", ["sales_order"])
        assert (pr["doctype"], pr["source_doctype"], pr["required_fields"]) == ("Purchase Receipt", "Purchase Order", ["purchase_order"])
        assert graph._record_doctype(dn) == "Sales Order" and graph._record_doctype(pr) == "Purchase Order"
        assert graph._target_record_id({"sales_order": SO}, {}, dn) == SO
        assert graph._target_record_id({"purchase_order": PO}, {}, pr) == PO

    def test_the_write_gate_follows_the_roles_erpnext_gives(self):
        gate = lambda skill, roles: graph.write_rbac_gate_node({"detected_intent": skill, "user_roles": roles, "collected_fields": {}})["write_rbac_passed"]
        for roles in (["Stock User"], ["Stock Manager"], ["Sales User"]):
            assert gate(DN_SKILL, roles)
        for roles in (["Purchase User"], ["Purchase Manager"], ["Accounts User"]):
            assert not gate(DN_SKILL, roles)
        for roles in (["Stock User"], ["Stock Manager"], ["Purchase User"]):
            assert gate(PR_SKILL, roles)
        for roles in (["Sales User"], ["Sales Manager"], ["Accounts User"]):
            assert not gate(PR_SKILL, roles)

    # ---- preconditions ------------------------------------------------------------------------
    @pytest.mark.parametrize("skill,slot,name,make,word,target", [
        (DN_SKILL, "sales_order", SO, lambda n, **k: sales_order(n, **k), "delivered", "Delivery Note"),
        (PR_SKILL, "purchase_order", PO, lambda n, **k: purchase_order(n, **k), "received", "Purchase Receipt"),
    ])
    def test_state_checks(self, skill, slot, name, make, word, target):
        out, gd = self.pre(skill, slot, name, make(name))
        assert out["preconditions_validated"] is True
        gd.assert_called_once_with("Sales Order" if slot == "sales_order" else "Purchase Order", name)

        out, _ = self.pre(skill, slot, name, make(name, docstatus=0))
        assert out["preconditions_validated"] is False and "Only a submitted" in out["final_response"]

        out, _ = self.pre(skill, slot, name, make(name, per=100.0))
        assert out["preconditions_validated"] is False and out["failure_classification"] == "validation_error"
        assert f"is already fully {word}, so there is nothing left to put on a {target}." in out["final_response"]

        out, _ = self.pre(skill, slot, name, make(name, per=50.0))
        assert out["preconditions_validated"] is True, "a partly done order can still be converted"

    # ---- confirmation -------------------------------------------------------------------------
    def test_a_delivery_note_question_shows_the_order_in_full_when_nothing_is_delivered(self):
        out, _ = self.confirm(DN_SKILL, "sales_order", SO, sales_order(SO))
        text = out["pending_confirmation"]["scope_description"]
        assert f"Are you sure you want to create a Delivery Note from Sales Order '{SO}'?" in text
        assert "It will be saved as a Draft." in text and "For: Beacon Healthcare Group" in text
        assert "- 10.0x DESK @ 100.0" in text and "- 20.0x CHAIR @ 50.0" in text and "Total: 2000.0" in text
        assert "Nothing is posted to stock until the Delivery Note is submitted." in text
        assert out["pending_confirmation"]["operation"] == "convert" and out["pending_confirmation"]["doctype"] == "Delivery Note"
        assert out["pending_confirmation"]["target_name"] == SO and out["pending_confirmation"]["target_modified"]

    def test_a_partly_delivered_order_shows_only_what_is_pending(self):
        out, _ = self.confirm(DN_SKILL, "sales_order", SO_PART, sales_order(SO_PART, per=50.0, delivered=(4, 20)))
        text = out["pending_confirmation"]["scope_description"]
        assert "- 6.0x DESK (of 10.0, 4.0 already delivered) @ 100.0" in text
        assert "CHAIR" not in text, "a row with nothing pending is left out"
        assert "Total:" not in text, "the order total covers the whole order, not what is pending"

    def test_a_partly_received_order_says_received(self):
        out, _ = self.confirm(PR_SKILL, "purchase_order", PO_PART, purchase_order(PO_PART, per=30.0, received=(3, 0)))
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to create a Purchase Receipt from Purchase Order" in text
        assert "- 7.0x DESK (of 10.0, 3.0 already received) @ 200.0" in text and "- 20.0x CHAIR @ 50.0" in text
        assert "For: Zuckerman Security Ltd." in text
        assert "Nothing is posted to stock until the Purchase Receipt is submitted." in text

    def test_existing_documents_for_the_order_are_mentioned_with_the_right_warning(self):
        out, gl = self.confirm(DN_SKILL, "sales_order", SO, sales_order(SO), existing=["MAT-DN-2026-00001", "MAT-DN-2026-00002"])
        text = out["pending_confirmation"]["scope_description"]
        assert "Delivery Note records already exist for this Sales Order: MAT-DN-2026-00001, MAT-DN-2026-00002." in text
        assert "A draft does not reduce what is still to deliver, so creating another would repeat it." in text
        assert gl.call_args.args == ("Delivery Note Item",)
        assert gl.call_args.kwargs["filters"] == [["against_sales_order", "=", SO], ["Delivery Note", "docstatus", "<", "2"]]
        assert gl.call_args.kwargs["parent_doctype"] == "Delivery Note"

        out, gl = self.confirm(PR_SKILL, "purchase_order", PO, purchase_order(PO), existing=["MAT-PRE-2026-00001"])
        text = out["pending_confirmation"]["scope_description"]
        assert "Purchase Receipt records already exist for this Purchase Order: MAT-PRE-2026-00001." in text
        assert "still to receive" in text
        assert gl.call_args.kwargs["filters"] == [["purchase_order", "=", PO], ["Purchase Receipt", "docstatus", "<", "2"]]

    def test_no_note_when_nothing_exists(self):
        out, _ = self.confirm(DN_SKILL, "sales_order", SO, sales_order(SO))
        assert "already exist" not in out["pending_confirmation"]["scope_description"]

    # ---- execution and what follows ----------------------------------------------------------------
    @pytest.mark.parametrize("skill,slot,name,target,source", [(DN_SKILL, "sales_order", SO, "Delivery Note", "Sales Order"), (PR_SKILL, "purchase_order", PO, "Purchase Receipt", "Purchase Order")])
    def test_the_tool_gets_only_the_target_the_source_and_the_order_id(self, skill, slot, name, target, source):
        state = {"detected_intent": skill, "collected_fields": {slot: name, "customer": "Evil", "docstatus": 1}}
        with patch.object(graph, "convert_document", return_value=ok()) as conv, patch.object(graph, "create_document") as create:
            graph.call_generic_tool_node(state)
        conv.assert_called_once_with(target, source, name)
        create.assert_not_called()

    def test_submitting_what_was_made_is_offered_to_the_stock_manager_only(self):
        for doctype in ("Delivery Note", "Purchase Receipt"):
            for roles in (["Stock Manager"], ["System Manager"]):
                state = {"user_roles": roles, "user_sub": "u", "messages": []}
                assert graph._submit_offer(state, doctype, "MAT-1") is not None, (roles, doctype)
            for roles in (["Stock User"], ["Sales User"], ["Sales Manager"], ["Purchase User"], ["Purchase Manager"], ["Accounts Manager"]):
                state = {"user_roles": roles, "user_sub": "u", "messages": []}
                assert graph._submit_offer(state, doctype, "MAT-1") is None, (roles, doctype)

    def test_these_documents_have_a_submit_skill_and_nothing_else(self):
        docs = ("Delivery Note", "Purchase Receipt")
        assert {s["doctype"] for s in graph.loaded_skills if s["tool"] == "submit_document" and s["doctype"] in docs} == set(docs)
        assert not [s for s in graph.loaded_skills if s["doctype"] in docs and s["tool"] in ("create_document", "update_document", "cancel_document", "delete_document")]


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

STOCK = ["Stock User"]


class StockConvertERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Sales Order"] = [sales_order(SO), sales_order(SO_FULL, per=100.0, delivered=(10, 20)), sales_order(SO_PART, per=50.0, delivered=(4, 20)), sales_order(SO_DRAFT, docstatus=0)]
        self.db["Purchase Order"] = [purchase_order(PO), purchase_order(PO_FULL, per=100.0, received=(10, 20)), purchase_order(PO_PART, per=30.0, received=(3, 0)), purchase_order(PO_DRAFT, docstatus=0)]
        self.db["Delivery Note"] = []
        self.db["Purchase Receipt"] = []
        self.next = 0

    SPEC = {"Delivery Note": ("Sales Order", "delivered_qty", "against_sales_order"), "Purchase Receipt": ("Purchase Order", "received_qty", "purchase_order")}

    def get_list(self, doctype, filters=None, fields=None, limit=None, parent_doctype=None, **kw):
        if doctype == "Item":  # item codes are exact here, so an item named in a partial conversion resolves to itself
            code = next((f[2] for f in (filters or []) if f[0] == "item_code"), None)
            if code in ("DESK", "CHAIR"):
                return _res("success", [{"name": code, "item_code": code, "item_name": code, "item_group": "Products"}])
        if doctype in ("Delivery Note Item", "Purchase Receipt Item"):
            self.calls.append(("get_list", doctype, filters))
            target = parent_doctype
            link = self.SPEC[target][2]
            wanted = next(f[2] for f in filters if f[0] == link)
            rows = [{"parent": d["name"]} for d in self.db[target] if d["docstatus"] < 2 and any(i.get(link) == wanted for i in d["items"])]
            return _res("success" if rows else "empty", rows)
        return super().get_list(doctype, filters, fields, limit)

    def convert_document(self, target, source_doctype, source_name, items=None):
        """ERPNext's mapper: only what is still pending goes on the new document."""
        self.calls.append(("convert_document", target, source_doctype, source_name) + ((items,) if items is not None else ()))
        source, done_field, link = self.SPEC[target]
        if source != source_doctype:
            return _res("permission_denied", error="not registered")
        src = next(r for r in self.db[source] if r["name"] == source_name)
        if src["docstatus"] != 1:
            return _res("system_error", error="Cannot map because following condition fails: docstatus=1")
        rows = [dict(item_code=i["item_code"], qty=i["qty"] - i.get(done_field, 0), rate=i["rate"], **{link: source_name}) for i in src["items"] if i["qty"] - i.get(done_field, 0) > 0]
        if not rows:
            return _res("system_error", error=f"Nothing is left to convert from {source} {source_name}.")
        if items is not None:
            wanted = {w["item_code"]: w["qty"] for w in items}
            rows = [dict(r, qty=wanted[r["item_code"]]) for r in rows if r["item_code"] in wanted]
        self.next += 1
        doc = {"name": f"MAT-{self.next:05d}", "docstatus": 0, "status": "Draft", "items": rows, "company": "Acme Ltd"}
        self.db[target].append(doc)
        return _res("success", copy.deepcopy(doc))

    def converts(self):
        return [c for c in self.calls if c[0] == "convert_document"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = StockConvertERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "convert_document", "submit_document",
                 "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)
    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


def script(env, message, skill, slot, value):
    env.llm.classify[message] = {"intent": skill, "is_compound": False}
    env.llm.extract[message] = {slot: value}


def test_e2e_a_sales_order_becomes_a_draft_delivery_note_after_a_yes(env):
    msg = f"create a delivery note from sales order {SO}"
    script(env, msg, DN_SKILL, "sales_order", SO)
    out = env.text(msg, roles=STOCK)
    assert f"Are you sure you want to create a Delivery Note from Sales Order '{SO}'?" in out
    assert "Nothing is posted to stock until the Delivery Note is submitted." in out
    assert env.erp.converts() == [] and env.erp.db["Delivery Note"] == []

    out = env.text("yes", roles=STOCK)
    assert f"FORMATTED[{DN_SKILL}]" in out
    assert "Submit it now?" not in out
    assert env.erp.converts() == [("convert_document", "Delivery Note", "Sales Order", SO)]
    (dn,) = env.erp.db["Delivery Note"]
    assert dn["docstatus"] == 0 and [(i["item_code"], i["qty"]) for i in dn["items"]] == [("DESK", 10.0), ("CHAIR", 20.0)]
    assert env.persisted()["pending_confirmation"] is None, "no submit offer is left waiting"


def test_e2e_a_purchase_order_becomes_a_draft_purchase_receipt_after_a_yes(env):
    msg = f"create a purchase receipt from purchase order {PO}"
    script(env, msg, PR_SKILL, "purchase_order", PO)
    out = env.text(msg, roles=["Purchase User"])
    assert f"Are you sure you want to create a Purchase Receipt from Purchase Order '{PO}'?" in out and "For: Zuckerman Security Ltd." in out
    out = env.text("yes", roles=["Purchase User"])
    assert f"FORMATTED[{PR_SKILL}]" in out and "Submit it now?" not in out
    (pr,) = env.erp.db["Purchase Receipt"]
    assert pr["docstatus"] == 0 and pr["items"][0]["purchase_order"] == PO


def test_e2e_only_what_is_pending_goes_on_a_partly_delivered_order(env):
    msg = f"create a delivery note from sales order {SO_PART}"
    script(env, msg, DN_SKILL, "sales_order", SO_PART)
    out = env.text(msg, roles=STOCK)
    assert "- 6.0x DESK (of 10.0, 4.0 already delivered)" in out
    env.text("yes", roles=STOCK)
    (dn,) = env.erp.db["Delivery Note"]
    assert [(i["item_code"], i["qty"]) for i in dn["items"]] == [("DESK", 6.0)]


def test_e2e_a_fully_delivered_or_received_order_is_refused_before_asking(env):
    msg = f"create a delivery note from sales order {SO_FULL}"
    script(env, msg, DN_SKILL, "sales_order", SO_FULL)
    out = env.text(msg, roles=STOCK)
    assert f"Sales Order '{SO_FULL}' is already fully delivered, so there is nothing left to put on a Delivery Note." in out
    msg2 = f"create a purchase receipt from purchase order {PO_FULL}"
    script(env, msg2, PR_SKILL, "purchase_order", PO_FULL)
    out = env.text(msg2, roles=STOCK, conversation_id="conv-2")
    assert f"Purchase Order '{PO_FULL}' is already fully received, so there is nothing left to put on a Purchase Receipt." in out
    assert env.erp.converts() == [] and env.persisted("conv-2")["pending_confirmation"] is None


def test_e2e_a_draft_order_is_refused_before_asking(env):
    msg = f"create a delivery note from sales order {SO_DRAFT}"
    script(env, msg, DN_SKILL, "sales_order", SO_DRAFT)
    out = env.text(msg, roles=STOCK)
    assert f"Sales Order '{SO_DRAFT}' is Draft. Only a submitted Sales Order can be converted into a Delivery Note." in out
    assert env.erp.converts() == []


def test_e2e_an_existing_draft_is_mentioned_when_asking_again(env):
    msg = f"create a delivery note from sales order {SO}"
    script(env, msg, DN_SKILL, "sales_order", SO)
    env.text(msg, roles=STOCK)
    env.text("yes", roles=STOCK)
    out = env.text(msg, roles=STOCK, conversation_id="conv-2")
    assert "Delivery Note records already exist for this Sales Order: MAT-00001." in out
    assert "creating another would repeat it" in out
    assert len(env.erp.db["Delivery Note"]) == 1


def test_e2e_the_wrong_roles_cannot_make_either_document(env):
    msg = f"create a delivery note from sales order {SO}"
    script(env, msg, DN_SKILL, "sales_order", SO)
    for i, roles in enumerate((["Purchase Manager"], ["Accounts User"])):
        assert "Denied" in env.text(msg, roles=roles, conversation_id=f"a{i}")
    msg2 = f"create a purchase receipt from purchase order {PO}"
    script(env, msg2, PR_SKILL, "purchase_order", PO)
    for i, roles in enumerate((["Sales User", "Sales Manager"], ["Accounts User"])):
        assert "Denied" in env.text(msg2, roles=roles, conversation_id=f"b{i}")
    assert env.erp.converts() == []


def test_e2e_what_is_typed_does_not_change_what_is_made(env):
    msg = f"create a delivery note from sales order {SO} with 999 DESK for Evil Corp"
    env.llm.classify[msg] = {"intent": DN_SKILL, "is_compound": False}
    env.llm.extract[msg] = {"sales_order": SO, "customer": "Evil Corp", "grand_total": 1}
    env.text(msg, roles=STOCK)
    env.text("yes", roles=STOCK)
    (dn,) = env.erp.db["Delivery Note"]
    assert [(i["item_code"], i["qty"]) for i in dn["items"]] == [("DESK", 10.0), ("CHAIR", 20.0)]


def test_e2e_a_no_creates_nothing_and_an_edit_between_question_and_yes_is_refused(env):
    msg = f"create a delivery note from sales order {SO}"
    script(env, msg, DN_SKILL, "sales_order", SO)
    env.text(msg, roles=STOCK)
    out = env.text("no", roles=STOCK)
    assert f"No Delivery Note was created from '{SO}'." in out
    env.text(msg, roles=STOCK, conversation_id="conv-2")
    env.erp.db["Sales Order"][0]["modified"] = "2026-11-20 11:00:00.000009"
    out = env.text("yes", roles=STOCK, conversation_id="conv-2")
    assert f"Sales Order '{SO}' was changed after you were asked to confirm" in out
    assert env.erp.converts() == []


def test_e2e_a_partial_delivery_note_holds_only_the_named_item_and_quantity(env):
    msg = f"create a delivery note from sales order {SO} with only 3 DESK"
    env.llm.classify[msg] = {"intent": DN_SKILL, "is_compound": False}
    env.llm.extract[msg] = {"sales_order": SO, "items": [{"item_code": "DESK", "qty": 3}]}
    out = env.text(msg, roles=STOCK)
    assert "- 3x DESK (of 10.0 on the Sales Order)" in out and "CHAIR" not in out
    env.text("yes", roles=STOCK)
    (dn,) = env.erp.db["Delivery Note"]
    assert [(i["item_code"], i["qty"]) for i in dn["items"]] == [("DESK", 3)]


def test_e2e_a_partly_delivered_order_cannot_be_asked_for_more_than_is_pending(env):
    msg = f"create a delivery note from sales order {SO_PART} with 8 DESK"
    env.llm.classify[msg] = {"intent": DN_SKILL, "is_compound": False}
    env.llm.extract[msg] = {"sales_order": SO_PART, "items": [{"item_code": "DESK", "qty": 8}]}
    out = env.text(msg, roles=STOCK)
    assert "only 6 is left to convert" in out
    assert env.erp.converts() == [] and env.erp.db["Delivery Note"] == []


def test_e2e_a_partial_purchase_receipt_holds_only_the_named_quantity(env):
    msg = f"create a purchase receipt from purchase order {PO} with 4 DESK"
    env.llm.classify[msg] = {"intent": PR_SKILL, "is_compound": False}
    env.llm.extract[msg] = {"purchase_order": PO, "items": [{"item_code": "DESK", "qty": 4}]}
    env.text(msg, roles=["Purchase User"])
    env.text("yes", roles=["Purchase User"])
    (pr,) = env.erp.db["Purchase Receipt"]
    assert [(i["item_code"], i["qty"]) for i in pr["items"]] == [("DESK", 4)]
