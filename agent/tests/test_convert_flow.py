"""
Quotation to Sales Order through the conversation pipeline, from node level to a full /chat run.

The conversion always asks first (showing the customer, items and total, and any orders already made
from the same quotation), only a submitted quotation can be converted, the new order is a Draft, and a
manager is then offered the submit. The end-to-end part reuses the harness of test_mixed_chains.py.
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
from mcp_erpnext.permissions import DENIED, PermissionCheck
from test_mixed_chains import SECRET, FakeERP, ScriptedLLM, Driver, _res

Q = "SAL-QTN-2026-00001"
QUOTE = {
    "name": Q, "docstatus": 1, "status": "Open", "modified": "2026-11-20 10:00:00.000001", "party_name": "Acme Corp",
    "customer_name": "Acme Corp", "grand_total": 500.0,
    "items": [{"item_code": "DESK", "qty": 4.0, "rate": 100.0}, {"item_code": "CHAIR", "qty": 2.0, "rate": 50.0}],
}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "SAL-ORD-2026-00200"}, "error": None, "has_more": False}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


def skill():
    return graph._get_skill_for_intent("convert-quotation-to-sales-order")


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def pre(self, collected=None, doc=QUOTE):
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert",
                 "collected_fields": collected if collected is not None else {"quotation": Q}, "resolved_entities": {}}
        with patch.object(graph, "get_document", return_value=ok(doc) if doc is not None else {"status": "not_found", "data": None, "error": "nope"}) as gd:
            return graph.precondition_validation_node(state), gd

    # ---- skill and gate ---------------------------------------------------------------------
    def test_the_skill_asks_for_a_quotation_and_nothing_else(self):
        s = skill()
        assert s["tool"] == "convert_document" and s["doctype"] == "Sales Order" and s["source_doctype"] == "Quotation"
        assert s["required_fields"] == ["quotation"] and s["optional_fields"] == ["items"]
        assert graph._record_doctype(s) == "Quotation"
        assert graph._record_doctype(graph._get_skill_for_intent("create-sales-order")) == "Sales Order"

    def test_the_quotation_is_the_record_acted_on(self):
        assert graph._target_record_id({"quotation": Q}, {}, skill()) == Q

    def test_validation_needs_the_quotation(self):
        v = lambda c: graph.validate_parameters_node({"detected_intent": "convert-quotation-to-sales-order", "collected_fields": c})
        assert v({})["all_required_filled"] is False
        assert v({"quotation": Q})["all_required_filled"] is True

    def test_the_write_gate_allows_sales_roles_only(self):
        gate = lambda roles: graph.write_rbac_gate_node({"detected_intent": "convert-quotation-to-sales-order", "user_roles": roles, "collected_fields": {}})["write_rbac_passed"]
        assert gate(["Sales User"]) and gate(["Sales Manager"]) and gate(["System Manager"])
        for roles in (["Purchase Manager"], ["Stock Manager"], ["Accounts User"]):
            assert not gate(roles)

    # ---- preconditions ----------------------------------------------------------------------
    def test_a_submitted_quotation_passes_and_the_source_is_read_not_the_order(self):
        out, gd = self.pre()
        assert out["preconditions_validated"] is True
        gd.assert_called_once_with("Quotation", Q)
        assert out["last_read_snapshot"]["name"] == Q

    def test_a_draft_quotation_is_refused_in_plain_words(self):
        out, _ = self.pre(doc={**QUOTE, "docstatus": 0})
        assert out["preconditions_validated"] is False and out["failure_classification"] == "validation_error"
        assert out["final_response"] == f"Quotation '{Q}' is Draft. Only a submitted Quotation can be converted into a Sales Order."

    def test_a_cancelled_quotation_is_refused(self):
        out, _ = self.pre(doc={**QUOTE, "docstatus": 2})
        assert out["preconditions_validated"] is False and "is Cancelled" in out["final_response"]

    def test_a_missing_quotation_is_not_found(self):
        out, _ = self.pre(doc=None)
        assert out["failure_classification"] == "not_found" and f"Quotation '{Q}' was not found." == out["final_response"]

    def test_no_quotation_named_asks_for_one(self):
        out, gd = self.pre(collected={})
        assert out["preconditions_validated"] is False and "exact Quotation record to convert" in out["final_response"]
        gd.assert_not_called()

    def test_erpnext_is_asked_whether_the_user_may_create_the_order(self):
        check = MagicMock(return_value=PermissionCheck(DENIED, "no"))
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert", "collected_fields": {"quotation": Q}, "resolved_entities": {}}
        with patch.object(graph, "check_permission", check), patch.object(graph, "get_document", return_value=ok(QUOTE)):
            out = graph.precondition_validation_node(state)
        check.assert_called_once_with("convert", "Sales Order", None)
        assert out["preconditions_validated"] is False and out["failure_classification"] == "permission_denied"
        assert "not allowed to create Sales Order." in out["final_response"]

    def test_an_edit_between_the_question_and_the_yes_names_the_quotation(self):
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert", "collected_fields": {"quotation": Q}, "resolved_entities": {},
                 "confirmation_result": "confirmed", "confirmed_action": {"skill": "convert-quotation-to-sales-order", "operation": "convert", "target_modified": "2026-11-19 09:00:00.000001"}}
        with patch.object(graph, "get_document", return_value=ok(QUOTE)):
            out = graph.precondition_validation_node(state)
        assert out["failure_classification"] == "conflict_stale_data"
        assert f"Quotation '{Q}' was changed after you were asked to confirm" in out["final_response"]

    # ---- confirmation -----------------------------------------------------------------------
    def confirm(self, snapshot=QUOTE, existing=None, **extra):
        state = {"detected_intent": "convert-quotation-to-sales-order", "preconditions_validated": True, "write_rbac_operation": "convert",
                 "collected_fields": {"quotation": Q}, "resolved_entities": {}, "messages": [], "user_sub": "u", "user_roles": ["Sales User"], "last_read_snapshot": snapshot}
        state.update(extra)
        found = {"status": "success", "data": [{"parent": p} for p in (existing or [])]} if existing is not None else {"status": "empty", "data": []}
        with patch.object(graph, "get_list", return_value=found) as gl:
            return graph.confirmation_node(state), gl

    def test_a_conversion_always_asks_and_shows_what_will_be_created(self):
        out, _ = self.confirm()
        pending = out["pending_confirmation"]
        text = pending["scope_description"]
        assert f"Are you sure you want to create a Sales Order from Quotation '{Q}'?" in text
        assert "It will be saved as a Draft." in text and "For: Acme Corp" in text
        assert "Items:" in text and "- 4.0x DESK @ 100.0" in text and "- 2.0x CHAIR @ 50.0" in text and "Total: 500.0" in text
        assert "Reply **Yes**" in out["final_response"]
        assert pending["operation"] == "convert" and pending["doctype"] == "Sales Order" and pending["target_name"] == Q
        assert pending["target_modified"] == QUOTE["modified"]
        assert pending["params"] == {"quotation": Q}

    def test_orders_already_made_from_the_quotation_are_mentioned(self):
        out, gl = self.confirm(existing=["SAL-ORD-2026-00050", "SAL-ORD-2026-00050", "SAL-ORD-2026-00051"])
        text = out["pending_confirmation"]["scope_description"]
        assert "Note: Sales Order records were already made from this Quotation: SAL-ORD-2026-00050, SAL-ORD-2026-00051. Converting again creates another one." in text
        args, kwargs = gl.call_args
        assert args == ("Sales Order Item",)
        assert kwargs["filters"] == [["prevdoc_docname", "=", Q], ["Sales Order", "docstatus", "<", "2"]]
        assert kwargs["parent_doctype"] == "Sales Order" and kwargs["fields"] == ["parent"]

    def test_no_note_when_there_are_none_or_the_lookup_fails(self):
        out, _ = self.confirm(existing=None)
        assert "already made" not in out["pending_confirmation"]["scope_description"]
        state = {"detected_intent": "convert-quotation-to-sales-order", "preconditions_validated": True, "write_rbac_operation": "convert", "collected_fields": {"quotation": Q},
                 "resolved_entities": {}, "messages": [], "user_sub": "u", "user_roles": ["Sales User"], "last_read_snapshot": QUOTE}
        for failure in ({"status": "system_error", "data": None, "error": "boom"}, RuntimeError("boom")):
            effect = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with patch.object(graph, "get_list", **effect):
                out = graph.confirmation_node(dict(state))
            assert "Are you sure you want to create a Sales Order" in out["pending_confirmation"]["scope_description"]
            assert "already made" not in out["pending_confirmation"]["scope_description"]

    def test_the_question_still_names_the_quotation_without_a_fresh_snapshot(self):
        out, _ = self.confirm(snapshot={"name": "SOMETHING-ELSE", "docstatus": 1})
        text = out["pending_confirmation"]["scope_description"]
        assert f"from Quotation '{Q}'?" in text and "Items:" not in text
        assert out["pending_confirmation"]["target_modified"] is None

    def test_a_confirmed_conversion_is_not_asked_again(self):
        out, _ = self.confirm(confirmation_result="confirmed", confirmed_action={"skill": "convert-quotation-to-sales-order", "operation": "convert", "doctype": "Sales Order", "target_name": Q})
        assert out["pending_confirmation"] is None and "final_response" not in out

    def test_a_declined_conversion_says_nothing_was_created(self):
        assert graph._not_executed_message({"operation": "convert", "doctype": "Sales Order", "target_name": Q}) == f"No Sales Order was created from '{Q}'."

    # ---- execution --------------------------------------------------------------------------
    def test_the_tool_gets_only_the_target_the_source_type_and_the_quotation_id(self):
        state = {"detected_intent": "convert-quotation-to-sales-order",
                 "collected_fields": {"quotation": Q, "customer": "Someone Else", "grand_total": 1, "docstatus": 1}}
        with patch.object(graph, "convert_document", return_value=ok()) as conv, patch.object(graph, "create_document") as create:
            graph.call_generic_tool_node(state)
        conv.assert_called_once_with("Sales Order", "Quotation", Q)
        create.assert_not_called()

    def test_a_partial_request_passes_the_items_to_the_tool(self):
        rows = [{"item_code": "DESK", "qty": 2.0}]
        state = {"detected_intent": "convert-quotation-to-sales-order", "collected_fields": {"quotation": Q, "items": rows}}
        with patch.object(graph, "convert_document", return_value=ok()) as conv:
            graph.call_generic_tool_node(state)
        conv.assert_called_once_with("Sales Order", "Quotation", Q, items=rows)

    def test_a_successful_conversion_is_verified_without_a_reread_and_remembers_the_new_order(self):
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert", "target_doctype": "Sales Order",
                 "collected_fields": {"quotation": Q}, "tool_raw_response": {"success": True, "data": {"name": "SAL-ORD-2026-00200", "docstatus": 0}}}
        with patch.object(graph, "get_document") as gd:
            out = graph.result_validation_node(state)
        gd.assert_not_called()
        assert out["write_verified"] is True
        assert out["last_created_doc"] == {"doctype": "Sales Order", "name": "SAL-ORD-2026-00200"}

    def test_a_conversion_that_returns_no_order_name_is_not_called_done(self):
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert", "target_doctype": "Sales Order",
                 "collected_fields": {"quotation": Q}, "tool_raw_response": {"success": True, "data": {"docstatus": 0}}}
        with patch.object(graph, "get_document", return_value={"status": "not_found", "data": None}):
            out = graph.result_validation_node(state)
        assert out["write_verified"] is False and out["failure_classification"] == "write_unverified"

    def test_a_failed_conversion_is_never_retried(self):
        state = {"detected_intent": "convert-quotation-to-sales-order", "write_rbac_operation": "convert", "target_tool": "convert_document", "target_doctype": "Sales Order",
                 "failure_classification": "tool_system_failure", "retry_count": 0, "collected_fields": {"quotation": Q},
                 "tool_raw_response": {"success": False, "status": "system_error", "error": "HTTP 502"}}
        out = graph._retry_and_escalation_node_impl(state)
        assert out["is_workflow_complete"] is True and "retry_count" not in out
        assert "not retried automatically" in out["final_response"]

    def test_a_manager_is_offered_the_submit_and_a_sales_user_is_not(self):
        manager = graph._submit_offer({"user_roles": ["Sales Manager"], "user_sub": "m", "messages": []}, "Sales Order", "SAL-ORD-2026-00200")
        assert manager is not None and manager[0]["skill"] == "submit-sales-order"
        assert graph._submit_offer({"user_roles": ["Sales User"], "user_sub": "s", "messages": []}, "Sales Order", "SAL-ORD-2026-00200") is None


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

SALES, MANAGER = ["Sales User"], ["Sales Manager"]
Q_DRAFT, Q_CANCELLED = "SAL-QTN-2026-00002", "SAL-QTN-2026-00003"


class ConvertERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Quotation"] = [
            copy.deepcopy(QUOTE),
            dict(copy.deepcopy(QUOTE), name=Q_DRAFT, docstatus=0, status="Draft"),
            dict(copy.deepcopy(QUOTE), name=Q_CANCELLED, docstatus=2, status="Cancelled"),
        ]
        self.db["Sales Order"] = []
        self.next = 200

    def get_list(self, doctype, filters=None, fields=None, limit=None, parent_doctype=None, **kw):
        if doctype == "Item":  # item codes are exact here, so an item named in a partial conversion resolves to itself
            code = next((f[2] for f in (filters or []) if f[0] == "item_code"), None)
            if code in ("DESK", "CHAIR"):
                return _res("success", [{"name": code, "item_code": code, "item_name": code, "item_group": "Products"}])
        if doctype == "Sales Order Item":
            self.calls.append(("get_list", doctype, filters))
            quotation = next(f[2] for f in filters if f[0] == "prevdoc_docname")
            rows = [{"parent": so["name"]} for so in self.db["Sales Order"] if so["docstatus"] < 2 and any(i.get("prevdoc_docname") == quotation for i in so["items"])]
            return _res("success" if rows else "empty", rows)
        return super().get_list(doctype, filters, fields, limit)

    def convert_document(self, target_doctype, source_doctype, source_name, items=None):
        self.calls.append(("convert_document", target_doctype, source_doctype, source_name) + ((items,) if items is not None else ()))
        src = next((r for r in self.db[source_doctype] if r["name"] == source_name), None)
        if src is None:
            return _res("not_found", error=f"{source_doctype} {source_name} not found")
        if src["docstatus"] != 1:
            return _res("system_error", error="Cannot map because following condition fails: docstatus=1")
        self.next += 1
        order = {"name": f"SAL-ORD-2026-{self.next:05d}", "docstatus": 0, "status": "Draft", "customer": src["party_name"], "company": "Acme Ltd",
                 "items": [dict(i, prevdoc_docname=source_name) for i in src["items"]], "modified": "2026-11-20 12:00:00.000001"}
        if items is not None:  # what the real tool does: keep the named rows, set their quantities
            wanted = {w["item_code"]: w["qty"] for w in items}
            order["items"] = [dict(i, qty=wanted[i["item_code"]]) for i in order["items"] if i["item_code"] in wanted]
        self.db[target_doctype].append(order)
        return _res("success", copy.deepcopy(order))

    def orders(self):
        return self.db["Sales Order"]

    def converts(self):
        return [c for c in self.calls if c[0] == "convert_document"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = ConvertERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "convert_document", "submit_document",
                 "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)
    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


def script(env, message, quotation):
    env.llm.classify[message] = {"intent": "convert-quotation-to-sales-order", "is_compound": False}
    env.llm.extract[message] = {"quotation": quotation}


def test_e2e_a_quotation_is_previewed_confirmed_and_converted_into_a_draft_order(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    out = env.text(msg, roles=SALES)
    assert f"Are you sure you want to create a Sales Order from Quotation '{Q}'?" in out
    assert "It will be saved as a Draft." in out and "For: Acme Corp" in out and "Total: 500.0" in out
    assert out.rstrip().endswith("Reply **Yes** to confirm, or **No** to cancel.")
    assert env.erp.converts() == [] and env.erp.orders() == [], "nothing is created before the Yes"
    pending = env.persisted()["pending_confirmation"]
    assert pending["operation"] == "convert" and pending["requested_by"] == "alice@example.com" and pending["target_modified"] == QUOTE["modified"]

    out = env.text("yes", roles=SALES)
    assert "FORMATTED[convert-quotation-to-sales-order]" in out
    (call,) = env.erp.converts()
    assert call[1:] == ("Sales Order", "Quotation", Q)
    (order,) = env.erp.orders()
    assert order["docstatus"] == 0 and order["items"][0]["prevdoc_docname"] == Q
    assert "Submit it now?" not in out, "a Sales User cannot submit"
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_a_manager_is_then_offered_the_submit_and_saying_yes_submits_the_new_order(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=MANAGER)
    out = env.text("yes", roles=MANAGER)
    assert "**Submit it now?**" in out and "Sales Order 'SAL-ORD-2026-00201'" in out
    pending = env.persisted()["pending_confirmation"]
    assert pending["skill"] == "submit-sales-order" and pending["target_name"] == "SAL-ORD-2026-00201" and pending["is_offer"] is True
    assert env.erp.orders()[0]["docstatus"] == 0, "the order stays a Draft until the manager says yes"


def test_e2e_saying_no_creates_nothing(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=SALES)
    out = env.text("no", roles=SALES)
    assert "cancelled" in out.lower() and f"No Sales Order was created from '{Q}'." in out
    assert env.erp.converts() == [] and env.erp.orders() == []


def test_e2e_orders_already_made_from_the_quotation_are_mentioned_before_asking(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=SALES)
    env.text("yes", roles=SALES)
    out = env.text(msg, roles=SALES, conversation_id="conv-2")
    assert "Sales Order records were already made from this Quotation: SAL-ORD-2026-00201" in out
    assert "Converting again creates another one." in out
    assert len(env.erp.orders()) == 1, "asking again does not create a second order"


def test_e2e_a_draft_quotation_is_refused_without_asking(env):
    msg = f"convert quotation {Q_DRAFT} to a sales order"
    script(env, msg, Q_DRAFT)
    out = env.text(msg, roles=SALES)
    assert f"Quotation '{Q_DRAFT}' is Draft. Only a submitted Quotation can be converted into a Sales Order." in out
    assert env.erp.converts() == [] and env.persisted()["pending_confirmation"] is None


def test_e2e_a_cancelled_or_missing_quotation_is_refused(env):
    msg = f"convert quotation {Q_CANCELLED} to a sales order"
    script(env, msg, Q_CANCELLED)
    assert "is Cancelled" in env.text(msg, roles=SALES)
    msg2 = "convert quotation SAL-QTN-2026-09999 to a sales order"
    script(env, msg2, "SAL-QTN-2026-09999")
    missing = env.text(msg2, roles=SALES, conversation_id="conv-2")
    assert "I couldn't find a Quotation matching 'SAL-QTN-2026-09999'." in missing
    assert "Sales Order" not in missing, "what is missing is the quotation, not an order"
    assert env.erp.converts() == []


def test_e2e_purchasing_stock_and_accounts_roles_cannot_convert(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    for i, roles in enumerate((["Purchase Manager"], ["Stock Manager"], ["Accounts User"])):
        assert "Denied" in env.text(msg, roles=roles, conversation_id=f"c{i}")
    assert env.erp.converts() == []


def test_e2e_what_the_quotation_says_is_all_that_is_used_not_what_was_typed(env):
    msg = f"convert quotation {Q} to a sales order for Evil Corp with 999 DESK at 1"
    env.llm.classify[msg] = {"intent": "convert-quotation-to-sales-order", "is_compound": False}
    env.llm.extract[msg] = {"quotation": Q, "customer": "Evil Corp", "rate": 1, "grand_total": 1}
    out = env.text(msg, roles=SALES)
    assert "For: Acme Corp" in out and "Evil Corp" not in out
    env.text("yes", roles=SALES)
    assert env.erp.converts() == [("convert_document", "Sales Order", "Quotation", Q)]
    (order,) = env.erp.orders()
    assert order["customer"] == "Acme Corp" and order["items"][0]["qty"] == 4.0


def test_e2e_a_quotation_changed_between_the_question_and_the_yes_is_refused(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=SALES)
    env.erp.db["Quotation"][0]["modified"] = "2026-11-20 11:00:00.000009"  # a colleague changes it
    out = env.text("yes", roles=SALES)
    assert f"Quotation '{Q}' was changed after you were asked to confirm" in out
    assert env.erp.converts() == []


def test_e2e_a_quotation_cancelled_between_the_question_and_the_yes_is_refused(env):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=SALES)
    env.erp.db["Quotation"][0].update(docstatus=2, status="Cancelled")
    out = env.text("yes", roles=SALES)
    assert "is Cancelled" in out
    assert env.erp.converts() == []


def test_e2e_a_failed_conversion_is_not_retried(env, monkeypatch):
    msg = f"convert quotation {Q} to a sales order"
    script(env, msg, Q)
    env.text(msg, roles=SALES)
    calls = []
    monkeypatch.setattr(graph, "convert_document", lambda *a, **k: (calls.append(a) or _res("system_error", error="HTTP 502")))
    out = env.text("yes", roles=SALES)
    assert "HTTP 502" in out and "not retried automatically" in out
    assert len(calls) == 1


def test_e2e_a_partial_conversion_shows_only_the_named_items_and_creates_only_those(env):
    msg = f"create a sales order from quotation {Q} with only 2 DESK"
    env.llm.classify[msg] = {"intent": "convert-quotation-to-sales-order", "is_compound": False}
    env.llm.extract[msg] = {"quotation": Q, "items": [{"item_code": "DESK", "qty": 2}]}
    out = env.text(msg, roles=SALES)
    assert "- 2x DESK (of 4.0 on the Quotation)" in out and "CHAIR" not in out and "Total:" not in out
    assert "Only the items listed are included" in out
    env.text("yes", roles=SALES)
    (order,) = env.erp.orders()
    assert [(i["item_code"], i["qty"]) for i in order["items"]] == [("DESK", 2)]
    assert env.erp.converts() == [("convert_document", "Sales Order", "Quotation", Q, [{"item_code": "DESK", "qty": 2}])]


def test_e2e_asking_for_more_than_the_quotation_has_is_refused_before_asking(env):
    msg = f"create a sales order from quotation {Q} with 999 DESK"
    env.llm.classify[msg] = {"intent": "convert-quotation-to-sales-order", "is_compound": False}
    env.llm.extract[msg] = {"quotation": Q, "items": [{"item_code": "DESK", "qty": 999}]}
    out = env.text(msg, roles=SALES)
    assert "999" in out and "only 4" in out
    assert env.erp.converts() == [] and env.erp.orders() == []


def test_e2e_an_item_that_is_not_on_the_quotation_is_refused(env):
    env.erp.db["Quotation"][0]["items"] = [i for i in env.erp.db["Quotation"][0]["items"] if i["item_code"] != "CHAIR"]
    msg = f"create a sales order from quotation {Q} with 1 CHAIR"
    env.llm.classify[msg] = {"intent": "convert-quotation-to-sales-order", "is_compound": False}
    env.llm.extract[msg] = {"quotation": Q, "items": [{"item_code": "CHAIR", "qty": 1}]}
    out = env.text(msg, roles=SALES)
    assert "CHAIR is not on Quotation" in out
    assert env.erp.converts() == []
