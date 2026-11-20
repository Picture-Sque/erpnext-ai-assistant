"""
Supplier through the conversation pipeline, from node level to a full /chat run.

Mirrors the Customer flow (see test_customer_flow.py) with the Supplier specifics: purchasing roles,
the create slot and identity slot are `supplier`, and a supplier with the same name must not be created
twice. The end-to-end part reuses the harness of test_mixed_chains.py.
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

SUMMIT = {"name": "Summit Traders Ltd.", "supplier_name": "Summit Traders Ltd.", "supplier_type": "Company",
          "supplier_group": "Demo Supplier Group", "country": "India", "website": None, "modified": "2026-11-20 10:00:00.000001"}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "Summit Traders Ltd."}, "error": None, "has_more": False}


def found(*names):
    return {"status": "success", "data": [{"name": n, "supplier_name": n} for n in names]}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def validate(self, intent, collected):
        return graph.validate_parameters_node({"detected_intent": intent, "collected_fields": collected})

    def test_create_needs_only_a_supplier_name_and_defaults_the_type(self):
        assert self.validate("create-supplier", {"supplier": "Harbour Metals"})["all_required_filled"] is True
        assert self.validate("create-supplier", {})["all_required_filled"] is False
        skill = graph._get_skill_for_intent("create-supplier")
        assert graph.apply_skill_defaults({"supplier": "Harbour"}, skill["defaults"])["supplier_type"] == "Company"

    def test_the_type_is_tidied_or_asked_again(self):
        out = self.validate("create-supplier", {"supplier": "S", "supplier_type": "individual"})
        assert out["all_required_filled"] is True and out["collected_fields"]["supplier_type"] == "Individual"
        out = self.validate("create-supplier", {"supplier": "S", "supplier_type": "Charity"})
        assert out["all_required_filled"] is False and "supplier_type" not in out["collected_fields"]

    def test_a_create_maps_the_asked_for_supplier_into_supplier_name_and_sends_only_declared_fields(self):
        state = {"detected_intent": "create-supplier", "collected_fields": {
            "supplier": "Harbour Metals", "supplier_type": "Company", "country": "India",
            "on_hold": 1, "tax_id": "1", "payment_terms": "x", "disabled": 1}}
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        assert create.call_args.args == ("Supplier", {"supplier_name": "Harbour Metals", "supplier_type": "Company", "country": "India"})

    def test_entity_resolution_never_replaces_the_name_of_a_supplier_being_created(self):
        state = {"detected_intent": "create-supplier", "user_roles": ["Purchase User"], "collected_fields": {"supplier": "Summit"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("Summit Traders Ltd.")) as search:
            out = graph.entity_resolution_and_read_rbac_node(state)
        search.assert_not_called()
        assert out["ambiguous_candidates"] is None and state["collected_fields"]["supplier"] == "Summit"

    def test_the_supplier_named_by_a_purchase_order_and_an_update_is_still_resolved(self):
        for intent, extra in (("update-supplier", {"country": "India"}), ("create-purchase-order", {"items": [{"item_code": "RM-001", "qty": 1}]})):
            state = {"detected_intent": intent, "user_roles": ["Purchase User"], "collected_fields": {"supplier": "summit traders ltd.", **extra}, "resolved_entities": {}}
            with patch.object(graph, "search_document", return_value=found("Summit Traders Ltd.")), \
                    patch.object(graph, "get_list", return_value={"status": "success", "data": [{"name": "RM-001", "item_code": "RM-001"}]}):
                out = graph.entity_resolution_and_read_rbac_node(state)
            assert out["resolved_entities"]["supplier"]["name"] == "Summit Traders Ltd.", intent

    def test_an_exact_existing_name_is_refused_whatever_the_case(self):
        state = {"detected_intent": "create-supplier", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"supplier": "SUMMIT traders ltd."}}
        with patch.object(graph, "search_document", return_value=found("Summit Traders Ltd.", "Summit Metals")):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and out["failure_classification"] == "validation_error"
        assert "Supplier 'Summit Traders Ltd.' already exists" in out["final_response"]

    def test_a_similar_name_is_allowed_and_a_failed_lookup_creates_nothing(self):
        state = {"detected_intent": "create-supplier", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"supplier": "Summit"}}
        with patch.object(graph, "search_document", return_value=found("Summit Traders Ltd.")), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": []}):
            assert graph.precondition_validation_node(state)["preconditions_validated"] is True
        with patch.object(graph, "search_document", return_value={"status": "system_error", "data": None, "error": "boom"}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and "Nothing was created" in out["final_response"]

    def test_the_duplicate_check_for_recent_creates_reads_the_supplier_slot(self):
        recent = {"name": "SUP-1", "creation": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"), "supplier_name": "Harbour Metals"}
        state = {"detected_intent": "create-supplier", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"supplier": "Harbour Metals"}}
        with patch.object(graph, "search_document", return_value={"status": "empty", "data": []}), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": [recent]}) as gl:
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and "same supplier" in out["final_response"]
        assert gl.call_args.kwargs["filters"] == [["supplier_name", "=", "Harbour Metals"]]

    def test_a_create_always_asks_with_details_and_similar_names(self):
        state = {"detected_intent": "create-supplier", "preconditions_validated": True, "write_rbac_operation": "create", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Purchase User"],
                 "collected_fields": {"supplier": "Summit", "supplier_type": "Company", "country": "India"}}
        with patch.object(graph, "search_document", return_value=found("Summit Traders Ltd.", "Summit Metals")):
            out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to create a Supplier for supplier 'Summit'?" in text
        assert "- supplier type: Company" in text and "- country: India" in text
        assert "Note: Supplier records with a similar name already exist: Summit Traders Ltd., Summit Metals." in text

    def test_update_needs_a_supplier_and_a_change_and_targets_the_named_supplier(self):
        assert self.validate("update-supplier", {"supplier": "Summit"})["all_required_filled"] is False
        assert self.validate("update-supplier", {"supplier": "Summit", "country": "India"})["all_required_filled"] is True
        skill = graph._get_skill_for_intent("update-supplier")
        assert graph._target_record_id({"supplier": "Summit Traders Ltd.", "country": "India"}, {}, skill) == "Summit Traders Ltd."
        assert graph._update_payload(skill, {"supplier": "Summit Traders Ltd.", "country": "India", "on_hold": 1, "tax_id": "1"}) == {"country": "India"}

    def test_the_update_goes_to_the_named_supplier_with_the_version(self):
        state = {"detected_intent": "update-supplier", "collected_fields": {"supplier": "Summit Traders Ltd.", "supplier_group": "Hardware"},
                 "last_read_snapshot": {"name": "Summit Traders Ltd.", "modified": SUMMIT["modified"]}}
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with("Supplier", "Summit Traders Ltd.", {"supplier_group": "Hardware"}, expected_modified=SUMMIT["modified"])

    def test_every_update_asks_and_shows_old_and_new_values(self):
        state = {"detected_intent": "update-supplier", "preconditions_validated": True, "write_rbac_operation": "update", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Purchase User"], "last_read_snapshot": SUMMIT,
                 "collected_fields": {"supplier": "Summit Traders Ltd.", "supplier_group": "Hardware", "country": "Germany"}}
        out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to update Supplier 'Summit Traders Ltd.'?" in text
        assert "- supplier_group: from Demo Supplier Group to Hardware" in text and "- country: from India to Germany" in text
        assert out["pending_confirmation"]["target_name"] == "Summit Traders Ltd."

    def test_the_write_gate_allows_purchasing_roles_and_refuses_sales_roles(self):
        gate = lambda intent, roles: graph.write_rbac_gate_node({"detected_intent": intent, "user_roles": roles, "collected_fields": {}})["write_rbac_passed"]
        for intent in ("create-supplier", "update-supplier"):
            assert gate(intent, ["Purchase User"]) and gate(intent, ["Purchase Manager"])
            assert not gate(intent, ["Sales User"]) and not gate(intent, ["Sales Manager"]) and not gate(intent, ["Accounts User"])


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

BUYER = ["Purchase User"]


class SupplierERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Supplier"] = [
            {"name": "Summit Metals", "supplier_name": "Summit Metals", "supplier_type": "Company", "supplier_group": "Hardware", "country": "India", "website": None, "modified": "2026-11-20 10:00:00.000001"},
            dict(SUMMIT),
        ]
        self.version = 0

    def create_document(self, doctype, fields):
        self.calls.append(("create_document", doctype, copy.deepcopy(fields)))
        doc = dict(copy.deepcopy(fields), name=fields["supplier_name"], modified="2026-11-20 10:00:00.000001")
        self.db[doctype].append(doc)
        return _res("success", copy.deepcopy(doc))

    def update_document(self, doctype, name, fields, expected_modified=None):
        self.calls.append(("update_document", doctype, name, copy.deepcopy(fields), expected_modified))
        doc = self.supplier(name)
        if expected_modified and expected_modified != doc["modified"]:
            return _res("system_error", error=f"Conflict: {doctype} {name} was changed by someone else after it was last read, so nothing was changed.")
        doc.update(fields)
        self.version += 1
        doc["modified"] = f"2026-11-20 12:00:00.{self.version:06d}"
        return _res("success", copy.deepcopy(doc))

    def supplier(self, name):
        return next(r for r in self.db["Supplier"] if r["name"] == name)

    def writes_of(self, op):
        return [c for c in self.calls if c[0] == op]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = SupplierERP()
    for name in ("get_list", "search_document", "get_document", "create_document", "update_document", "submit_document",
                 "cancel_document", "get_count", "aggregate"):
        monkeypatch.setattr(graph, name, getattr(erp, name))
    llm = ScriptedLLM()
    monkeypatch.setattr(graph, "invoke_structured_llm", llm.structured)
    monkeypatch.setattr(graph, "invoke_llm", llm.text)
    monkeypatch.setattr(followup_router, "invoke_structured_llm", llm.structured)
    with TestClient(main.app) as client:
        yield Driver(client, erp, llm)


def script(env, message, intent, fields):
    env.llm.classify[message] = {"intent": intent, "is_compound": False}
    env.llm.extract[message] = fields


def test_e2e_a_new_supplier_is_previewed_confirmed_and_created(env):
    msg = "add a supplier called Harbour Metals in the Hardware group, country India"
    script(env, msg, "create-supplier", {"supplier": "Harbour Metals", "supplier_group": "Hardware", "country": "India"})
    out = env.text(msg, roles=BUYER)
    assert "Are you sure you want to create a Supplier for supplier 'Harbour Metals'?" in out
    assert "- supplier type: Company" in out and "- supplier group: Hardware" in out and "- country: India" in out
    assert "similar name" not in out
    assert env.erp.writes_of("create_document") == []

    assert env.text("yes", roles=BUYER) == "FORMATTED[create-supplier]"
    (call,) = env.erp.writes_of("create_document")
    assert call[1:] == ("Supplier", {"supplier_name": "Harbour Metals", "supplier_type": "Company", "supplier_group": "Hardware", "country": "India"})
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_similar_names_are_shown_and_a_no_creates_nothing(env):
    msg = "add a supplier Summit"
    script(env, msg, "create-supplier", {"supplier": "Summit"})
    out = env.text(msg, roles=BUYER)
    assert "similar name already exist: Summit Metals, Summit Traders Ltd." in out
    assert "cancelled" in env.text("no", roles=BUYER).lower()
    assert env.erp.writes_of("create_document") == [] and len(env.erp.db["Supplier"]) == 2


def test_e2e_an_exact_existing_name_is_refused_without_asking(env):
    msg = "create a supplier summit traders ltd."
    script(env, msg, "create-supplier", {"supplier": "summit traders ltd."})
    out = env.text(msg, roles=BUYER)
    assert "Supplier 'Summit Traders Ltd.' already exists" in out
    assert env.erp.writes_of("create_document") == [] and env.persisted()["pending_confirmation"] is None


def test_e2e_the_typed_name_is_never_swapped_for_an_existing_supplier(env):
    msg = "create a supplier Summit Tr"
    script(env, msg, "create-supplier", {"supplier": "Summit Tr"})
    assert "for supplier 'Summit Tr'?" in env.text(msg, roles=BUYER)
    env.text("yes", roles=BUYER)
    assert env.erp.writes_of("create_document")[0][2]["supplier_name"] == "Summit Tr"


def test_e2e_an_update_shows_old_and_new_values_and_changes_only_what_was_asked(env):
    msg = "change the supplier group of Summit Traders Ltd. to Hardware"
    script(env, msg, "update-supplier", {"supplier": "Summit Traders Ltd.", "supplier_group": "Hardware"})
    out = env.text(msg, roles=BUYER)
    assert "Are you sure you want to update Supplier 'Summit Traders Ltd.'?" in out
    assert "- supplier_group: from Demo Supplier Group to Hardware" in out
    assert env.erp.writes_of("update_document") == []

    assert env.text("yes", roles=BUYER) == "FORMATTED[update-supplier]"
    (call,) = env.erp.writes_of("update_document")
    assert call[1:4] == ("Supplier", "Summit Traders Ltd.", {"supplier_group": "Hardware"})
    assert call[4] == "2026-11-20 10:00:00.000001"
    assert env.erp.supplier("Summit Traders Ltd.")["supplier_group"] == "Hardware"
    assert env.erp.supplier("Summit Traders Ltd.")["country"] == "India"


def test_e2e_an_ambiguous_supplier_asks_which_one_and_writes_nothing_meanwhile(env):
    msg = "change the supplier group of Summit to Hardware"
    script(env, msg, "update-supplier", {"supplier": "Summit", "supplier_group": "Hardware"})
    out = env.text(msg, roles=BUYER)
    assert "Summit Metals" in out and "Summit Traders Ltd." in out
    assert env.erp.writes_of("update_document") == []


def test_e2e_payment_holds_and_tax_details_in_the_request_are_ignored_and_nothing_is_changed(env):
    msg = "put Summit Traders Ltd. on payment hold"
    script(env, msg, "update-supplier", {"supplier": "Summit Traders Ltd.", "on_hold": 1, "hold_type": "Payments", "tax_id": "9"})
    env.text(msg, roles=BUYER)
    assert env.erp.writes_of("update_document") == []
    state = env.persisted()
    assert state["pending_confirmation"] is None
    assert not {"on_hold", "hold_type", "tax_id"} & set(state["collected_fields"] or {})
    assert env.erp.supplier("Summit Traders Ltd.")["modified"] == "2026-11-20 10:00:00.000001"


def test_e2e_sales_and_accounts_roles_cannot_create_or_update_a_supplier(env):
    msg = "add a supplier Harbour Metals"
    script(env, msg, "create-supplier", {"supplier": "Harbour Metals"})
    for i, roles in enumerate((["Sales User", "Sales Manager"], ["Accounts User"])):
        assert "Denied" in env.text(msg, roles=roles, conversation_id=f"c{i}")
    msg2 = "change the supplier group of Summit Traders Ltd. to Hardware"
    script(env, msg2, "update-supplier", {"supplier": "Summit Traders Ltd.", "supplier_group": "Hardware"})
    assert "Denied" in env.text(msg2, roles=["Sales User"], conversation_id="c9")
    assert env.erp.writes_of("create_document") == [] and env.erp.writes_of("update_document") == []


def test_e2e_an_edit_between_the_question_and_the_yes_is_refused(env):
    msg = "change the supplier group of Summit Traders Ltd. to Hardware"
    script(env, msg, "update-supplier", {"supplier": "Summit Traders Ltd.", "supplier_group": "Hardware"})
    env.text(msg, roles=BUYER)
    env.erp.supplier("Summit Traders Ltd.")["modified"] = "2026-11-20 11:00:00.000009"
    out = env.text("yes", roles=BUYER)
    assert "was changed after you were asked to confirm" in out
    assert env.erp.writes_of("update_document") == []
    assert env.erp.supplier("Summit Traders Ltd.")["supplier_group"] == "Demo Supplier Group"
