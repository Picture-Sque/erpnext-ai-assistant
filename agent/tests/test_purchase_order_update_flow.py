"""
The update-purchase-order flow with the widened skill, from node level to a full /chat run.

Mirrors test_sales_order_update_flow.py and test_sales_order_update_e2e.py: early validation,
the Draft-only rule, confirmation with a before/after preview, what is sent to ERPNext, the
stale-version checks and verification. The end-to-end part reuses the harness of
test_mixed_chains.py.
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

SNAPSHOT = {
    "name": "PO-1",
    "docstatus": 0,
    "modified": "2026-11-20 10:15:30.123456",
    "schedule_date": "2026-11-10",
    "items": [
        {"item_code": "ALUM", "qty": 100, "rate": 300},
        {"item_code": "STEEL", "qty": 40, "rate": 80},
    ],
}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "PO-1"}, "error": None, "has_more": False}


def skill():
    return graph._get_skill_for_intent("update-purchase-order")


def validate(collected):
    return graph.validate_parameters_node({"detected_intent": "update-purchase-order", "collected_fields": collected})


@pytest.mark.usefixtures("quiet_audit")
class TestFlow:
    def test_skill_loaded_with_the_widened_fields(self):
        s = skill()
        assert s["required_fields"] == ["name"]
        assert set(s["optional_fields"]) == {"schedule_date", "transaction_date", "item_updates"}
        assert set(s["require_any_of"]) == set(s["optional_fields"])
        assert s["allowed_roles"] == ["Purchase User", "Purchase Manager", "System Manager", "Administrator"]

    def test_update_payload_is_limited_to_declared_changes(self):
        payload = graph._update_payload(
            skill(),
            {"name": "PO-1", "schedule_date": "2026-12-01", "po_no": "X", "docstatus": 1,
             "item_updates": [{"item_code": "ALUM", "qty": 150, "amount": 9}]},
        )
        assert payload == {"schedule_date": "2026-12-01", "item_updates": [{"item_code": "ALUM", "qty": 150}]}

    # validation ------------------------------------------------------------------------
    def test_one_change_is_enough(self):
        for collected in (
            {"name": "PO-1", "schedule_date": "2026-12-01"},
            {"name": "PO-1", "transaction_date": "2026-11-01"},
            {"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": 150}]},
        ):
            assert validate(collected)["all_required_filled"] is True, collected

    def test_nothing_to_change_is_asked_about(self):
        out = validate({"name": "PO-1"})
        assert out["all_required_filled"] is False
        assert any("at least one thing to change" in m for m in out["missing_parameters"])

    def test_an_item_only_change_does_not_trip_the_date_rule(self):
        assert validate({"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": 1}]})["all_required_filled"] is True

    def test_the_existing_date_rule_still_applies(self):
        out = validate({"name": "PO-1", "schedule_date": "2026-11-01", "transaction_date": "2026-11-20"})
        assert out["all_required_filled"] is False
        assert any("strictly after transaction_date" in m for m in out["missing_parameters"])

    def test_bad_values_are_asked_about_not_sent(self):
        out = validate({"name": "PO-1", "schedule_date": "next Friday"})
        assert out["all_required_filled"] is False and "schedule_date" not in out["collected_fields"]
        out = validate({"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": -3}]})
        assert out["all_required_filled"] is False and "item_updates" not in out["collected_fields"]
        assert any("'qty' for ALUM must be above zero" in m for m in out["missing_parameters"])

    def test_item_changes_are_tidied(self):
        out = validate({"name": "PO-1", "item_updates": {"item_code": " ALUM ", "qty": "150"}})
        assert out["collected_fields"]["item_updates"] == [{"item_code": "ALUM", "qty": 150}]

    # preconditions ---------------------------------------------------------------------
    def _pre(self, collected):
        return {
            "detected_intent": "update-purchase-order", "write_rbac_operation": "update",
            "collected_fields": collected, "resolved_entities": {},
        }

    def test_item_changes_are_refused_on_a_submitted_order(self):
        with patch.object(graph, "get_document", return_value=ok({**SNAPSHOT, "docstatus": 1})):
            out = graph.precondition_validation_node(self._pre({"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": 1}]}))
        assert out["preconditions_validated"] is False
        assert "only allowed on a Draft" in out["final_response"]

    def test_item_changes_pass_on_a_draft_and_dates_pass_on_a_submitted_order(self):
        with patch.object(graph, "get_document", return_value=ok(SNAPSHOT)):
            assert graph.precondition_validation_node(self._pre({"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": 1}]}))["preconditions_validated"] is True
        with patch.object(graph, "get_document", return_value=ok({**SNAPSHOT, "docstatus": 1})):
            assert graph.precondition_validation_node(self._pre({"name": "PO-1", "schedule_date": "2026-12-01"}))["preconditions_validated"] is True

    # confirmation ----------------------------------------------------------------------
    def _confirm(self, collected, snapshot=SNAPSHOT):
        return {
            "detected_intent": "update-purchase-order", "preconditions_validated": True,
            "write_rbac_operation": "update", "collected_fields": collected, "resolved_entities": {},
            "messages": [], "user_sub": "p@example.com", "user_roles": ["Purchase User"],
            "last_read_snapshot": snapshot,
        }

    def test_item_changes_ask_and_show_before_and_after(self):
        out = graph.confirmation_node(self._confirm(
            {"name": "PO-1", "item_updates": [{"item_code": "ALUM", "qty": 150, "rate": 310}, {"item_code": "STEEL", "qty": 45}]}
        ))
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to update Purchase Order 'PO-1'?" in text
        assert "- ALUM: qty from 100 to 150, rate from 300 to 310" in text
        assert "- STEEL: qty from 40 to 45" in text
        assert out["pending_confirmation"]["target_modified"] == SNAPSHOT["modified"]

    def test_a_date_change_on_a_draft_is_not_confirmed(self):
        assert graph.confirmation_node(self._confirm({"name": "PO-1", "schedule_date": "2026-12-01"})) == {}

    def test_confirming_a_date_change_on_a_submitted_order_shows_the_change(self):
        out = graph.confirmation_node(self._confirm({"name": "PO-1", "schedule_date": "2026-12-01"}, {**SNAPSHOT, "docstatus": 1}))
        assert "- schedule_date: from 2026-11-10 to 2026-12-01" in out["pending_confirmation"]["scope_description"]

    # execution and verification --------------------------------------------------------
    def test_changes_reach_the_tool_with_the_version(self):
        state = {
            "detected_intent": "update-purchase-order",
            "collected_fields": {"name": "PO-1", "schedule_date": "2026-12-01", "item_updates": [{"item_code": "ALUM", "qty": 150}], "docstatus": 1},
            "last_read_snapshot": SNAPSHOT,
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with(
            "Purchase Order", "PO-1",
            {"schedule_date": "2026-12-01", "item_updates": [{"item_code": "ALUM", "qty": 150}]},
            expected_modified=SNAPSHOT["modified"],
        )

    def _verify(self, collected, doc):
        state = {
            "detected_intent": "update-purchase-order", "write_rbac_operation": "update", "target_doctype": "Purchase Order",
            "tool_raw_response": {"success": True, "data": {"name": "PO-1"}}, "collected_fields": collected,
        }
        with patch.object(graph, "get_document", return_value=ok(doc)):
            return graph.result_validation_node(state)

    def test_changes_that_landed_are_verified_and_ones_that_did_not_are_caught(self):
        doc = {
            "name": "PO-1", "schedule_date": "2026-12-01",
            "items": [{"item_code": "ALUM", "qty": 150, "rate": 300, "schedule_date": "2026-12-01"}],
        }
        assert self._verify({"name": "PO-1", "schedule_date": "2026-12-01", "item_updates": [{"item_code": "ALUM", "qty": 150}]}, doc)["write_verified"] is True
        out = self._verify({"name": "PO-1", "item_updates": [{"item_code": "ALUM", "rate": 310}]}, doc)
        assert out["write_verified"] is False and "rate of 310 for ALUM" in out["final_response"]

    def test_a_schedule_date_must_be_on_every_row_not_only_the_header(self):
        # ERPNext derives the header date from the rows; a header that looks right with stale
        # rows is not a completed update.
        doc = {
            "name": "PO-1", "schedule_date": "2026-12-01",
            "items": [
                {"item_code": "ALUM", "qty": 1, "rate": 1, "schedule_date": "2026-12-01"},
                {"item_code": "STEEL", "qty": 1, "rate": 1, "schedule_date": "2026-11-12"},
            ],
        }
        out = self._verify({"name": "PO-1", "schedule_date": "2026-12-01"}, doc)
        assert out["write_verified"] is False
        assert out["failure_classification"] == "conflict_stale_data"
        assert "item row 'STEEL' shows '2026-11-12'" in out["final_response"]

    def test_a_header_that_did_not_change_is_caught(self):
        doc = {"name": "PO-1", "schedule_date": "2026-11-10", "items": [{"item_code": "ALUM", "schedule_date": "2026-11-10"}]}
        out = self._verify({"name": "PO-1", "schedule_date": "2026-12-01"}, doc)
        assert out["write_verified"] is False
        assert "still shows '2026-11-10'" in out["final_response"]


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

BUYER = ["Purchase User"]
DRAFT, SUBMITTED = "PUR-ORD-2026-00021", "PUR-ORD-2026-00022"


class PurchaseERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.version = 0
        self.db["Purchase Order"] = [self._order(DRAFT, 0), self._order(SUBMITTED, 1)]

    def _order(self, name, docstatus):
        return {
            "name": name, "supplier": "Zuckerman Security Ltd.", "docstatus": docstatus,
            "status": "Draft" if docstatus == 0 else "To Receive and Bill",
            "modified": "2026-11-20 10:00:00.000001", "schedule_date": "2026-11-10",
            "items": [
                {"name": f"{name}-1", "item_code": "ITEM-DESK-001", "qty": 50, "rate": 300},
                {"name": f"{name}-2", "item_code": "MS-001", "qty": 20, "rate": 80},
            ],
        }

    def order(self, name):
        return next(r for r in self.db["Purchase Order"] if r["name"] == name)

    def touch(self, name):
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
        if "schedule_date" in fields:
            # ERPNext derives the header date from the rows, and crud.update_document sets it on each.
            for row in doc["items"]:
                row["schedule_date"] = fields["schedule_date"]
        self.version += 1
        doc["modified"] = f"2026-11-20 12:00:00.{self.version:06d}"
        return _res("success", copy.deepcopy(doc))

    def updates(self):
        return [c for c in self.calls if c[0] == "update_document"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = PurchaseERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "update_document",
                 "submit_document", "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)
    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


def script(env, message, fields):
    env.llm.classify[message] = {"intent": "update-purchase-order", "is_compound": False}
    env.llm.extract[message] = fields


QTY_MSG = "change the quantity of ITEM-DESK-001 to 60 on {}"


def test_e2e_quantity_change_is_previewed_confirmed_and_verified(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 60}]})

    out = env.text(msg, roles=BUYER)
    assert f"Are you sure you want to update Purchase Order '{DRAFT}'?" in out
    assert "- ITEM-DESK-001: qty from 50 to 60" in out
    assert env.erp.updates() == []

    assert env.text("yes", roles=BUYER) == "FORMATTED[update-purchase-order]"
    (call,) = env.erp.updates()
    assert call[3] == {"item_updates": [{"item_code": "ITEM-DESK-001", "qty": 60}]}
    assert call[4] == "2026-11-20 10:00:00.000001"
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 60
    assert env.erp.order(DRAFT)["items"][1]["qty"] == 20


def test_e2e_schedule_date_change_still_runs_without_a_question(env):
    msg = f"reschedule {DRAFT} to 2026-12-01"
    script(env, msg, {"name": DRAFT, "schedule_date": "2026-12-01"})
    assert env.text(msg, roles=BUYER) == "FORMATTED[update-purchase-order]"
    assert env.erp.order(DRAFT)["schedule_date"] == "2026-12-01"


def test_e2e_item_changes_on_a_submitted_order_never_reach_erpnext(env):
    msg = QTY_MSG.format(SUBMITTED)
    script(env, msg, {"name": SUBMITTED, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 60}]})
    out = env.text(msg, roles=BUYER)
    assert "only allowed on a Draft" in out
    assert env.erp.updates() == []


def test_e2e_a_sales_role_cannot_edit_a_purchase_order(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 60}]})
    out = env.text(msg, roles=["Sales User", "Sales Manager"])
    assert "Denied" in out
    assert env.erp.updates() == []


def test_e2e_an_edit_between_the_question_and_the_yes_is_refused(env):
    msg = QTY_MSG.format(DRAFT)
    script(env, msg, {"name": DRAFT, "item_updates": [{"item_code": "ITEM-DESK-001", "qty": 60}]})
    env.text(msg, roles=BUYER)
    env.erp.touch(DRAFT)
    out = env.text("yes", roles=BUYER)
    assert "was changed after you were asked to confirm" in out
    assert env.erp.updates() == []
    assert env.erp.order(DRAFT)["items"][0]["qty"] == 50
