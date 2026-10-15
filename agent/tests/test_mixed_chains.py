"""
agent/tests/test_mixed_chains.py

End-to-end tests for mixed read/write compound requests across the confirmation
boundary. Each test drives the real FastAPI /chat endpoint (JWT auth, SQLite
persistence between requests, the compiled LangGraph) with:
  - an in-memory ERPNext replacing the generic tool functions, and
  - a scripted LLM (classifier / planner / extractor / formatter) keyed by message,
so every turn is deterministic and runs offline.
"""

import copy
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import audit_logger
import chat_store
import main
import workflow.followup_router as followup_router
import workflow.graph as graph

SECRET = "mixed-chain-test-secret-key-0123456789abcdef"
PO1 = "PUR-ORD-2026-00001"
PO2 = "PUR-ORD-2026-00002"
SM = ["System Manager"]


# ---------------------------------------------------------------------------
# In-memory ERPNext
# ---------------------------------------------------------------------------

def _res(status, data=None, error=None):
    return {"status": status, "data": data, "error": error, "has_more": False,
            "success": status in ("success", "empty")}


class FakeERP:
    def __init__(self):
        self.db = {
            "Supplier": [{"name": "Zuckerman Security Ltd.", "supplier_name": "Zuckerman Security Ltd.", "supplier_group": "Hardware"}],
            "Item": [
                {"name": "RM-001", "item_code": "RM-001", "item_name": "Raw Material 1", "item_group": "Raw Material"},
                {"name": "MS-001", "item_code": "MS-001", "item_name": "Mild Steel 1", "item_group": "Raw Material"},
                {"name": "ITEM-DESK-001", "item_code": "ITEM-DESK-001", "item_name": "Office Desk", "item_group": "Products"},
            ],
            "Bin": [
                {"name": "BIN-1", "item_code": "MS-001", "warehouse": "Stores - TC", "actual_qty": 42.0},
                {"name": "BIN-2", "item_code": "RM-001", "warehouse": "Stores - TC", "actual_qty": 7.0},
                {"name": "BIN-3", "item_code": "ITEM-DESK-001", "warehouse": "Stores - TC", "actual_qty": 3.0},
            ],
            "Purchase Order": [
                {"name": PO1, "supplier": "Zuckerman Security Ltd.", "status": "Draft", "docstatus": 0,
                 "items": [{"item_code": "RM-001", "qty": 5}]},
            ],
            "Customer": [],
        }
        self.calls = []

    @staticmethod
    def _match(row, flt):
        field, op, val = flt
        cur = row.get(field)
        if op == "=":
            return str(cur) == str(val)
        if op == "like":
            return str(val).strip("%").lower() in str(cur or "").lower()
        if op == "in":
            return cur in val
        return True

    def get_list(self, doctype, filters=None, fields=None, limit=None):
        self.calls.append(("get_list", doctype, filters))
        rows = [r for r in self.db.get(doctype, []) if all(self._match(r, f) for f in (filters or []))]
        if fields:
            rows = [{k: r.get(k) for k in fields if k in r} for r in rows]
        rows = copy.deepcopy(rows[: (limit or 20)])
        return _res("success" if rows else "empty", rows)

    def search_document(self, doctype, query):
        field = {"Item": "item_name", "Supplier": "supplier_name", "Customer": "customer_name"}.get(doctype, "name")
        return self.get_list(doctype, [[field, "like", f"%{query}%"]])

    def get_document(self, doctype, name):
        self.calls.append(("get_document", doctype, name))
        for r in self.db.get(doctype, []):
            if r.get("name") == name:
                return _res("success", copy.deepcopy(r))
        return _res("not_found", error=f"{doctype} {name} not found")

    def create_document(self, doctype, fields):
        self.calls.append(("create_document", doctype, fields))
        name = f"PUR-ORD-2026-{len(self.db[doctype]) + 1:05d}"
        doc = dict(copy.deepcopy(fields), name=name, docstatus=0, status="Draft")
        self.db[doctype].append(doc)
        return _res("success", copy.deepcopy(doc))

    def _set_docstatus(self, op, doctype, name, docstatus):
        self.calls.append((op, doctype, name))
        for r in self.db.get(doctype, []):
            if r.get("name") == name:
                r["docstatus"] = docstatus
                r["status"] = {1: "To Receive and Bill", 2: "Cancelled"}[docstatus]
                return _res("success", copy.deepcopy(r))
        return _res("not_found", error="not found")

    def submit_document(self, doctype, name):
        return self._set_docstatus("submit_document", doctype, name, 1)

    def cancel_document(self, doctype, name):
        return self._set_docstatus("cancel_document", doctype, name, 2)

    def get_count(self, doctype, filters=None):
        return _res("success", len([r for r in self.db.get(doctype, []) if all(self._match(r, f) for f in (filters or []))]))

    def po(self, name):
        return next(r for r in self.db["Purchase Order"] if r["name"] == name)

    def writes(self):
        return [c for c in self.calls if c[0] in ("create_document", "submit_document", "cancel_document")]

    def bin_reads(self):
        return [c for c in self.calls if c[0] == "get_list" and c[1] == "Bin"]


# ---------------------------------------------------------------------------
# Scripted LLM
# ---------------------------------------------------------------------------

def _between(prompt, start, end):
    i = prompt.index(start) + len(start)
    return prompt[i:prompt.index(end, i)]


class ScriptedLLM:
    def __init__(self):
        self.classify = {}   # message -> classifier JSON
        self.plans = {}      # message -> list of steps
        self.extract = {}    # message -> extracted fields
        self.followup = {}   # message -> follow-up JSON
        self.calls = []

    def structured(self, prompt, *args, **kwargs):
        if prompt.startswith("Classify the user message intent"):
            msg = _between(prompt, "User message: '", "'")
            self.calls.append(("classify", msg))
            return copy.deepcopy(self.classify.get(msg, {"intent": "fallback", "is_compound": False}))
        if prompt.startswith("You are a compound request planner"):
            msg = _between(prompt, 'User request: "', '"\n')
            self.calls.append(("plan", msg))
            return {"steps": copy.deepcopy(self.plans[msg])}
        if prompt.startswith("You are a precise JSON entity extractor"):
            msg = _between(prompt, "User message: '", "'\n")
            self.calls.append(("extract", msg))
            return copy.deepcopy(self.extract.get(msg, {}))
        if prompt.startswith("You are a slot-override extractor"):
            msg = _between(prompt, "follow-up message: '", "'")
            self.calls.append(("followup", msg))
            return copy.deepcopy(self.followup.get(msg, {"is_followup": False}))
        raise AssertionError(f"Unexpected structured LLM prompt: {prompt[:80]!r}")

    def text(self, prompt, *args, **kwargs):
        self.calls.append(("text", prompt.splitlines()[0]))
        skill = next((l for l in prompt.splitlines() if l.startswith("Skill: ")), "Skill: ?")
        return f"FORMATTED[{skill[7:]}]"


# ---------------------------------------------------------------------------
# Fixtures / driver
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()

    erp = FakeERP()
    for name in ("get_list", "search_document", "get_document", "create_document",
                 "submit_document", "cancel_document", "get_count"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)

    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


class Driver:
    def __init__(self, client, erp, llm):
        self.client, self.erp, self.llm = client, erp, llm

    def say(self, message, user="alice@example.com", roles=SM, conversation_id="conv-1", max_llm_calls=3):
        token = jwt.encode({"sub": user, "full_name": user, "roles": roles,
                            "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
        before = len(self.llm.calls)
        resp = self.client.post("/chat", json={"message": message, "conversation_id": conversation_id},
                                headers={"Authorization": f"Bearer {token}"})
        llm_calls = len(self.llm.calls) - before
        assert llm_calls <= max_llm_calls, f"{llm_calls} LLM calls for {message!r}: {self.llm.calls[before:]}"
        return resp

    def text(self, message, **kw):
        resp = self.say(message, **kw)
        assert resp.status_code == 200, resp.text
        return resp.json()["response"]

    def persisted(self, conversation_id="conv-1"):
        return chat_store.load_session_state(conversation_id, SM)


def _compound(llm, msg, first_intent, steps):
    llm.classify[msg] = {"intent": first_intent, "is_compound": True, "pending_followup_steps": []}
    llm.plans[msg] = steps


ZUCK_ITEMS = {"supplier": "Zuckerman Security Ltd.", "items": [{"item_code": "RM-001", "qty": 10, "rate": 100}]}


def _setup_po_submit_stock(llm):
    msg = "create a purchase order for Zuckerman Security Ltd. for 10 RM-001 at 100, submit it, and check stock for MS-001"
    _compound(llm, msg, "create-purchase-order", [
        {"skill": "create-purchase-order", "slots": copy.deepcopy(ZUCK_ITEMS), "uses": {}, "condition": None},
        {"skill": "submit-purchase-order", "slots": {}, "uses": {"name": "$step0.name"}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    return msg


# ---------------------------------------------------------------------------
# PO + stock
# ---------------------------------------------------------------------------

def test_po_submit_stock_turn1_shows_draft_stock_and_prompt_last(env):
    msg = _setup_po_submit_stock(env.llm)
    out = env.text(msg)

    created = out.index(f"Successfully created Purchase Order: {PO2}")
    stock = out.index("MS-001")
    prompt = out.index(f"Are you sure you want to submit Purchase Order '{PO2}'?")
    assert created < stock < prompt, out
    assert "42.0" in out
    assert out.rstrip().endswith("Reply **Yes** to confirm, or **No** to cancel.")
    assert "Bin" not in out.split("Are you sure")[1]  # no stale doctype in the prompt

    # Draft created, never submitted without confirmation.
    assert env.erp.po(PO2)["docstatus"] == 0
    assert [c[0] for c in env.erp.writes()] == ["create_document"]
    pending = env.persisted()["pending_confirmation"]
    assert pending["operation"] == "submit" and pending["target_name"] == PO2
    assert pending["requested_by"] == "alice@example.com"


def test_po_submit_stock_yes_submits_and_cleans_up(env):
    env.text(_setup_po_submit_stock(env.llm))
    bin_reads = len(env.erp.bin_reads())

    out = env.text("yes")
    assert f"Successfully submitted Purchase Order: {PO2}!" in out
    assert env.erp.po(PO2)["docstatus"] == 1
    assert len(env.erp.bin_reads()) == bin_reads, "completed read steps must not re-run"
    assert "42.0" not in out, "already-reported results are not repeated"

    state = env.persisted()
    assert state["pending_confirmation"] is None
    assert state["chain_plan"] is None and state["chain_step_status"] is None


def test_po_submit_stock_no_cancels_and_keeps_draft(env):
    env.text(_setup_po_submit_stock(env.llm))
    bin_reads = len(env.erp.bin_reads())

    out = env.text("no")
    assert "cancelled" in out.lower()
    assert f"Purchase Order '{PO2}' was not submitted and remains a Draft." in out
    assert env.erp.po(PO2)["docstatus"] == 0
    assert [c[0] for c in env.erp.writes()] == ["create_document"]
    assert len(env.erp.bin_reads()) == bin_reads

    state = env.persisted()
    assert state["pending_confirmation"] is None and state["chain_plan"] is None


def test_plain_create_and_stock_needs_no_confirmation(env):
    msg = "create a purchase order for Zuckerman Security Ltd. for 10 RM-001 at 100 and also check stock for MS-001"
    _compound(env.llm, msg, "create-purchase-order", [
        {"skill": "create-purchase-order", "slots": copy.deepcopy(ZUCK_ITEMS), "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    out = env.text(msg)
    assert f"Successfully created Purchase Order: {PO2}" in out and "Draft" in out
    assert "42.0" in out
    assert "Reply **Yes**" not in out
    assert env.persisted()["pending_confirmation"] is None


def test_stock_then_submit_reverse_order(env):
    msg = f"check stock for MS-001 and submit purchase order {PO1}"
    _compound(env.llm, msg, "check-inventory", [
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
    ])
    out = env.text(msg)
    assert out.index("42.0") < out.index(f"Are you sure you want to submit Purchase Order '{PO1}'?")
    assert "submit Bin" not in out

    out2 = env.text("yes")
    assert f"Successfully submitted Purchase Order: {PO1}!" in out2
    assert env.erp.po(PO1)["docstatus"] == 1


def test_read_read_regression_shows_both_results_exactly(env):
    msg = "check stock for RM-001 and MS-001"
    _compound(env.llm, msg, "check-inventory", [
        {"skill": "check-inventory", "slots": {"item_code": "RM-001"}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    out = env.text(msg)
    assert out.index("RM-001") < out.index("MS-001")
    assert "7.0" in out and "42.0" in out
    assert "No stock" not in out
    assert env.persisted()["chain_plan"] is None


# ---------------------------------------------------------------------------
# Multiple destructive steps, failures, RBAC, ownership
# ---------------------------------------------------------------------------

def test_two_destructive_steps_confirmed_separately(env):
    msg = f"submit purchase order {PO1} and then cancel it"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        {"skill": "cancel-purchase-order", "slots": {}, "uses": {"name": "$step0.name"}, "condition": None},
    ])
    out1 = env.text(msg)
    assert f"submit Purchase Order '{PO1}'" in out1
    assert "After that I will also run: Cancel purchase order" in out1
    assert env.erp.writes() == []

    out2 = env.text("yes")
    assert f"Successfully submitted Purchase Order: {PO1}!" in out2
    assert f"Are you sure you want to cancel Purchase Order '{PO1}'?" in out2
    assert env.erp.po(PO1)["docstatus"] == 1  # cancel not executed yet

    out3 = env.text("yes")
    assert f"Successfully cancelled Purchase Order: {PO1}!" in out3
    assert env.erp.po(PO1)["docstatus"] == 2
    assert [c[0] for c in env.erp.writes()] == ["submit_document", "cancel_document"]


def test_no_on_first_destructive_skips_dependent_second(env):
    msg = f"submit purchase order {PO1} and then cancel it"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        {"skill": "cancel-purchase-order", "slots": {}, "uses": {"name": "$step0.name"}, "condition": None},
    ])
    env.text(msg)
    out = env.text("no")
    assert "cancelled" in out.lower() and "skipped" in out.lower()
    assert env.erp.writes() == []
    assert env.persisted()["chain_plan"] is None


def test_mid_chain_failure_reports_and_continues(env):
    msg = "check stock for NOPE-999 and check stock for MS-001"
    _compound(env.llm, msg, "check-inventory", [
        {"skill": "check-inventory", "slots": {"item_code": "NOPE-999"}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    out = env.text(msg)
    assert "NOPE-999" in out
    assert "No stock found" in out  # the skill's own not_found_message, shown verbatim
    assert "42.0" in out, "the independent step must still run"
    assert out.index("NOPE-999") < out.index("42.0")


def test_missing_details_in_one_step_do_not_block_others(env):
    msg = "create a purchase order for 10 RM-001 and also check stock for MS-001"
    _compound(env.llm, msg, "create-purchase-order", [
        {"skill": "create-purchase-order", "slots": {"items": [{"item_code": "RM-001", "qty": 10}]}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    out = env.text(msg)
    assert "Missing required details: supplier" in out
    assert "42.0" in out
    assert env.erp.writes() == []


def test_rbac_denied_on_yes_blocks_destructive_action(env):
    msg = f"submit purchase order {PO1} and also check stock for MS-001"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    out1 = env.text(msg)
    assert "42.0" in out1 and "Reply **Yes**" in out1

    # Same user, but the role was revoked before the reply: RBAC re-runs at execution time.
    out2 = env.text("yes", roles=["Purchase User"])
    assert "Permission Denied" in out2
    assert env.erp.po(PO1)["docstatus"] == 0
    assert env.erp.writes() == []
    assert env.persisted()["pending_confirmation"] is None


def test_other_user_cannot_load_or_confirm(env):
    msg = f"submit purchase order {PO1} and also check stock for MS-001"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    env.text(msg)

    resp = env.say("yes", user="bob@example.com", roles=SM)  # bob has the role, but not the conversation
    assert resp.status_code == 403
    assert env.erp.writes() == []
    assert env.persisted()["pending_confirmation"] is not None  # alice can still answer

    assert f"Successfully submitted Purchase Order: {PO1}!" in env.text("yes")


def test_gate_refuses_pending_requested_by_someone_else():
    """Defense in depth inside the graph, independent of the HTTP ownership check."""
    pending = {"operation": "submit", "doctype": "Purchase Order", "target_name": PO1, "skill": "submit-purchase-order",
               "params": {"name": PO1}, "requested_by": "alice@example.com",
               "requested_at_ts": datetime.now(timezone.utc).isoformat(), "scope_description": "Submit?"}
    res = graph.pending_action_gate_node({
        "messages": [HumanMessage(content="yes")], "pending_confirmation": pending, "user_sub": "bob@example.com",
    })
    assert res["gate_route"] == "format_response"
    assert "Only that user" in res["final_response"]
    assert "pending_confirmation" not in res  # left in place for alice
    assert res.get("confirmation_result") is None


def test_gate_discards_expired_pending_without_executing():
    pending = {"operation": "submit", "doctype": "Purchase Order", "target_name": PO1, "skill": "submit-purchase-order",
               "params": {"name": PO1}, "requested_by": "alice@example.com",
               "requested_at_ts": (datetime.now(timezone.utc) - timedelta(minutes=graph.PENDING_CONFIRMATION_MAX_MINUTES + 1)).isoformat(),
               "scope_description": "Submit?"}
    res = graph.pending_action_gate_node({
        "messages": [HumanMessage(content="yes")], "pending_confirmation": pending, "user_sub": "alice@example.com",
    })
    assert res["gate_route"] == "format_response"
    assert res["pending_confirmation"] is None
    assert "expired" in res["final_response"]
    assert res.get("confirmation_result") is None


@pytest.mark.parametrize("reply,kind", [
    ("yes", "yes"), ("Yes.", "yes"), ("yes, proceed", "yes"), ("OK", "yes"),
    ("no", "no"), ("No!", "no"), ("cancel", "no"),
    (f"cancel purchase order {PO1}", "other"), ("yes but change the qty", "other"), ("hmm", "other"),
])
def test_confirmation_reply_parsing_is_exact(reply, kind):
    assert graph._classify_confirmation_reply(reply) == kind


# ---------------------------------------------------------------------------
# Unrelated messages while a confirmation is pending
# ---------------------------------------------------------------------------

def test_pending_then_unrelated_query(env):
    _setup_po_submit_stock(env.llm)
    env.text("create a purchase order for Zuckerman Security Ltd. for 10 RM-001 at 100, submit it, and check stock for MS-001")

    lookup = f"show purchase order {PO1}"
    env.llm.classify[lookup] = {"intent": "purchase-order-lookup", "is_compound": False}
    env.llm.extract[lookup] = {"name": PO1}

    # First non-yes/no reply: re-ask once; nothing executed, the new query is not run.
    out1 = env.text(lookup)
    assert "I need a Yes or No" in out1
    assert env.erp.po(PO2)["docstatus"] == 0
    assert env.persisted()["pending_confirmation"]["reask_count"] == 1

    # Second: the pending submit is cancelled unexecuted and the new query is handled.
    out2 = env.text(lookup)
    assert "Cancelled the pending action without executing it" in out2
    assert "FORMATTED[purchase-order-lookup]" in out2
    assert env.erp.po(PO2)["docstatus"] == 0
    state = env.persisted()
    assert state["pending_confirmation"] is None and state["chain_plan"] is None


# ---------------------------------------------------------------------------
# Dependent step resumes after confirmation using its stored slots
# ---------------------------------------------------------------------------

def test_dependent_step_waits_and_resumes_after_yes(env):
    msg = f"submit purchase order {PO1} and then show its status"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        # The planner sometimes puts the reference in slots; it must be treated as a dependency.
        {"skill": "purchase-order-lookup", "slots": {"name": "$step0.name"}, "uses": {}, "condition": None},
    ])
    out1 = env.text(msg)
    assert "After that I will also run: Purchase order lookup" in out1
    assert "To Receive and Bill" not in out1

    extract_calls = len([c for c in env.llm.calls if c[0] == "extract"])
    out2 = env.text("yes")
    assert f"Successfully submitted Purchase Order: {PO1}!" in out2
    assert "To Receive and Bill" in out2
    assert out2.index("Successfully submitted") < out2.index("To Receive and Bill")
    assert len([c for c in env.llm.calls if c[0] == "extract"]) == extract_calls, "resumed steps never re-extract from 'yes'"


def test_dependent_read_without_explicit_reference_targets_same_document(env):
    msg = f"submit purchase order {PO1} and then look it up"
    _compound(env.llm, msg, "submit-purchase-order", [
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
        {"skill": "purchase-order-lookup", "slots": {}, "uses": {}, "condition": None},
    ])
    env.text(msg)
    out = env.text("yes")
    assert "To Receive and Bill" in out


# ---------------------------------------------------------------------------
# Follow-up after a read turn (follow-up router now actually routes)
# ---------------------------------------------------------------------------

def test_followup_question_reuses_previous_skill(env):
    first = "check stock for RM-001"
    env.llm.extract[first] = {"item_code": "RM-001"}
    out1 = env.text(first)
    assert "FORMATTED[check-inventory]" in out1

    follow = "what about ITEM-DESK-001?"
    env.llm.followup[follow] = {"is_followup": True, "overrides": {"item_code": "ITEM-DESK-001"}}
    env.llm.extract[follow] = {}
    out2 = env.text(follow)
    assert "FORMATTED[check-inventory]" in out2
    assert any(c[1] == "Bin" and c[2] == [["item_code", "=", "ITEM-DESK-001"]] for c in env.erp.calls if c[0] == "get_list")
    assert not any(c == ("classify", follow) for c in env.llm.calls), "follow-up must bypass the classifier"


# ---------------------------------------------------------------------------
# Regression: live run where a short message after a stock check was hijacked by
# the follow-up router and the next compound request was swallowed as a slot value.
# ---------------------------------------------------------------------------

def test_bare_yes_after_completed_turn_and_next_compound_request(env):
    reads = "check stock for RM-001 and MS-001"
    _compound(env.llm, reads, "check-inventory", [
        {"skill": "check-inventory", "slots": {"item_code": "RM-001"}, "uses": {}, "condition": None},
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
    ])
    env.text(reads)

    out_yes = env.text("yes", max_llm_calls=0)
    assert "no action waiting for confirmation" in out_yes
    assert env.erp.writes() == []

    mixed = f"check stock for MS-001 and submit purchase order {PO1}"
    _compound(env.llm, mixed, "check-inventory", [
        {"skill": "check-inventory", "slots": {"item_code": "MS-001"}, "uses": {}, "condition": None},
        {"skill": "submit-purchase-order", "slots": {"name": PO1}, "uses": {}, "condition": None},
    ])
    out = env.text(mixed)
    assert f"Are you sure you want to submit Purchase Order '{PO1}'?" in out
    assert not any(c[0] == "followup" for c in env.llm.calls), "new requests must not go to the follow-up extractor"


def test_short_new_request_after_read_skips_followup_extractor():
    state = {
        "messages": [HumanMessage(content=f"check stock for MS-001 and submit purchase order {PO1}")],
        "pending_confirmation": None,
        "pending_slot_clarification": None,
        "last_turn_context": {
            "skill": "check-inventory", "slots": {"item_code": "RM-001"}, "turn_id": 1,
            "completed_at": datetime.now(timezone.utc).isoformat(), "follow_up_slots": ["item_code", "warehouse"],
        },
    }
    def _no_llm(*a, **k):
        raise AssertionError("follow-up extractor must not run")
    original = followup_router.invoke_structured_llm
    followup_router.invoke_structured_llm = _no_llm
    try:
        res = followup_router.resolve_followup_node(state)
    finally:
        followup_router.invoke_structured_llm = original
    assert res["_followup_route"] == "classify_intent"


def test_free_text_slot_question_does_not_swallow_a_new_request():
    state = {
        "messages": [HumanMessage(content=f"check stock for MS-001 and submit purchase order {PO1}")],
        "pending_confirmation": None,
        "last_turn_context": None,
        "pending_slot_clarification": {
            "origin_skill": "check-inventory", "origin_slots": {"item_code": "RM-001"},
            "missing_slot": "item_code", "question_asked": "What would you like to change?",
            "options": None, "asked_turn_id": 3, "attempts": 0,
        },
    }
    res = followup_router.resolve_followup_node(state)
    assert res["_followup_route"] == "classify_intent"
    assert res["pending_slot_clarification"] is None
