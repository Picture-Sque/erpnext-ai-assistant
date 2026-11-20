"""
Customer through the conversation pipeline, from node level to a full /chat run.

A new customer always asks first (with its details and any similar existing names), an exact
existing name is refused outright, and an update names the customer, resolves it with the user,
shows old and new values, and never touches the fields that are off limits. The end-to-end part
reuses the harness of test_mixed_chains.py.
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

ACME = {"name": "Acme Industries", "customer_name": "Acme Industries", "customer_type": "Company",
        "customer_group": "Commercial", "territory": "India", "website": None, "modified": "2026-11-20 10:00:00.000001"}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "Acme Industries"}, "error": None, "has_more": False}


def found(*names):
    return {"status": "success", "data": [{"name": n, "customer_name": n} for n in names]}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def validate(self, intent, collected):
        return graph.validate_parameters_node({"detected_intent": intent, "collected_fields": collected})

    # ---- create ----------------------------------------------------------------------------
    def test_create_needs_only_a_name_and_defaults_the_type(self):
        assert self.validate("create-customer", {"customer_name": "Northwind"})["all_required_filled"] is True
        assert self.validate("create-customer", {})["all_required_filled"] is False
        collected = graph.apply_skill_defaults({"customer_name": "Northwind"}, graph._get_skill_for_intent("create-customer")["defaults"])
        assert collected["customer_type"] == "Company"

    def test_the_customer_type_is_tidied_or_asked_again(self):
        out = self.validate("create-customer", {"customer_name": "N", "customer_type": "individual"})
        assert out["all_required_filled"] is True and out["collected_fields"]["customer_type"] == "Individual"
        out = self.validate("create-customer", {"customer_name": "N", "customer_type": "Charity"})
        assert out["all_required_filled"] is False and "customer_type" not in out["collected_fields"]

    def test_a_create_sends_only_declared_fields(self):
        state = {"detected_intent": "create-customer", "collected_fields": {
            "customer_name": "Northwind", "customer_type": "Company", "customer_group": "Commercial",
            "credit_limits": [{"credit_limit": 1}], "tax_id": "1", "disabled": 1}}
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        assert create.call_args.args == ("Customer", {"customer_name": "Northwind", "customer_type": "Company", "customer_group": "Commercial"})

    def test_slot_extraction_ignores_fields_the_skill_never_declared(self):
        llm = {"customer_name": "Northwind", "credit_limits": 999999, "tax_id": "1", "disabled": 1}
        with patch.object(graph, "invoke_structured_llm", return_value=llm):
            out = graph.collect_parameters_node({"detected_intent": "create-customer", "messages": [MagicMock(content="add Northwind")], "collected_fields": {}})
        assert set(out["collected_fields"]) == {"customer_name", "customer_type"} or set(out["collected_fields"]) == {"customer_name"}

    def test_entity_resolution_never_replaces_the_name_of_a_customer_being_created(self):
        state = {"detected_intent": "create-customer", "user_roles": ["Sales User"], "collected_fields": {"customer_name": "Acme"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("Acme Corp")) as search:
            out = graph.entity_resolution_and_read_rbac_node(state)
        search.assert_not_called()
        assert out["ambiguous_candidates"] is None and out["resolved_entities"] == {}
        assert state["collected_fields"]["customer_name"] == "Acme"

    def test_entity_resolution_still_resolves_the_customer_named_by_an_update_and_a_quotation(self):
        state = {"detected_intent": "update-customer", "user_roles": ["Sales User"], "collected_fields": {"customer_name": "acme industries", "territory": "India"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("Acme Industries")):
            out = graph.entity_resolution_and_read_rbac_node(state)
        assert out["resolved_entities"]["customer_name"]["name"] == "Acme Industries"
        quote = {"detected_intent": "create-quotation", "user_roles": ["Sales User"], "collected_fields": {"customer": "acme industries"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("Acme Industries")):
            out = graph.entity_resolution_and_read_rbac_node(quote)
        assert out["resolved_entities"]["customer"]["name"] == "Acme Industries"

    def test_an_exact_existing_name_is_refused_whatever_the_case(self):
        state = {"detected_intent": "create-customer", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"customer_name": "ACME industries"}}
        with patch.object(graph, "search_document", return_value=found("Acme Industries", "Acme Corp")):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False
        assert out["failure_classification"] == "validation_error"
        assert "Customer 'Acme Industries' already exists" in out["final_response"] and "nothing was created" in out["final_response"]

    def test_a_similar_but_different_name_is_allowed_through(self):
        state = {"detected_intent": "create-customer", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"customer_name": "Acme"}}
        with patch.object(graph, "search_document", return_value=found("Acme Industries", "Acme Corp")), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": []}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is True

    @pytest.mark.parametrize("status,klass", [("system_error", "tool_system_failure"), ("permission_denied", "permission_denied")])
    def test_if_the_existing_names_cannot_be_checked_nothing_is_created(self, status, klass):
        state = {"detected_intent": "create-customer", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"customer_name": "Northwind"}}
        with patch.object(graph, "search_document", return_value={"status": status, "data": None, "error": "boom"}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and out["failure_classification"] == klass
        assert "Nothing was created" in out["final_response"]

    def test_a_create_always_asks_and_lists_the_details_and_similar_names(self):
        state = {"detected_intent": "create-customer", "preconditions_validated": True, "write_rbac_operation": "create", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Sales User"],
                 "collected_fields": {"customer_name": "Acme", "customer_type": "Company", "customer_group": "Commercial"}}
        with patch.object(graph, "search_document", return_value=found("Acme Industries", "Acme Corp")):
            out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to create a Customer for customer name 'Acme'?" in text
        assert "Details:" in text and "- customer type: Company" in text and "- customer group: Commercial" in text
        assert "- customer name" not in text, "the name is already in the question"
        assert "Note: Customer records with a similar name already exist: Acme Industries, Acme Corp." in text
        assert out["pending_confirmation"]["operation"] == "create"
        assert "Reply **Yes**" in out["final_response"]

    def test_a_create_with_no_similar_names_asks_without_the_note(self):
        state = {"detected_intent": "create-customer", "preconditions_validated": True, "write_rbac_operation": "create", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Sales User"], "collected_fields": {"customer_name": "Northwind", "customer_type": "Company"}}
        with patch.object(graph, "search_document", return_value={"status": "empty", "data": []}):
            out = graph.confirmation_node(state)
        assert "similar name" not in out["pending_confirmation"]["scope_description"]

    def test_creates_of_other_doctypes_are_not_affected(self):
        assert graph._describe_create_extras("Sales Order", {"customer": "A"}) == ""

    # ---- update ----------------------------------------------------------------------------
    def test_update_needs_a_customer_and_a_change(self):
        assert self.validate("update-customer", {"customer_name": "Acme"})["all_required_filled"] is False
        assert self.validate("update-customer", {"territory": "India"})["all_required_filled"] is False
        assert self.validate("update-customer", {"customer_name": "Acme", "territory": "India"})["all_required_filled"] is True

    def test_the_record_is_the_customer_the_user_named(self):
        skill = graph._get_skill_for_intent("update-customer")
        assert graph._target_record_id({"customer_name": "Acme Industries", "territory": "India"}, {}, skill) == "Acme Industries"
        assert graph._target_record_id({"name": "Explicit", "customer_name": "Other"}, {}, skill) == "Explicit"
        assert graph._target_record_id({"customer_name": "Acme"}, {}, graph._get_skill_for_intent("update-sales-order")) is None

    def test_the_identity_slot_is_never_part_of_what_an_update_sends(self):
        payload = graph._update_payload(graph._get_skill_for_intent("update-customer"),
                                        {"customer_name": "Acme Industries", "territory": "India", "credit_limits": 1, "tax_id": "1"})
        assert payload == {"territory": "India"}

    def test_the_update_goes_to_the_named_customer_with_the_version(self):
        state = {"detected_intent": "update-customer", "collected_fields": {"customer_name": "Acme Industries", "customer_group": "Government"},
                 "last_read_snapshot": {"name": "Acme Industries", "modified": ACME["modified"]}}
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with("Customer", "Acme Industries", {"customer_group": "Government"}, expected_modified=ACME["modified"])

    def test_every_update_asks_and_shows_old_and_new_values(self):
        state = {"detected_intent": "update-customer", "preconditions_validated": True, "write_rbac_operation": "update", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Sales User"], "last_read_snapshot": ACME,
                 "collected_fields": {"customer_name": "Acme Industries", "customer_group": "Government", "website": "https://acme.example"}}
        out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to update Customer 'Acme Industries'?" in text
        assert "- customer_group: from Commercial to Government" in text
        assert "- website: from (empty) to https://acme.example" in text
        assert out["pending_confirmation"]["target_name"] == "Acme Industries"
        assert out["pending_confirmation"]["target_modified"] == ACME["modified"]

    def test_verification_checks_the_changed_fields_only(self):
        state = {"detected_intent": "update-customer", "write_rbac_operation": "update", "target_doctype": "Customer",
                 "tool_raw_response": {"success": True, "data": {"name": "Acme Industries"}},
                 "collected_fields": {"customer_name": "Acme Industries", "customer_group": "Government"}}
        with patch.object(graph, "get_document", return_value=ok({**ACME, "customer_group": "Government"})):
            assert graph.result_validation_node(state)["write_verified"] is True
        with patch.object(graph, "get_document", return_value=ok(ACME)):
            out = graph.result_validation_node(state)
        assert out["write_verified"] is False and "customer_group" in out["final_response"]

    def test_the_write_gate_allows_sales_roles_and_refuses_others(self):
        gate = lambda roles: graph.write_rbac_gate_node({"detected_intent": "update-customer", "user_roles": roles, "collected_fields": {}})["write_rbac_passed"]
        assert gate(["Sales User"]) and gate(["Sales Manager"])
        assert not gate(["Accounts User"]) and not gate(["Stock User"])


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

SALES = ["Sales User"]


class CustomerERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Customer"] = [
            {"name": "Acme Corp", "customer_name": "Acme Corp", "customer_type": "Company", "customer_group": "Commercial", "territory": "India", "website": None, "modified": "2026-11-20 10:00:00.000001"},
            {"name": "Acme Corporation", "customer_name": "Acme Corporation", "customer_type": "Company", "customer_group": "Commercial", "territory": "India", "website": None, "modified": "2026-11-20 10:00:00.000001"},
            dict(ACME),
        ]
        self.version = 0

    def create_document(self, doctype, fields):
        self.calls.append(("create_document", doctype, copy.deepcopy(fields)))
        doc = dict(copy.deepcopy(fields), name=fields["customer_name"], modified="2026-11-20 10:00:00.000001")
        self.db[doctype].append(doc)
        return _res("success", copy.deepcopy(doc))

    def update_document(self, doctype, name, fields, expected_modified=None):
        self.calls.append(("update_document", doctype, name, copy.deepcopy(fields), expected_modified))
        doc = self.customer(name)
        if expected_modified and expected_modified != doc["modified"]:
            return _res("system_error", error=f"Conflict: {doctype} {name} was changed by someone else after it was last read, so nothing was changed.")
        doc.update(fields)
        self.version += 1
        doc["modified"] = f"2026-11-20 12:00:00.{self.version:06d}"
        return _res("success", copy.deepcopy(doc))

    def customer(self, name):
        return next(r for r in self.db["Customer"] if r["name"] == name)

    def writes_of(self, op):
        return [c for c in self.calls if c[0] == op]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = CustomerERP()
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


def test_e2e_a_new_customer_is_previewed_confirmed_and_created(env):
    msg = "add a customer called Northwind Traders in the Commercial customer group"
    script(env, msg, "create-customer", {"customer_name": "Northwind Traders", "customer_group": "Commercial"})

    out = env.text(msg, roles=SALES)
    assert "Are you sure you want to create a Customer for customer name 'Northwind Traders'?" in out
    assert "- customer type: Company" in out and "- customer group: Commercial" in out
    assert "similar name" not in out
    assert env.erp.writes_of("create_document") == [], "nothing is created before the Yes"

    out = env.text("yes", roles=SALES)
    assert out == "FORMATTED[create-customer]"
    (call,) = env.erp.writes_of("create_document")
    assert call[1:] == ("Customer", {"customer_name": "Northwind Traders", "customer_type": "Company", "customer_group": "Commercial"})
    assert env.erp.customer("Northwind Traders")["customer_group"] == "Commercial"
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_similar_names_are_shown_and_a_no_creates_nothing(env):
    msg = "add a customer Acme"
    script(env, msg, "create-customer", {"customer_name": "Acme"})
    out = env.text(msg, roles=SALES)
    assert "similar name already exist: Acme Corp, Acme Corporation, Acme Industries" in out
    out = env.text("no", roles=SALES)
    assert "cancelled" in out.lower()
    assert env.erp.writes_of("create_document") == []
    assert len(env.erp.db["Customer"]) == 3


def test_e2e_an_exact_existing_name_is_refused_without_asking(env):
    msg = "create a customer Acme Corp"
    script(env, msg, "create-customer", {"customer_name": "acme corp"})
    out = env.text(msg, roles=SALES)
    assert "Customer 'Acme Corp' already exists" in out
    assert env.erp.writes_of("create_document") == []
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_the_name_typed_for_a_new_customer_is_never_swapped_for_an_existing_one(env):
    msg = "create a customer Acme Ind"
    script(env, msg, "create-customer", {"customer_name": "Acme Ind"})
    out = env.text(msg, roles=SALES)
    assert "customer name 'Acme Ind'?" in out, "not 'Acme Industries'"
    env.text("yes", roles=SALES)
    (call,) = env.erp.writes_of("create_document")
    assert call[2]["customer_name"] == "Acme Ind"


def test_e2e_an_update_shows_old_and_new_values_and_changes_only_what_was_asked(env):
    msg = "change the customer group of Acme Industries to Government"
    script(env, msg, "update-customer", {"customer_name": "Acme Industries", "customer_group": "Government"})

    out = env.text(msg, roles=SALES)
    assert "Are you sure you want to update Customer 'Acme Industries'?" in out
    assert "- customer_group: from Commercial to Government" in out
    assert env.erp.writes_of("update_document") == []

    out = env.text("yes", roles=SALES)
    assert out == "FORMATTED[update-customer]"
    (call,) = env.erp.writes_of("update_document")
    assert call[1:4] == ("Customer", "Acme Industries", {"customer_group": "Government"})
    assert call[4] == "2026-11-20 10:00:00.000001"
    assert env.erp.customer("Acme Industries")["customer_group"] == "Government"
    assert env.erp.customer("Acme Industries")["territory"] == "India"


def test_e2e_the_customer_is_matched_whatever_the_case(env):
    msg = "reassign customer acme industries to territory Rest Of The World"
    script(env, msg, "update-customer", {"customer_name": "acme industries", "territory": "Rest Of The World"})
    out = env.text(msg, roles=SALES)
    assert "update Customer 'Acme Industries'?" in out
    env.text("yes", roles=SALES)
    assert env.erp.customer("Acme Industries")["territory"] == "Rest Of The World"


def test_e2e_an_ambiguous_customer_asks_which_one_and_writes_nothing_meanwhile(env):
    msg = "change the customer group of Acme to Government"
    script(env, msg, "update-customer", {"customer_name": "Acme", "customer_group": "Government"})
    out = env.text(msg, roles=SALES)
    assert "Acme Corp" in out and "Acme Corporation" in out and "Acme Industries" in out
    assert env.erp.writes_of("update_document") == []
    assert all(c["customer_group"] == "Commercial" for c in env.erp.db["Customer"])


def test_e2e_off_limits_fields_in_the_request_are_ignored_and_nothing_is_changed(env):
    msg = "set a credit limit of 500000 for Acme Industries"
    script(env, msg, "update-customer", {"customer_name": "Acme Industries", "credit_limits": 500000, "tax_id": "99", "disabled": 1})
    env.text(msg, roles=SALES)
    assert env.erp.writes_of("update_document") == []
    state = env.persisted()
    assert state["pending_confirmation"] is None
    kept = state["collected_fields"] or {}
    assert not {"credit_limits", "tax_id", "disabled"} & set(kept), f"off-limits fields were kept: {kept}"
    assert env.erp.customer("Acme Industries")["modified"] == "2026-11-20 10:00:00.000001", "the customer was not touched"


def test_e2e_a_role_without_sales_rights_cannot_create_or_update(env):
    msg = "add a customer Northwind Traders"
    script(env, msg, "create-customer", {"customer_name": "Northwind Traders"})
    out = env.text(msg, roles=["Accounts User"])
    assert "Denied" in out
    assert env.erp.writes_of("create_document") == []
    msg2 = "change the customer group of Acme Industries to Government"
    script(env, msg2, "update-customer", {"customer_name": "Acme Industries", "customer_group": "Government"})
    out = env.text(msg2, roles=["Accounts User"], conversation_id="conv-2")
    assert "Denied" in out
    assert env.erp.writes_of("update_document") == []


def test_e2e_an_edit_between_the_question_and_the_yes_is_refused(env):
    msg = "change the customer group of Acme Industries to Government"
    script(env, msg, "update-customer", {"customer_name": "Acme Industries", "customer_group": "Government"})
    env.text(msg, roles=SALES)
    env.erp.customer("Acme Industries")["modified"] = "2026-11-20 11:00:00.000009"  # a colleague saves it
    out = env.text("yes", roles=SALES)
    assert "was changed after you were asked to confirm" in out
    assert env.erp.writes_of("update_document") == []
    assert env.erp.customer("Acme Industries")["customer_group"] == "Commercial"
