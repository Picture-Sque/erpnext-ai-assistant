"""
Item through the conversation pipeline, from node level to a full /chat run.

Mirrors the Customer and Supplier flows (test_customer_flow.py, test_supplier_flow.py) with the Item
specifics: stock roles, the item code is both the create's ID and the update's identity slot, the item
group is required on create, and prices, stock settings, the code and the unit are never writable.
The end-to-end part reuses the harness of test_mixed_chains.py.
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

DESK = {"name": "ITEM-DESK-001", "item_code": "ITEM-DESK-001", "item_name": "Office Desk", "item_group": "Products",
        "stock_uom": "Nos", "description": "A desk", "brand": None, "modified": "2026-11-20 10:00:00.000001"}


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "ITEM-DESK-001"}, "error": None, "has_more": False}


def found(*codes):
    return {"status": "success", "data": [{"name": c, "item_code": c, "item_name": c} for c in codes]}


@pytest.fixture
def quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


@pytest.mark.usefixtures("quiet_audit")
class TestNodes:
    def validate(self, intent, collected):
        return graph.validate_parameters_node({"detected_intent": intent, "collected_fields": collected})

    def test_create_needs_a_code_and_a_group(self):
        assert self.validate("create-item", {"item_code": "W-1", "item_group": "Products"})["all_required_filled"] is True
        for partial in ({}, {"item_code": "W-1"}, {"item_group": "Products"}):
            assert self.validate("create-item", partial)["all_required_filled"] is False

    def test_a_create_sends_only_declared_fields(self):
        state = {"detected_intent": "create-item", "collected_fields": {
            "item_code": "W-1", "item_group": "Products", "item_name": "Widget", "description": "d",
            "standard_rate": 99, "is_stock_item": 0, "valuation_rate": 5, "disabled": 1, "barcodes": [{"barcode": "1"}]}}
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        assert create.call_args.args == ("Item", {"item_code": "W-1", "item_group": "Products", "item_name": "Widget", "description": "d"})

    def test_slot_extraction_ignores_prices_and_stock_settings(self):
        llm = {"item_code": "W-1", "item_group": "Products", "standard_rate": 99, "is_stock_item": 0, "opening_stock": 500}
        with patch.object(graph, "invoke_structured_llm", return_value=llm):
            out = graph.collect_parameters_node({"detected_intent": "create-item", "messages": [MagicMock(content="create W-1")], "collected_fields": {}})
        assert set(out["collected_fields"]) == {"item_code", "item_group"}

    def test_entity_resolution_never_replaces_the_code_of_an_item_being_created(self):
        state = {"detected_intent": "create-item", "user_roles": ["Stock Manager"], "collected_fields": {"item_code": "ITEM-DESK", "item_group": "Products"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("ITEM-DESK-001")) as search:
            out = graph.entity_resolution_and_read_rbac_node(state)
        search.assert_not_called()
        assert out["ambiguous_candidates"] is None and state["collected_fields"]["item_code"] == "ITEM-DESK"

    def test_an_item_named_by_an_update_is_still_resolved(self):
        state = {"detected_intent": "update-item", "user_roles": ["Stock Manager"], "collected_fields": {"item_code": "item-desk-001", "brand": "Ergo"}, "resolved_entities": {}}
        with patch.object(graph, "search_document", return_value=found("ITEM-DESK-001")):
            out = graph.entity_resolution_and_read_rbac_node(state)
        assert out["resolved_entities"]["item_code"]["name"] == "ITEM-DESK-001"

    def test_an_exact_existing_code_is_refused_whatever_the_case(self):
        state = {"detected_intent": "create-item", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"item_code": "item-desk-001", "item_group": "Products"}}
        with patch.object(graph, "search_document", return_value=found("ITEM-DESK-001", "ITEM-DESK-002")):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and out["failure_classification"] == "validation_error"
        assert "Item 'ITEM-DESK-001' already exists" in out["final_response"]

    def test_a_similar_code_is_allowed_and_a_failed_lookup_creates_nothing(self):
        state = {"detected_intent": "create-item", "write_rbac_operation": "create", "resolved_entities": {}, "collected_fields": {"item_code": "ITEM-DESK", "item_group": "Products"}}
        with patch.object(graph, "search_document", return_value=found("ITEM-DESK-001")), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": []}):
            assert graph.precondition_validation_node(state)["preconditions_validated"] is True
        with patch.object(graph, "search_document", return_value={"status": "system_error", "data": None, "error": "boom"}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False and "Nothing was created" in out["final_response"]

    def test_a_create_always_asks_with_details_and_similar_codes(self):
        state = {"detected_intent": "create-item", "preconditions_validated": True, "write_rbac_operation": "create", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Stock Manager"],
                 "collected_fields": {"item_code": "ITEM-DESK", "item_group": "Products", "item_name": "Desk", "stock_uom": "Nos"}}
        with patch.object(graph, "search_document", return_value=found("ITEM-DESK-001", "ITEM-DESK-002")):
            out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to create an Item for item code 'ITEM-DESK'?" in text
        assert "- item group: Products" in text and "- item name: Desk" in text and "- stock uom: Nos" in text
        assert "Note: Item records with a similar name already exist: ITEM-DESK-001, ITEM-DESK-002." in text

    def test_update_needs_an_item_and_a_change_and_targets_the_named_item(self):
        assert self.validate("update-item", {"item_code": "ITEM-DESK-001"})["all_required_filled"] is False
        assert self.validate("update-item", {"item_code": "ITEM-DESK-001", "brand": "Ergo"})["all_required_filled"] is True
        skill = graph._get_skill_for_intent("update-item")
        assert graph._target_record_id({"item_code": "ITEM-DESK-001", "brand": "Ergo"}, {}, skill) == "ITEM-DESK-001"
        payload = graph._update_payload(skill, {"item_code": "ITEM-DESK-001", "brand": "Ergo", "standard_rate": 5, "stock_uom": "Kg", "disabled": 1})
        assert payload == {"brand": "Ergo"}, "neither the code, the unit, prices nor flags can be sent"

    def test_the_update_goes_to_the_named_item_with_the_version(self):
        state = {"detected_intent": "update-item", "collected_fields": {"item_code": "ITEM-DESK-001", "item_group": "Services"},
                 "last_read_snapshot": {"name": "ITEM-DESK-001", "modified": DESK["modified"]}}
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with("Item", "ITEM-DESK-001", {"item_group": "Services"}, expected_modified=DESK["modified"])

    def test_every_update_asks_and_shows_old_and_new_values(self):
        state = {"detected_intent": "update-item", "preconditions_validated": True, "write_rbac_operation": "update", "resolved_entities": {}, "messages": [],
                 "user_sub": "u", "user_roles": ["Stock Manager"], "last_read_snapshot": DESK,
                 "collected_fields": {"item_code": "ITEM-DESK-001", "item_group": "Services", "brand": "Ergo"}}
        out = graph.confirmation_node(state)
        text = out["pending_confirmation"]["scope_description"]
        assert "Are you sure you want to update Item 'ITEM-DESK-001'?" in text
        assert "- item_group: from Products to Services" in text and "- brand: from (empty) to Ergo" in text

    def test_the_write_gate_allows_stock_roles_and_refuses_others(self):
        gate = lambda intent, roles: graph.write_rbac_gate_node({"detected_intent": intent, "user_roles": roles, "collected_fields": {}})["write_rbac_passed"]
        for intent in ("create-item", "update-item"):
            assert gate(intent, ["Stock Manager"]) and gate(intent, ["Item Manager"])
            for roles in (["Sales User"], ["Purchase Manager"], ["Stock User"], ["Accounts User"]):
                assert not gate(intent, roles)


# ---------------------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------------------

STOCK = ["Stock Manager"]


class ItemERP(FakeERP):
    def __init__(self):
        super().__init__()
        self.db["Item"] = [
            dict(DESK),
            dict(DESK, name="ITEM-DESK-002", item_code="ITEM-DESK-002", item_name="Large Desk", description="A large desk"),
            {"name": "RM-001", "item_code": "RM-001", "item_name": "Raw Material 1", "item_group": "Raw Material", "stock_uom": "Nos", "description": None, "brand": None, "modified": "2026-11-20 10:00:00.000001"},
        ]
        self.version = 0

    def search_document(self, doctype, query):
        """Like the real search: matches the code and the name, case-insensitively."""
        self.calls.append(("search_document", doctype, query))
        q = query.lower()
        rows = [copy.deepcopy(r) for r in self.db.get(doctype, []) if q in str(r.get("item_code", "")).lower() or q in str(r.get("item_name", "")).lower() or q in str(r.get("name", "")).lower()]
        return _res("success" if rows else "empty", rows)

    def create_document(self, doctype, fields):
        self.calls.append(("create_document", doctype, copy.deepcopy(fields)))
        doc = dict(copy.deepcopy(fields), name=fields["item_code"], modified="2026-11-20 10:00:00.000001")
        doc.setdefault("stock_uom", "Nos")
        self.db[doctype].append(doc)
        return _res("success", copy.deepcopy(doc))

    def update_document(self, doctype, name, fields, expected_modified=None):
        self.calls.append(("update_document", doctype, name, copy.deepcopy(fields), expected_modified))
        doc = self.item(name)
        if expected_modified and expected_modified != doc["modified"]:
            return _res("system_error", error=f"Conflict: {doctype} {name} was changed by someone else after it was last read, so nothing was changed.")
        doc.update(fields)
        self.version += 1
        doc["modified"] = f"2026-11-20 12:00:00.{self.version:06d}"
        return _res("success", copy.deepcopy(doc))

    def item(self, name):
        return next(r for r in self.db["Item"] if r["name"] == name)

    def writes_of(self, op):
        return [c for c in self.calls if c[0] == op]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    audit_logger.init_audit_db()
    erp = ItemERP()
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


def test_e2e_a_new_item_is_previewed_confirmed_and_created(env):
    msg = "create item WIDGET-100 called Widget in the Products item group"
    script(env, msg, "create-item", {"item_code": "WIDGET-100", "item_name": "Widget", "item_group": "Products"})
    out = env.text(msg, roles=STOCK)
    assert "Are you sure you want to create an Item for item code 'WIDGET-100'?" in out
    assert "- item group: Products" in out and "- item name: Widget" in out
    assert "similar name" not in out
    assert env.erp.writes_of("create_document") == []

    assert env.text("yes", roles=STOCK) == "FORMATTED[create-item]"
    (call,) = env.erp.writes_of("create_document")
    assert call[1:] == ("Item", {"item_code": "WIDGET-100", "item_name": "Widget", "item_group": "Products"})
    assert env.erp.item("WIDGET-100")["stock_uom"] == "Nos"
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_similar_codes_are_shown_and_a_no_creates_nothing(env):
    msg = "create item ITEM-DESK in the Products group"
    script(env, msg, "create-item", {"item_code": "ITEM-DESK", "item_group": "Products"})
    out = env.text(msg, roles=STOCK)
    assert "similar name already exist: ITEM-DESK-001, ITEM-DESK-002" in out
    assert "cancelled" in env.text("no", roles=STOCK).lower()
    assert env.erp.writes_of("create_document") == [] and len(env.erp.db["Item"]) == 3


def test_e2e_an_exact_existing_code_is_refused_without_asking(env):
    msg = "create item item-desk-001 in the Products group"
    script(env, msg, "create-item", {"item_code": "item-desk-001", "item_group": "Products"})
    out = env.text(msg, roles=STOCK)
    assert "Item 'ITEM-DESK-001' already exists" in out
    assert env.erp.writes_of("create_document") == [] and env.persisted()["pending_confirmation"] is None


def test_e2e_a_create_without_a_group_asks_for_the_group(env):
    msg = "create item WIDGET-200"
    script(env, msg, "create-item", {"item_code": "WIDGET-200"})
    env.text(msg, roles=STOCK)
    assert env.erp.writes_of("create_document") == []
    assert env.persisted()["pending_confirmation"] is None


def test_e2e_prices_and_stock_settings_in_the_request_are_never_sent(env):
    msg = "create item WIDGET-300 in Products with a price of 500 and no stock tracking"
    script(env, msg, "create-item", {"item_code": "WIDGET-300", "item_group": "Products", "standard_rate": 500, "is_stock_item": 0, "valuation_rate": 400})
    env.text(msg, roles=STOCK)
    env.text("yes", roles=STOCK)
    (call,) = env.erp.writes_of("create_document")
    assert call[2] == {"item_code": "WIDGET-300", "item_group": "Products"}


def test_e2e_an_update_shows_old_and_new_values_and_changes_only_what_was_asked(env):
    msg = "change the item group of ITEM-DESK-001 to Services"
    script(env, msg, "update-item", {"item_code": "ITEM-DESK-001", "item_group": "Services"})
    out = env.text(msg, roles=STOCK)
    assert "Are you sure you want to update Item 'ITEM-DESK-001'?" in out
    assert "- item_group: from Products to Services" in out
    assert env.erp.writes_of("update_document") == []

    assert env.text("yes", roles=STOCK) == "FORMATTED[update-item]"
    (call,) = env.erp.writes_of("update_document")
    assert call[1:4] == ("Item", "ITEM-DESK-001", {"item_group": "Services"})
    assert call[4] == "2026-11-20 10:00:00.000001"
    assert env.erp.item("ITEM-DESK-001")["item_group"] == "Services"
    assert env.erp.item("ITEM-DESK-001")["item_name"] == "Office Desk"


def test_e2e_an_ambiguous_item_asks_which_one_and_writes_nothing_meanwhile(env):
    msg = "change the item group of ITEM-DESK to Services"
    script(env, msg, "update-item", {"item_code": "ITEM-DESK", "item_group": "Services"})
    out = env.text(msg, roles=STOCK)
    assert "ITEM-DESK-001" in out and "ITEM-DESK-002" in out
    assert env.erp.writes_of("update_document") == []


def test_e2e_the_code_the_unit_and_prices_cannot_be_changed(env):
    msg = "rename ITEM-DESK-001 to DESK-NEW, set its unit to Kg and its price to 900"
    script(env, msg, "update-item", {"item_code": "ITEM-DESK-001", "stock_uom": "Kg", "standard_rate": 900, "disabled": 1})
    env.text(msg, roles=STOCK)
    assert env.erp.writes_of("update_document") == []
    state = env.persisted()
    assert state["pending_confirmation"] is None
    assert not {"stock_uom", "standard_rate", "disabled"} & set(state["collected_fields"] or {})
    assert env.erp.item("ITEM-DESK-001")["modified"] == "2026-11-20 10:00:00.000001"


def test_e2e_sales_purchasing_and_plain_stock_user_roles_cannot_write_items(env):
    msg = "create item WIDGET-400 in Products"
    script(env, msg, "create-item", {"item_code": "WIDGET-400", "item_group": "Products"})
    for i, roles in enumerate((["Sales User"], ["Purchase Manager"], ["Stock User"], ["Accounts User"])):
        assert "Denied" in env.text(msg, roles=roles, conversation_id=f"c{i}")
    msg2 = "change the item group of ITEM-DESK-001 to Services"
    script(env, msg2, "update-item", {"item_code": "ITEM-DESK-001", "item_group": "Services"})
    assert "Denied" in env.text(msg2, roles=["Stock User"], conversation_id="c9")
    assert env.erp.writes_of("create_document") == [] and env.erp.writes_of("update_document") == []


def test_e2e_an_edit_between_the_question_and_the_yes_is_refused(env):
    msg = "change the item group of ITEM-DESK-001 to Services"
    script(env, msg, "update-item", {"item_code": "ITEM-DESK-001", "item_group": "Services"})
    env.text(msg, roles=STOCK)
    env.erp.item("ITEM-DESK-001")["modified"] = "2026-11-20 11:00:00.000009"
    out = env.text("yes", roles=STOCK)
    assert "was changed after you were asked to confirm" in out
    assert env.erp.writes_of("update_document") == []
    assert env.erp.item("ITEM-DESK-001")["item_group"] == "Products"
