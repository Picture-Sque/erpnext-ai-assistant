"""
The update-sales-order flow with the widened skill: delivery date, customer PO reference and
quantity or rate changes on existing item rows.

Covers early validation, the Draft-only rule, confirmation with a before/after preview,
what is sent to ERPNext, and verification that each change landed.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.workflow import graph

SNAPSHOT = {
    "name": "SO-1",
    "docstatus": 0,
    "modified": "2026-11-20 10:15:30.123456",
    "delivery_date": "2026-11-01",
    "po_no": "PO-OLD",
    "items": [
        {"item_code": "DESK", "qty": 2, "rate": 100},
        {"item_code": "CHAIR", "qty": 4, "rate": 50},
    ],
}


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "SO-1"}, "error": None, "has_more": False}


def skill():
    return graph._get_skill_for_intent("update-sales-order")


def validate(collected):
    state = {"detected_intent": "update-sales-order", "collected_fields": collected}
    return graph.validate_parameters_node(state)


class TestSkillShape:
    def test_skill_loaded_with_the_widened_fields(self):
        s = skill()
        assert s is not None
        assert s["required_fields"] == ["name"]
        assert set(s["optional_fields"]) == {"delivery_date", "po_no", "po_date", "item_updates"}
        assert set(s["require_any_of"]) == set(s["optional_fields"])
        assert s["allowed_roles"] == ["Sales User", "Sales Manager", "System Manager", "Administrator"]

    def test_update_payload_carries_every_declared_change(self):
        payload = graph._update_payload(
            skill(),
            {
                "name": "SO-1", "delivery_date": "2026-12-01", "po_no": "PO-1", "po_date": "2026-11-15",
                "item_updates": [{"item_code": "DESK", "qty": 5, "amount": 1}], "docstatus": 1, "status": "x",
            },
        )
        assert payload == {
            "delivery_date": "2026-12-01", "po_no": "PO-1", "po_date": "2026-11-15",
            "item_updates": [{"item_code": "DESK", "qty": 5}],
        }


class TestValidation:
    def test_one_change_is_enough(self):
        for collected in (
            {"name": "SO-1", "delivery_date": "2026-12-01"},
            {"name": "SO-1", "po_no": "PO-1"},
            {"name": "SO-1", "po_date": "2026-11-15"},
            {"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]},
        ):
            assert validate(collected)["all_required_filled"] is True, collected

    def test_a_name_with_nothing_to_change_is_asked_about(self):
        out = validate({"name": "SO-1"})
        assert out["all_required_filled"] is False
        assert any("at least one thing to change" in m for m in out["missing_parameters"])

    def test_missing_name_is_still_required(self):
        out = validate({"delivery_date": "2026-12-01"})
        assert out["all_required_filled"] is False
        assert "name" in out["missing_parameters"]

    def test_a_date_that_is_not_a_date_is_dropped_and_asked_again(self):
        out = validate({"name": "SO-1", "delivery_date": "next Friday"})
        assert out["all_required_filled"] is False
        assert "delivery_date" not in out["collected_fields"]
        assert any("delivery date as a date written YYYY-MM-DD" in m for m in out["missing_parameters"])

    def test_a_good_change_survives_next_to_a_bad_date(self):
        out = validate({"name": "SO-1", "po_date": "soon", "po_no": "PO-1"})
        assert out["all_required_filled"] is False
        assert out["collected_fields"]["po_no"] == "PO-1"
        assert "po_date" not in out["collected_fields"]

    def test_item_changes_are_tidied(self):
        out = validate({"name": "SO-1", "item_updates": [{"item_code": " DESK ", "qty": "5", "rate": "12.50", "note": ""}]})
        assert out["all_required_filled"] is True
        assert out["collected_fields"]["item_updates"] == [{"item_code": "DESK", "qty": 5, "rate": 12.5}]

    def test_a_single_change_object_is_accepted_as_a_list(self):
        out = validate({"name": "SO-1", "item_updates": {"item_code": "DESK", "qty": 5}})
        assert out["all_required_filled"] is True
        assert out["collected_fields"]["item_updates"] == [{"item_code": "DESK", "qty": 5}]

    @pytest.mark.parametrize(
        "entry,expected",
        [
            ({"item_code": "DESK", "qty": 0}, "'qty' for DESK must be above zero"),
            ({"item_code": "DESK", "qty": -2}, "'qty' for DESK must be above zero"),
            ({"item_code": "DESK", "rate": -1}, "'rate' for DESK cannot be negative"),
            ({"item_code": "DESK", "qty": "many"}, "'qty' for DESK must be a number"),
            ({"item_code": "DESK"}, "does not say what to change"),
            ({"qty": 5}, "needs a item_code"),
        ],
    )
    def test_bad_item_changes_are_asked_about_not_sent(self, entry, expected):
        out = validate({"name": "SO-1", "item_updates": [entry]})
        assert out["all_required_filled"] is False
        assert any(expected in m for m in out["missing_parameters"]), out["missing_parameters"]
        assert "item_updates" not in out["collected_fields"]

    def test_other_write_skills_are_untouched(self):
        state = {
            "detected_intent": "submit-sales-order",
            "collected_fields": {"name": "SO-1"},
        }
        assert graph.validate_parameters_node(state)["all_required_filled"] is True

    def test_the_question_uses_the_hint(self):
        text = graph._describe_field(skill(), "item_updates")
        assert "item code" in text and "quantity" in text


class TestPreconditions:
    def _state(self, collected):
        return {
            "detected_intent": "update-sales-order",
            "write_rbac_operation": "update",
            "collected_fields": collected,
            "resolved_entities": {},
        }

    def test_item_changes_are_refused_on_a_submitted_order(self):
        doc = {**SNAPSHOT, "docstatus": 1}
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.precondition_validation_node(
                self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]})
            )
        assert out["preconditions_validated"] is False
        assert out["failure_classification"] == "validation_error"
        assert "is Submitted" in out["final_response"]
        assert "only allowed on a Draft" in out["final_response"]

    def test_item_changes_are_allowed_on_a_draft(self):
        with patch.object(graph, "get_document", return_value=ok(SNAPSHOT)):
            out = graph.precondition_validation_node(
                self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]})
            )
        assert out["preconditions_validated"] is True

    def test_a_date_change_is_still_allowed_on_a_submitted_order(self):
        doc = {**SNAPSHOT, "docstatus": 1}
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.precondition_validation_node(self._state({"name": "SO-1", "delivery_date": "2026-12-01"}))
        assert out["preconditions_validated"] is True

    def test_a_cancelled_order_cannot_be_updated(self):
        doc = {**SNAPSHOT, "docstatus": 2}
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.precondition_validation_node(self._state({"name": "SO-1", "po_no": "PO-1"}))
        assert out["preconditions_validated"] is False

    def test_nothing_to_change_is_caught_again_before_confirmation(self):
        with patch.object(graph, "get_document", return_value=ok(SNAPSHOT)):
            out = graph.precondition_validation_node(self._state({"name": "SO-1"}))
        assert out["preconditions_validated"] is False
        assert "at least one of" in out["final_response"]


class TestConfirmation:
    def _state(self, collected, snapshot=SNAPSHOT):
        return {
            "detected_intent": "update-sales-order",
            "preconditions_validated": True,
            "write_rbac_operation": "update",
            "collected_fields": collected,
            "resolved_entities": {},
            "messages": [],
            "user_sub": "u@example.com",
            "user_roles": ["Sales User"],
            "last_read_snapshot": snapshot,
        }

    def test_item_changes_always_ask_and_show_before_and_after(self):
        out = graph.confirmation_node(
            self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5, "rate": 90}, {"item_code": "CHAIR", "rate": 55}]})
        )
        pending = out["pending_confirmation"]
        text = pending["scope_description"]
        assert "Are you sure you want to update Sales Order 'SO-1'?" in text
        assert "Changes:" in text
        assert "- DESK: qty from 2 to 5, rate from 100 to 90" in text
        assert "- CHAIR: rate from 50 to 55" in text
        assert "Reply **Yes**" in out["final_response"]
        assert pending["operation"] == "update"
        assert pending["params"]["item_updates"][0]["qty"] == 5

    def test_a_date_or_po_change_on_a_draft_is_not_confirmed(self):
        for collected in ({"name": "SO-1", "delivery_date": "2026-12-01"}, {"name": "SO-1", "po_no": "PO-9"}):
            assert graph.confirmation_node(self._state(collected)) == {}, collected

    def test_confirmation_on_a_submitted_order_also_shows_the_change(self):
        snapshot = {**SNAPSHOT, "docstatus": 1}
        out = graph.confirmation_node(self._state({"name": "SO-1", "delivery_date": "2026-12-01"}, snapshot))
        text = out["pending_confirmation"]["scope_description"]
        assert "- delivery_date: from 2026-11-01 to 2026-12-01" in text

    def test_missing_current_value_is_shown_without_a_from(self):
        out = graph.confirmation_node(
            self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]}, snapshot={"name": "SO-9", "docstatus": 0})
        )
        text = out["pending_confirmation"]["scope_description"]
        assert "- DESK: qty to 5" in text
        assert "from" not in text.split("Changes:")[1]

    def test_the_pending_action_records_the_version_shown(self):
        out = graph.confirmation_node(self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]}))
        assert out["pending_confirmation"]["target_modified"] == SNAPSHOT["modified"]

    def test_confirmed_action_is_not_asked_again(self):
        state = self._state({"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5}]})
        state["confirmation_result"] = "confirmed"
        state["confirmed_action"] = {"skill": "update-sales-order", "operation": "update", "doctype": "Sales Order", "target_name": "SO-1"}
        out = graph.confirmation_node(state)
        assert out["pending_confirmation"] is None
        assert "final_response" not in out


class TestExecution:
    def test_the_whole_change_set_reaches_the_tool_with_the_version(self):
        state = {
            "detected_intent": "update-sales-order",
            "collected_fields": {
                "name": "SO-1", "po_no": "PO-9", "delivery_date": "2026-12-01",
                "item_updates": [{"item_code": "DESK", "qty": 5}], "docstatus": 1,
            },
            "last_read_snapshot": SNAPSHOT,
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with(
            "Sales Order",
            "SO-1",
            {"delivery_date": "2026-12-01", "po_no": "PO-9", "item_updates": [{"item_code": "DESK", "qty": 5}]},
            expected_modified=SNAPSHOT["modified"],
        )


class TestVerification:
    def _state(self, collected):
        return {
            "detected_intent": "update-sales-order",
            "write_rbac_operation": "update",
            "target_doctype": "Sales Order",
            "tool_raw_response": {"success": True, "data": {"name": "SO-1"}},
            "collected_fields": collected,
        }

    def _doc(self, **over):
        doc = {
            "name": "SO-1", "delivery_date": "2026-11-01", "po_no": "PO-OLD",
            "items": [{"item_code": "DESK", "qty": 5, "rate": 90, "delivery_date": "2026-11-01"}, {"item_code": "CHAIR", "qty": 4, "rate": 50}],
        }
        doc.update(over)
        return doc

    def test_changes_that_landed_are_verified(self):
        collected = {"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5, "rate": 90}], "po_no": "PO-OLD"}
        with patch.object(graph, "get_document", return_value=ok(self._doc())):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is True

    def test_a_quantity_that_did_not_change_is_caught(self):
        collected = {"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 8}]}
        with patch.object(graph, "get_document", return_value=ok(self._doc())):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is False
        assert out["failure_classification"] == "conflict_stale_data"
        assert "qty of 8 for DESK" in out["final_response"]
        assert "still shows 5" in out["final_response"]

    def test_a_rate_that_did_not_change_is_caught(self):
        collected = {"name": "SO-1", "item_updates": [{"item_code": "CHAIR", "rate": 60}]}
        with patch.object(graph, "get_document", return_value=ok(self._doc())):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is False
        assert "rate of 60 for CHAIR" in out["final_response"]

    def test_a_missing_row_is_caught(self):
        collected = {"name": "SO-1", "item_updates": [{"item_code": "SOFA", "qty": 1}]}
        with patch.object(graph, "get_document", return_value=ok(self._doc())):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is False
        assert "SOFA was not found" in out["final_response"]

    def test_numbers_are_compared_as_numbers(self):
        collected = {"name": "SO-1", "item_updates": [{"item_code": "DESK", "qty": 5, "rate": 90}]}
        doc = self._doc(items=[{"item_code": "DESK", "qty": 5.0, "rate": "90.0"}])
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is True

    def test_a_po_reference_that_did_not_change_is_caught(self):
        collected = {"name": "SO-1", "po_no": "PO-NEW"}
        with patch.object(graph, "get_document", return_value=ok(self._doc())):
            out = graph.result_validation_node(self._state(collected))
        assert out["write_verified"] is False
