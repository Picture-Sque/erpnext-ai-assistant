"""
End-to-end tests for updating a Sales Order through the real /chat endpoint (JWT, persisted
conversation state, the compiled LangGraph), with an in-memory ERPNext and a scripted LLM.
They reuse the harness of test_mixed_chains.py.

Scenarios: a quantity and rate change is previewed, confirmed and verified; a date change
still runs without a question; changes that must not run (submitted order, wrong role, the
order edited between the question and the Yes) do not reach ERPNext.
"""

import copy
import os
import sys

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
from mcp_erpnext.permissions import ALLOWED, DENIED, PermissionCheck

SALES_USER = ["Sales User"]
DRAFT, SUBMITTED = "SAL-ORD-2026-00040", "SAL-ORD-2026-00041"


class SalesERP(FakeERP):
    """The fake ERPNext, plus Sales Orders that can be updated the way ERPNext updates them."""

    def __init__(self):
        super().__init__()
        self.version = 0
        self.denied = set()
        self.db["Sales Order"] = [self._order(DRAFT, 0), self._order(SUBMITTED, 1)]

    def _order(self, name, docstatus):
        return {
            "name": name, "customer": "Acme", "docstatus": docstatus, "status": "Draft" if docstatus == 0 else "To Deliver and Bill",
            "modified": "2026-11-20 10:00:00.000001", "delivery_date": "2026-11-01", "po_no": "PO-OLD",
            "items": [
                {"name": f"{name}-1", "item_code": "ITEM-DESK-001", "qty": 2, "rate": 100},
                {"name": f"{name}-2", "item_code": "MS-001", "qty": 4, "rate": 50},
            ],
        }

    def order(self, name):
        return next(r for r in self.db["Sales Order"] if r["name"] == name)

    def touch(self, name):
        """Someone else edits the order."""
        self.version += 1
        self.order(name)["modified"] = f"2026-11-20 11:00:00.{self.version:06d}"

    def update_document(self, doctype, name, fields, expected_modified=None):
        self.calls.append(("update_document", doctype, name, copy.deepcopy(fields), expected_modified))
        doc = self.order(name)
        if expected_modified and expected_modified != doc["modified"]:
            return _res("system_error", error=f"Conflict: {doctype} {name} was changed by someone else after it was last read, so nothing was changed.")
        for key, value in fields.items():
            if key == "item_updates":
                for edit in value:
                    row = next(r for r in doc["items"] if r["item_code"] == edit["item_code"])
                    row.update({k: v for k, v in edit.items() if k != "item_code"})
            else:
                doc[key] = value
        if "delivery_date" in fields:
            # crud.update_document copies a new delivery date onto every item row.
            for row in doc["items"]:
                row["delivery_date"] = fields["delivery_date"]
        self.version += 1
        doc["modified"] = f"2026-11-20 12:00:00.{self.version:06d}"
        return _res("success", copy.deepcopy(doc))

    def updates(self):
        return [c for c in self.calls if c[0] == "update_document"]

    def check_permission(self, operation, doctype, name=None):
        """What ERPNext says about the signed-in user. Add an operation to `denied` to revoke it."""
        self.calls.append(("check_permission", operation, doctype, name))
        return PermissionCheck(DENIED if operation in self.denied else ALLOWED)

    denied: set = set()


class CapturingLLM(ScriptedLLM):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def text(self, prompt, *args, **kwargs):
        self.prompts.append(prompt)
        return super().text(prompt, *args, **kwargs)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()

    erp = SalesERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "update_document",
                 "submit_document", "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    monkeypatch.setattr(graph, "check_permission", erp.check_permission)
    llm = CapturingLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)

    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


def script(env, message, fields):
    env.llm.classify[message] = {"intent": "update-sales-order", "is_compound": False}
    env.llm.extract[message] = fields


QTY_MSG = "change the quantity of ITEM-DESK-001 to 12 and the rate to 90 on {}"


def test_quantity_and_rate_change_is_previewed_confirmed_and_verified(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12, "rate": 90}]})

    out = env.text(msg, roles=SALES_USER)
    assert f"Are you sure you want to update Sales Order '{DRAFT}'?" in out
    assert "Changes:" in out
    assert "- ITEM-DESK-001: qty from 2 to 12, rate from 100 to 90" in out
    assert out.rstrip().endswith("Reply **Yes** to confirm, or **No** to cancel.")
    assert env.erp.updates() == [], "nothing is written before the Yes"
    pending = env.persisted()["pending_confirmation"]
    assert pending["operation"] == "update" and pending["target_name"] == DRAFT
    assert pending["requested_by"] == "alice@example.com"

    out = env.text("yes", roles=SALES_USER)
    assert out == "FORMATTED[update-sales-order]"
    (call,) = env.erp.updates()
    assert call[1:4] == ("Sales Order", DRAFT, {"item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12, "rate": 90}]})
    assert call[4] == "2026-11-20 10:00:00.000001", "the write carries the version the user was shown"
    row = env.erp.order(DRAFT)["items"][0]
    assert (row["qty"], row["rate"]) == (12, 90)
    assert env.erp.order(DRAFT)["items"][1]["qty"] == 4, "other rows are untouched"
    assert env.persisted()["pending_confirmation"] is None


def test_saying_no_changes_nothing(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})
    env.text(msg, roles=SALES_USER)

    out = env.text("no", roles=SALES_USER)
    assert "cancelled" in out.lower()
    assert env.erp.updates() == []
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 2
    assert env.persisted()["pending_confirmation"] is None


def test_delivery_date_change_still_runs_without_a_question(env):
    msg = f"move the delivery of {DRAFT} to 2026-12-01"
    script(env, msg, {"name": DRAFT, "delivery_date": "2026-12-01"})

    out = env.text(msg, roles=SALES_USER)
    assert out == "FORMATTED[update-sales-order]"
    (call,) = env.erp.updates()
    assert call[3] == {"delivery_date": "2026-12-01"}
    assert env.erp.order(DRAFT)["delivery_date"] == "2026-12-01"


def test_po_reference_change_runs_without_a_question(env):
    msg = f"set the customer PO on {DRAFT} to PO-8841"
    script(env, msg, {"name": DRAFT, "po_no": "PO-8841"})
    assert env.text(msg, roles=SALES_USER) == "FORMATTED[update-sales-order]"
    assert env.erp.order(DRAFT)["po_no"] == "PO-8841"


def test_item_changes_on_a_submitted_order_never_reach_erpnext(env):
    msg = QTY_MSG.format(SUBMITTED)
    script(env, msg, {"name": SUBMITTED, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})

    out = env.text(msg, roles=SALES_USER)
    assert "is Submitted" in out and "only allowed on a Draft" in out
    assert env.erp.updates() == []
    assert env.persisted()["pending_confirmation"] is None


def test_a_role_without_edit_rights_is_refused_before_anything_else(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})

    out = env.text(msg, roles=["Accounts User"])
    assert "Denied" in out
    assert "Sales User, Sales Manager, System Manager, Administrator" in out
    assert env.erp.updates() == []
    assert not [c for c in env.erp.calls if c[0] == "get_document"], "the order is not even read"
    assert env.persisted()["pending_confirmation"] is None


def test_an_edit_between_the_question_and_the_yes_is_refused(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})
    env.text(msg, roles=SALES_USER)

    env.erp.touch(DRAFT)  # a colleague saves the order while the question is open
    out = env.text("yes", roles=SALES_USER)
    assert "was changed after you were asked to confirm" in out
    assert "Nothing was executed" in out
    assert env.erp.updates() == []
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 2


def test_erpnext_refusing_a_stale_write_is_reported_as_a_conflict_and_not_retried(env, monkeypatch):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})
    env.text(msg, roles=SALES_USER)

    real = env.erp.update_document

    def edited_just_before_the_write(doctype, name, fields, expected_modified=None):
        env.erp.touch(name)  # lands after the preconditions read the order, before the write
        return real(doctype, name, fields, expected_modified=expected_modified)

    monkeypatch.setattr(graph, "update_document", edited_just_before_the_write)
    out = env.text("yes", roles=SALES_USER)
    assert "changed by someone else" in out
    assert "review the current document" in out
    assert len(env.erp.updates()) == 1, "a conflict is never retried"
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 2


def test_nothing_to_change_asks_what_to_change(env):
    msg = f"update {DRAFT}"
    script(env, msg, {"name": DRAFT})

    env.text(msg, roles=SALES_USER)
    assert env.erp.updates() == []
    prompt = env.llm.prompts[-1]
    assert "at least one thing to change" in prompt
    assert "delivery date" in prompt and "item updates" in prompt


def test_a_bad_quantity_is_asked_about_not_sent(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 0}]})

    env.text(msg, roles=SALES_USER)
    assert env.erp.updates() == []
    assert "'qty' for ITEM-DESK-001 must be above zero" in env.llm.prompts[-1]


def test_fields_the_model_makes_up_are_not_sent(env):
    msg = f"move the delivery of {DRAFT} to 2026-12-01"
    script(env, msg, {"name": DRAFT, "delivery_date": "2026-12-01", "docstatus": 1, "grand_total": 1, "status": "Closed"})

    env.text(msg, roles=SALES_USER)
    (call,) = env.erp.updates()
    assert call[3] == {"delivery_date": "2026-12-01"}
    assert env.erp.order(DRAFT)["docstatus"] == 0


def test_erpnext_refusing_the_user_stops_a_write_the_role_list_allows(env):
    # The user holds a role the assistant accepts, but ERPNext (User Permissions, a customised
    # role, ownership) says no. Nothing is asked and nothing is written.
    env.erp.denied = {"update"}
    msg = f"move the delivery of {DRAFT} to 2026-12-01"
    script(env, msg, {"name": DRAFT, "delivery_date": "2026-12-01"})

    out = env.text(msg, roles=SALES_USER)
    assert "Permission Denied" in out
    assert f"not allowed to update Sales Order {DRAFT}" in out
    assert env.erp.updates() == []
    assert env.persisted()["pending_confirmation"] is None
    assert ("check_permission", "update", "Sales Order", DRAFT) in env.erp.calls


def test_access_revoked_between_the_question_and_the_yes_is_honoured(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})
    out = env.text(msg, roles=SALES_USER)
    assert "Reply **Yes**" in out

    env.erp.denied = {"update"}  # the administrator removes the permission while the question is open
    out = env.text("yes", roles=SALES_USER)
    assert "Permission Denied" in out
    assert env.erp.updates() == []
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 2
    assert env.persisted()["pending_confirmation"] is None


def test_the_question_is_asked_again_when_the_yes_arrives(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 12}]})
    env.text(msg, roles=SALES_USER)
    env.text("yes", roles=SALES_USER)
    asked = [c for c in env.erp.calls if c[0] == "check_permission"]
    assert len(asked) == 2, "once before the confirmation and once again before the write"
    assert {c[1:] for c in asked} == {("update", "Sales Order", DRAFT)}


def test_a_denied_submit_is_refused_even_for_a_manager_role(env):
    env.erp.denied = {"submit"}
    msg = f"submit {DRAFT}"
    env.llm.classify[msg] = {"intent": "submit-sales-order", "is_compound": False}
    env.llm.extract[msg] = {"name": DRAFT}

    out = env.text(msg, roles=["Sales Manager"])
    assert "not allowed to submit Sales Order" in out
    assert [c for c in env.erp.calls if c[0] == "submit_document"] == []
    assert env.erp.order(DRAFT)["docstatus"] == 0
