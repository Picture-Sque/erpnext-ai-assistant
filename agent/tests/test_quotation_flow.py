"""
Quotation through the conversation pipeline, from node level to a full /chat run:
what a create sends to ERPNext, the duplicate check, validation and confirmation of an update,
the submit offer after a create, and a confirmed submit and cancel. The end-to-end part reuses
the harness of test_mixed_chains.py.
"""

import copy
import os
import sys
from datetime import datetime
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
    "name": "SAL-QTN-1", "docstatus": 0, "modified": "2026-11-20 10:15:30.123456", "valid_till": "2026-12-31",
    "items": [{"item_code": "DESK", "qty": 2, "rate": 100}, {"item_code": "CHAIR", "qty": 4, "rate": 50}],
}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "SAL-QTN-1"}, "error": None, "has_more": False}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def test_a_create_sends_the_fixed_type_the_mapped_customer_and_clean_rows(self):
        state = {
            "detected_intent": "create-quotation",
            "collected_fields": {
                "customer": "Acme Corp", "transaction_date": "2026-11-21", "valid_till": "2026-12-31",
                "items": [{"item_code": "DESK", "qty": 3, "rate": 7500, "warehouse": "Stores", "delivery_date": "2026-12-01", "item_name": "Desk"}],
                "grand_total": 1, "docstatus": 1,
            },
        }
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        doctype, payload = create.call_args.args
        assert doctype == "Quotation"
        assert payload == {
            "quotation_to": "Customer", "party_name": "Acme Corp", "transaction_date": "2026-11-21", "valid_till": "2026-12-31",
            "items": [{"item_code": "DESK", "qty": 3, "rate": 7500}],
        }

    def test_a_valid_till_that_is_not_a_date_is_asked_about(self):
        out = graph.validate_parameters_node({
            "detected_intent": "create-quotation",
            "collected_fields": {"customer": "Acme", "items": [{"item_code": "A", "qty": 1}], "valid_till": "end of year"},
        })
        assert out["all_required_filled"] is False
        assert "valid_till" not in out["collected_fields"]
        assert any("valid till as a date written YYYY-MM-DD" in m for m in out["missing_parameters"])

    def test_a_create_needs_a_customer_and_items(self):
        out = graph.validate_parameters_node({"detected_intent": "create-quotation", "collected_fields": {"valid_till": "2026-12-31"}})
        assert out["all_required_filled"] is False
        assert {"customer", "items"} <= set(out["missing_parameters"])

    def test_the_duplicate_check_looks_for_the_customer_in_party_name(self):
        recent = {"name": "SAL-QTN-9", "creation": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"), "party_name": "Acme Corp"}
        state = {
            "detected_intent": "create-quotation", "write_rbac_operation": "create", "resolved_entities": {},
            "collected_fields": {"customer": "Acme Corp", "items": [{"item_code": "A", "qty": 1}]},
        }
        with patch.object(graph, "get_list", return_value={"status": "success", "data": [recent]}) as gl:
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False
        assert "same customer" in out["final_response"] and "SAL-QTN-9" in out["final_response"]
        assert gl.call_args.args[0] == "Quotation"
        assert gl.call_args.kwargs["filters"] == [["party_name", "=", "Acme Corp"]]
        assert "party_name" in gl.call_args.kwargs["fields"]

    def test_update_needs_a_change_and_rejects_bad_numbers(self):
        v = lambda c: graph.validate_parameters_node({"detected_intent": "update-quotation", "collected_fields": c})
        assert v({"name": "Q"})["all_required_filled"] is False
        assert v({"name": "Q", "valid_till": "2027-01-01"})["all_required_filled"] is True
        assert v({"name": "Q", "item_updates": [{"item_code": "A", "qty": "5"}]})["collected_fields"]["item_updates"] == [{"item_code": "A", "qty": 5}]
        assert v({"name": "Q", "item_updates": [{"item_code": "A", "qty": 0}]})["all_required_filled"] is False

    def test_item_changes_are_refused_on_a_submitted_quotation_and_validity_is_not(self):
        pre = lambda c, doc: graph.precondition_validation_node({
            "detected_intent": "update-quotation", "write_rbac_operation": "update", "collected_fields": c, "resolved_entities": {}})
        with patch.object(graph, "get_document", return_value=ok({**SNAPSHOT, "docstatus": 1})):
            refused = pre({"name": "SAL-QTN-1", "item_updates": [{"item_code": "DESK", "qty": 3}]}, None)
            allowed = pre({"name": "SAL-QTN-1", "valid_till": "2027-01-01"}, None)
        assert refused["preconditions_validated"] is False and "only allowed on a Draft" in refused["final_response"]
        assert allowed["preconditions_validated"] is True

    def test_item_changes_ask_with_before_and_after_and_validity_does_not(self):
        base = {
            "detected_intent": "update-quotation", "preconditions_validated": True, "write_rbac_operation": "update",
            "resolved_entities": {}, "messages": [], "user_sub": "u", "user_roles": ["Sales User"], "last_read_snapshot": SNAPSHOT,
        }
        asked = graph.confirmation_node({**base, "collected_fields": {"name": "SAL-QTN-1", "item_updates": [{"item_code": "DESK", "qty": 5, "rate": 90}]}})
        text = asked["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to update Quotation 'SAL-QTN-1'?" in text
        assert "- DESK: qty from 2 to 5, rate from 100 to 90" in text
        assert graph.confirmation_node({**base, "collected_fields": {"name": "SAL-QTN-1", "valid_till": "2027-01-01"}}) == {}

    def test_what_is_sent_for_an_update_is_limited_to_the_declared_changes(self):
        state = {
            "detected_intent": "update-quotation",
            "collected_fields": {"name": "SAL-QTN-1", "valid_till": "2027-01-01", "item_updates": [{"item_code": "DESK", "qty": 5, "amount": 9}],
                                 "party_name": "Someone Else", "docstatus": 1},
            "last_read_snapshot": SNAPSHOT,
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with(
            "Quotation", "SAL-QTN-1", {"valid_till": "2027-01-01", "item_updates": [{"item_code": "DESK", "qty": 5}]},
            expected_modified=SNAPSHOT["modified"],
        )

    def test_verification_checks_validity_and_rows(self):
        doc = {"name": "SAL-QTN-1", "valid_till": "2027-01-01", "items": [{"item_code": "DESK", "qty": 5, "rate": 100}]}
        state = {
            "detected_intent": "update-quotation", "write_rbac_operation": "update", "target_doctype": "Quotation",
            "tool_raw_response": {"success": True, "data": {"name": "SAL-QTN-1"}},
            "collected_fields": {"name": "SAL-QTN-1", "valid_till": "2027-01-01", "item_updates": [{"item_code": "DESK", "qty": 5}]},
        }
        with patch.object(graph, "get_document", return_value=ok(doc)):
            assert graph.result_validation_node(state)["write_verified"] is True
        state["collected_fields"]["item_updates"] = [{"item_code": "DESK", "qty": 6}]
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.result_validation_node(state)
        assert out["write_verified"] is False and "qty of 6 for DESK" in out["final_response"]

    def test_the_submit_offer_exists_for_a_manager_and_not_for_a_sales_user(self):
        manager = graph._submit_offer({"user_roles": ["Sales Manager"], "user_sub": "m", "messages": []}, "Quotation", "SAL-QTN-1")
        assert manager is not None
        assert manager[0]["skill"] == "submit-quotation" and manager[0]["operation"] == "submit"
        assert "Quotation 'SAL-QTN-1'" in manager[1]
        assert graph._submit_offer({"user_roles": ["Sales User"], "user_sub": "s", "messages": []}, "Quotation", "SAL-QTN-1") is None


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

MANAGER, SALES_USER = ["Sales Manager"], ["Sales User"]


class QuotationERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Customer"] = [{"name": "Acme Corp", "customer_name": "Acme Corp", "customer_group": "Commercial", "territory": "India"}]
        self.db["Quotation"] = []

    def create_document(self, doctype, fields):
        self.calls.append(("create_document", doctype, copy.deepcopy(fields)))
        name = f"SAL-QTN-2026-{len(self.db[doctype]) + 1:05d}"
        doc = dict(copy.deepcopy(fields), name=name, docstatus=0, status="Draft", modified="2026-11-20 10:00:00.000001",
                   creation=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"))
        self.db[doctype].append(doc)
        return _res("success", copy.deepcopy(doc))

    def _guarded(self, op, doctype, name, expected_modified):
        """submit and cancel as the real tool layer takes them: with the version the caller last read."""
        doc = self.quotation(name)
        if expected_modified and expected_modified != doc.get("modified"):
            return _res("system_error", error=f"Conflict: {doctype} {name} was changed by someone else after it was last read, so nothing was changed.")
        return self._set_docstatus(op, doctype, name, 1 if op == "submit_document" else 2)

    def submit_document(self, doctype, name, expected_modified=None):
        return self._guarded("submit_document", doctype, name, expected_modified)

    def cancel_document(self, doctype, name, expected_modified=None):
        return self._guarded("cancel_document", doctype, name, expected_modified)

    def quotation(self, name):
        return next(r for r in self.db["Quotation"] if r["name"] == name)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = QuotationERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "submit_document",
                 "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)
    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


CREATE_MSG = "create a quotation for Acme Corp for 3 ITEM-DESK-001 at 7500"


def script_create(env):
    env.llm.classify[CREATE_MSG] = {"intent": "create-quotation", "is_compound": False}
    env.llm.extract[CREATE_MSG] = {"customer": "Acme Corp", "items": [{"item_code": "ITEM-DESK-001", "qty": 3, "rate": 7500}]}


def test_e2e_create_makes_a_draft_and_a_manager_is_offered_the_submit(env):
    script_create(env)
    out = env.text(CREATE_MSG, roles=MANAGER)
    assert "FORMATTED[create-quotation]" in out
    assert "**Submit it now?**" in out and "Quotation 'SAL-QTN-2026-00001'" in out

    (call,) = [c for c in env.erp.calls if c[0] == "create_document"]
    assert call[1] == "Quotation"
    assert call[2]["quotation_to"] == "Customer" and call[2]["party_name"] == "Acme Corp"
    assert call[2]["items"] == [{"item_code": "ITEM-DESK-001", "qty": 3, "rate": 7500}]
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 0, "created as a Draft, not submitted"
    pending = env.persisted()["pending_confirmation"]
    assert pending["operation"] == "submit" and pending["skill"] == "submit-quotation" and pending["is_offer"] is True


def test_e2e_saying_yes_to_the_offer_submits_it(env):
    script_create(env)
    env.text(CREATE_MSG, roles=MANAGER)
    out = env.text("yes", roles=MANAGER)
    assert "FORMATTED[submit-quotation]" in out
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 1
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_a_sales_user_creates_the_draft_but_is_not_offered_the_submit(env):
    script_create(env)
    out = env.text(CREATE_MSG, roles=SALES_USER)
    assert "FORMATTED[create-quotation]" in out
    assert "Submit it now?" not in out
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 0
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_a_sales_user_cannot_submit_one(env):
    script_create(env)
    env.text(CREATE_MSG, roles=SALES_USER)
    msg = "submit quotation SAL-QTN-2026-00001"
    env.llm.classify[msg] = {"intent": "submit-quotation", "is_compound": False}
    env.llm.extract[msg] = {"name": "SAL-QTN-2026-00001"}
    out = env.text(msg, roles=SALES_USER)
    assert "Denied" in out
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 0
    assert [c for c in env.erp.calls if c[0] == "submit_document"] == []


def test_e2e_a_second_identical_create_within_a_minute_is_questioned(env):
    script_create(env)
    env.text(CREATE_MSG, roles=SALES_USER)
    out = env.text(CREATE_MSG, roles=SALES_USER, conversation_id="conv-2")
    assert "A similar Quotation was just created" in out and "same customer" in out
    assert len(env.erp.db["Quotation"]) == 1


def test_e2e_a_manager_can_cancel_a_submitted_quotation_after_confirming(env):
    script_create(env)
    env.text(CREATE_MSG, roles=MANAGER)
    env.text("yes", roles=MANAGER)  # submits the offered draft
    msg = "cancel quotation SAL-QTN-2026-00001"
    env.llm.classify[msg] = {"intent": "cancel-quotation", "is_compound": False}
    env.llm.extract[msg] = {"name": "SAL-QTN-2026-00001"}
    out = env.text(msg, roles=MANAGER)
    assert "Are you sure you want to cancel Quotation 'SAL-QTN-2026-00001'?" in out
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 1
    out = env.text("yes", roles=MANAGER)
    assert "FORMATTED[cancel-quotation]" in out
    assert env.erp.quotation("SAL-QTN-2026-00001")["docstatus"] == 2
