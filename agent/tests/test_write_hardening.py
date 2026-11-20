"""
Graph-level tests for write-path hardening:
- an update sends only the fields the skill declares and the registry allows
- slot extraction cannot add fields a write skill never declared
- create payloads (including item rows) are reduced to registry-declared fields
- the duplicate check uses registry keys, so Purchase Orders get it too
- bulk actions are capped and bulk delete is refused
- the write gate fails closed and agrees with the registry
- a write skill that exceeds the registry is not loaded
- write verification checks what was sent, not everything collected
"""

import copy
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from agent.workflow import graph


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    monkeypatch.setattr(graph, "log_audit_event", MagicMock())


def ok(data=None):
    return {"status": "success", "success": True, "data": data or {"name": "SO-1"}, "error": None, "has_more": False}


def skill(name):
    return graph._get_skill_for_intent(name)


class TestUpdatePayload:
    def test_update_sends_only_declared_fields(self):
        state = {
            "detected_intent": "update-sales-order",
            "collected_fields": {
                "name": "SO-1",
                "delivery_date": "2026-12-01",
                "docstatus": 1,
                "grand_total": 5,
                "status": "Closed",
                "filters": [["customer", "=", "X"]],
                "_auto_resolved_note": "note",
            },
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with("Sales Order", "SO-1", {"delivery_date": "2026-12-01"})

    def test_purchase_order_update_keeps_its_declared_fields(self):
        state = {
            "detected_intent": "update-purchase-order",
            "collected_fields": {"name": "PO-1", "schedule_date": "2026-12-01", "transaction_date": "2026-11-20", "docstatus": 2},
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(state)
        upd.assert_called_once_with(
            "Purchase Order", "PO-1", {"schedule_date": "2026-12-01", "transaction_date": "2026-11-20"}
        )

    def test_update_with_nothing_changeable_is_not_sent(self):
        state = {
            "detected_intent": "update-sales-order",
            "collected_fields": {"name": "SO-1", "docstatus": 1, "grand_total": 5},
        }
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            out = graph.call_generic_tool_node(state)
        upd.assert_not_called()
        assert out["failure_classification"] == "validation_error"
        assert out["tool_raw_response"]["success"] is False


class TestCreatePayload:
    def test_create_drops_undeclared_keys_in_document_and_item_rows(self):
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {
                "customer": "Acme",
                "delivery_date": "2026-12-01",
                "transaction_date": "2026-11-20",
                "items": [{"item_code": "SKU1", "qty": 2, "item_name": "Widget", "docstatus": 1, "amount": 99}],
            },
        }
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        doctype, payload = create.call_args.args
        assert doctype == "Sales Order"
        assert payload["customer"] == "Acme"
        row = payload["items"][0]
        assert row == {"item_code": "SKU1", "qty": 2, "delivery_date": "2026-12-01"}


class TestSlotExtraction:
    def _state(self, intent, text="go"):
        return {"detected_intent": intent, "messages": [HumanMessage(content=text)], "collected_fields": {}}

    def test_write_skill_ignores_undeclared_llm_keys(self):
        llm = {"name": "SO-1", "delivery_date": "2026-12-01", "docstatus": 1, "grand_total": 9, "owner": "x"}
        with patch.object(graph, "invoke_structured_llm", return_value=llm):
            out = graph.collect_parameters_node(self._state("update-sales-order", "update SO-1 delivery to 2026-12-01"))
        assert out["collected_fields"] == {"name": "SO-1", "delivery_date": "2026-12-01"}

    def test_write_skill_keeps_filters_for_bulk_requests(self):
        llm = {"filters": [["customer", "=", "X"]], "docstatus": 2}
        with patch.object(graph, "invoke_structured_llm", return_value=llm):
            out = graph.collect_parameters_node(self._state("cancel-sales-order", "cancel all orders for X"))
        assert "filters" in out["collected_fields"]
        assert "docstatus" not in out["collected_fields"]

    def test_read_skill_is_unchanged(self):
        llm = {"customer_name": "Acme", "something_extra": "kept"}
        with patch.object(graph, "invoke_structured_llm", return_value=llm):
            out = graph.collect_parameters_node(self._state("customer-lookup", "find Acme"))
        assert out["collected_fields"].get("something_extra") == "kept"


class TestDuplicateCheck:
    def _recent(self, **extra):
        return {"name": "PO-9", "creation": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"), **extra}

    def test_purchase_order_create_is_checked_by_supplier(self):
        state = {
            "detected_intent": "create-purchase-order",
            "write_rbac_operation": "create",
            "collected_fields": {"supplier": "Summit Traders", "items": [{"item_code": "A", "qty": 1}]},
        }
        with patch.object(graph, "get_list", return_value={"status": "success", "data": [self._recent(supplier="Summit Traders")]}) as gl:
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False
        assert "same supplier" in out["final_response"]
        kwargs = gl.call_args.kwargs
        assert kwargs["filters"] == [["supplier", "=", "Summit Traders"]]
        assert "supplier" in kwargs["fields"]

    def test_sales_order_create_still_checked_by_customer(self):
        state = {
            "detected_intent": "create-sales-order",
            "write_rbac_operation": "create",
            "collected_fields": {"customer": "Acme", "items": [{"item_code": "A", "qty": 1}]},
        }
        with patch.object(graph, "get_list", return_value={"status": "success", "data": [self._recent(customer="Acme")]}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is False
        assert "same customer" in out["final_response"]

    def test_no_recent_duplicate_passes(self):
        state = {
            "detected_intent": "create-purchase-order",
            "write_rbac_operation": "create",
            "collected_fields": {"supplier": "Summit Traders", "items": [{"item_code": "A", "qty": 1}]},
        }
        with patch.object(graph, "get_list", return_value={"status": "success", "data": []}):
            out = graph.precondition_validation_node(state)
        assert out["preconditions_validated"] is True


class TestBulkLimits:
    def _confirm_state(self):
        return {
            "detected_intent": "cancel-sales-order",
            "preconditions_validated": True,
            "write_rbac_operation": "cancel",
            "collected_fields": {"filters": [["customer", "=", "X"]]},
            "resolved_entities": {},
            "messages": [],
            "user_sub": "u@example.com",
            "user_roles": ["Sales Manager"],
        }

    def test_bulk_over_cap_is_refused_before_asking(self, monkeypatch):
        monkeypatch.setenv("WRITE_BULK_MAX", "25")
        with patch.object(graph, "get_count", return_value={"status": "success", "data": 26}), \
                patch.object(graph, "get_list") as gl:
            out = graph.confirmation_node(self._confirm_state())
        assert "pending_confirmation" not in out
        assert "limit for one request is 25" in out["final_response"]
        assert out["failure_classification"] == "validation_error"
        gl.assert_not_called()

    def test_bulk_within_cap_asks_for_confirmation(self, monkeypatch):
        monkeypatch.setenv("WRITE_BULK_MAX", "25")
        with patch.object(graph, "get_count", return_value={"status": "success", "data": 3}), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": [{"name": "SO-1"}]}):
            out = graph.confirmation_node(self._confirm_state())
        assert out["pending_confirmation"]["is_bulk"] is True
        assert out["pending_confirmation"]["affected_count"] == 3

    def test_confirmed_bulk_over_cap_is_aborted_at_execution(self, monkeypatch):
        monkeypatch.setenv("WRITE_BULK_MAX", "25")
        state = {
            "detected_intent": "cancel-sales-order",
            "collected_fields": {},
            "bulk_operation_scope": {
                "doctype": "Sales Order", "filters": [["customer", "=", "X"]], "affected_count": 40, "confirmed": True,
            },
        }
        with patch.object(graph, "get_count", return_value={"status": "success", "data": 40}), \
                patch.object(graph, "get_list") as gl, \
                patch.object(graph, "cancel_document") as cancel:
            out = graph.call_generic_tool_node(state)
        assert out["tool_raw_response"]["success"] is False
        assert "limit of 25" in out["tool_raw_response"]["error"]
        gl.assert_not_called()
        cancel.assert_not_called()

    def test_confirmed_bulk_read_is_bounded_by_the_cap(self, monkeypatch):
        monkeypatch.setenv("WRITE_BULK_MAX", "25")
        state = {
            "detected_intent": "cancel-sales-order",
            "collected_fields": {},
            "bulk_operation_scope": {
                "doctype": "Sales Order", "filters": [["customer", "=", "X"]], "affected_count": 2, "confirmed": True,
            },
        }
        with patch.object(graph, "get_count", return_value={"status": "success", "data": 2}), \
                patch.object(graph, "get_list", return_value={"status": "success", "data": [{"name": "SO-1"}, {"name": "SO-2"}]}) as gl, \
                patch.object(graph, "cancel_document", return_value=ok()) as cancel:
            out = graph.call_generic_tool_node(state)
        assert gl.call_args.kwargs["limit"] == 25
        assert cancel.call_count == 2
        assert out["tool_raw_response"]["data"]["bulk_success_count"] == 2

    def test_bulk_delete_is_never_executed(self):
        state = {
            "detected_intent": "delete-sales-order",
            "target_tool": "delete_document",
            "target_doctype": "Sales Order",
            "collected_fields": {},
            "bulk_operation_scope": {
                "doctype": "Sales Order", "filters": [["customer", "=", "X"]], "affected_count": 2, "confirmed": True,
            },
        }
        with patch.object(graph, "get_count", return_value={"status": "success", "data": 2}), \
                patch.object(graph, "delete_document") as delete:
            out = graph.call_generic_tool_node(state)
        delete.assert_not_called()
        assert out["tool_raw_response"]["success"] is False


class TestWriteGate:
    def _state(self, intent, roles):
        return {"detected_intent": intent, "user_roles": roles, "collected_fields": {"name": "SO-1"}}

    def test_registry_must_agree_with_the_skill(self, monkeypatch):
        widened = copy.deepcopy(skill("submit-sales-order"))
        widened["allowed_roles"] = list(widened["allowed_roles"]) + ["Accounts User"]
        monkeypatch.setattr(graph, "loaded_skills", [widened])
        out = graph.write_rbac_gate_node(self._state("submit-sales-order", ["Accounts User"]))
        assert out["write_rbac_passed"] is False
        assert "write policy" in out["final_response"]

    def test_write_skill_without_roles_is_not_open_to_everyone(self, monkeypatch):
        open_skill = copy.deepcopy(skill("submit-sales-order"))
        open_skill["allowed_roles"] = []
        monkeypatch.setattr(graph, "loaded_skills", [open_skill])
        out = graph.write_rbac_gate_node(self._state("submit-sales-order", ["Sales Manager"]))
        assert out["write_rbac_passed"] is False

    def test_allowed_role_still_passes(self):
        out = graph.write_rbac_gate_node(self._state("submit-sales-order", ["Sales Manager"]))
        assert out["write_rbac_passed"] is True

    def test_wrong_role_denied(self):
        out = graph.write_rbac_gate_node(self._state("submit-sales-order", ["Sales User"]))
        assert out["write_rbac_passed"] is False


class TestSkillLoading:
    def _write(self, root, folder, text):
        d = root / folder
        d.mkdir()
        (d / "SKILL.md").write_text(text, encoding="utf-8")

    def test_write_skill_beyond_the_registry_is_not_loaded(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            "bad-write",
            "---\nname: bad-write\nintent: bad-write\ndescription: broader than the registry\n"
            "allowed_roles:\n  - Sales User\n  - Accounts User\ntool: update_document\ndoctype: Sales Order\n"
            "required_fields:\n  - name\n  - grand_total\n---\nbody\n",
        )
        self._write(
            tmp_path,
            "good-read",
            "---\nname: good-read\nintent: good-read\ndescription: a read skill\ntool: get_list\ndoctype: Customer\n---\nbody\n",
        )
        monkeypatch.setattr(graph.os.path, "abspath", lambda p: str(tmp_path))
        names = [s["name"] for s in graph.load_skills()]
        assert "bad-write" not in names
        assert "good-read" in names

    def test_shipped_write_skills_all_load(self):
        names = {s["name"] for s in graph.loaded_skills if s["tool"] in graph._WRITE_TOOLS}
        assert names == {
            "create-sales-order", "update-sales-order", "submit-sales-order", "cancel-sales-order",
            "create-purchase-order", "update-purchase-order", "submit-purchase-order", "cancel-purchase-order",
            "create-quotation", "update-quotation", "submit-quotation", "cancel-quotation",
            "create-customer", "update-customer",
            "create-supplier", "update-supplier",
            "create-item", "update-item",
            "convert-quotation-to-sales-order",
        }


class TestWriteVerification:
    def test_update_verification_checks_only_what_was_sent(self):
        doc = {
            "name": "SO-1",
            "delivery_date": "2026-12-01",
            "status": "Draft",
            "items": [{"item_code": "A", "delivery_date": "2026-12-01"}],
        }
        state = {
            "detected_intent": "update-sales-order",
            "write_rbac_operation": "update",
            "target_doctype": "Sales Order",
            "tool_raw_response": {"success": True, "data": {"name": "SO-1"}},
            "collected_fields": {"name": "SO-1", "delivery_date": "2026-12-01", "status": "Submitted"},
        }
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.result_validation_node(state)
        assert out["write_verified"] is True
        assert "failure_classification" not in out

    def test_update_verification_still_catches_a_sent_field_that_did_not_land(self):
        doc = {"name": "SO-1", "delivery_date": "2026-11-01", "items": []}
        state = {
            "detected_intent": "update-sales-order",
            "write_rbac_operation": "update",
            "target_doctype": "Sales Order",
            "tool_raw_response": {"success": True, "data": {"name": "SO-1"}},
            "collected_fields": {"name": "SO-1", "delivery_date": "2026-12-01"},
        }
        with patch.object(graph, "get_document", return_value=ok(doc)):
            out = graph.result_validation_node(state)
        assert out["write_verified"] is False
        assert out["failure_classification"] == "conflict_stale_data"


class TestWritesAreNeverRetried:
    """
    A retry goes straight back to the tool step. For a write that skips the preconditions
    and the confirmation, and repeats a change that may already have been applied.
    """

    def _write_state(self, **extra):
        state = {
            "detected_intent": "create-sales-order",
            "write_rbac_operation": "create",
            "target_doctype": "Sales Order",
            "failure_classification": "tool_system_failure",
            "retry_count": 0,
            "collected_fields": {"customer": "Acme"},
        }
        state.update(extra)
        return state

    def test_failed_write_is_not_retried(self):
        state = self._write_state(
            target_tool="create_document",
            tool_raw_response={"success": False, "status": "system_error", "error": "HTTP 502"},
        )
        out = graph._retry_and_escalation_node_impl(state)
        assert out["is_workflow_complete"] is True
        assert "retry_count" not in out
        assert "HTTP 502" in out["final_response"]
        assert "not retried automatically" in out["final_response"]
        assert "check ERPNext" in out["final_response"]

    def test_route_after_a_failed_write_never_returns_to_the_tool(self):
        state = self._write_state(target_tool="create_document", tool_raw_response={"error": "timeout"})
        merged = {**state, **graph._retry_and_escalation_node_impl(state)}
        assert graph.route_after_retry_and_escalation(merged) != "call_generic_tool"

    def test_precondition_failure_cannot_fall_through_to_the_write(self):
        # The duplicate check could not run. The message says nothing was created and the
        # write must not be reached by a retry.
        state = self._write_state(
            preconditions_validated=False,
            final_response="Could not check for a duplicate Sales Order before creating it: boom. Nothing was created.",
        )
        out = graph._retry_and_escalation_node_impl(state)
        assert out["is_workflow_complete"] is True
        assert out["final_response"].startswith("Could not check for a duplicate")
        assert "not retried automatically" not in out["final_response"]
        merged = {**state, **out}
        assert graph.route_after_retry_and_escalation(merged) != "call_generic_tool"

    def test_rate_limit_on_a_write_is_not_retried_either(self):
        state = self._write_state(failure_classification="transient_rate_limit", target_tool="update_document")
        out = graph._retry_and_escalation_node_impl(state)
        assert out["is_workflow_complete"] is True
        assert "retry_count" not in out

    def test_failed_write_is_audited(self):
        state = self._write_state(target_tool="create_document", tool_raw_response={"error": "HTTP 500"})
        graph._retry_and_escalation_node_impl(state)
        assert graph.log_audit_event.call_args.kwargs["outcome"] == "failed"

    def test_reads_are_still_retried(self):
        state = {
            "detected_intent": "customer-lookup",
            "failure_classification": "tool_system_failure",
            "retry_count": 0,
            "target_doctype": "Customer",
            "target_tool": "get_list",
        }
        out = graph._retry_and_escalation_node_impl(state)
        assert out["is_workflow_complete"] is False
        assert out["retry_count"] == 1


MODIFIED = "2026-11-20 10:15:30.123456"
NEWER = "2026-11-20 10:20:00.000001"


class TestConcurrencyInTheGraph:
    def _confirm_state(self, **extra):
        state = {
            "detected_intent": "submit-sales-order",
            "preconditions_validated": True,
            "write_rbac_operation": "submit",
            "collected_fields": {"name": "SO-1"},
            "resolved_entities": {},
            "messages": [],
            "user_sub": "u@example.com",
            "user_roles": ["Sales Manager"],
            "last_read_snapshot": {"name": "SO-1", "docstatus": 0, "modified": MODIFIED},
        }
        state.update(extra)
        return state

    def test_confirmation_records_the_version_the_user_is_asked_about(self):
        out = graph.confirmation_node(self._confirm_state())
        assert out["pending_confirmation"]["target_modified"] == MODIFIED

    def test_confirmation_ignores_a_snapshot_of_another_record(self):
        state = self._confirm_state(last_read_snapshot={"name": "SO-2", "docstatus": 0, "modified": MODIFIED})
        out = graph.confirmation_node(state)
        assert out["pending_confirmation"]["target_modified"] is None

    def _precondition_state(self, asked):
        return {
            "detected_intent": "submit-sales-order",
            "write_rbac_operation": "submit",
            "collected_fields": {"name": "SO-1"},
            "resolved_entities": {},
            "confirmation_result": "confirmed",
            "confirmed_action": {"skill": "submit-sales-order", "operation": "submit", "target_modified": asked},
        }

    def test_edit_between_ask_and_yes_is_refused(self):
        fresh = {"name": "SO-1", "docstatus": 0, "modified": NEWER}
        with patch.object(graph, "get_document", return_value=ok(fresh)):
            out = graph.precondition_validation_node(self._precondition_state(MODIFIED))
        assert out["preconditions_validated"] is False
        assert out["failure_classification"] == "conflict_stale_data"
        assert "was changed after you were asked to confirm" in out["final_response"]
        assert "Nothing was executed" in out["final_response"]

    def test_unchanged_document_passes(self):
        fresh = {"name": "SO-1", "docstatus": 0, "modified": MODIFIED}
        with patch.object(graph, "get_document", return_value=ok(fresh)):
            out = graph.precondition_validation_node(self._precondition_state(MODIFIED))
        assert out["preconditions_validated"] is True

    def test_confirmation_without_a_recorded_version_still_works(self):
        fresh = {"name": "SO-1", "docstatus": 0, "modified": NEWER}
        with patch.object(graph, "get_document", return_value=ok(fresh)):
            out = graph.precondition_validation_node(self._precondition_state(None))
        assert out["preconditions_validated"] is True

    def _tool_state(self, intent, collected, snapshot_name="SO-1"):
        return {
            "detected_intent": intent,
            "collected_fields": collected,
            "last_read_snapshot": {"name": snapshot_name, "modified": MODIFIED},
        }

    def test_submit_and_cancel_carry_the_version(self):
        with patch.object(graph, "submit_document", return_value=ok()) as sub:
            graph.call_generic_tool_node(self._tool_state("submit-sales-order", {"name": "SO-1"}))
        sub.assert_called_once_with("Sales Order", "SO-1", expected_modified=MODIFIED)
        with patch.object(graph, "cancel_document", return_value=ok()) as can:
            graph.call_generic_tool_node(self._tool_state("cancel-sales-order", {"name": "SO-1"}))
        can.assert_called_once_with("Sales Order", "SO-1", expected_modified=MODIFIED)

    def test_update_carries_the_version(self):
        with patch.object(graph, "update_document", return_value=ok()) as upd:
            graph.call_generic_tool_node(
                self._tool_state("update-sales-order", {"name": "SO-1", "delivery_date": "2026-12-01"})
            )
        upd.assert_called_once_with("Sales Order", "SO-1", {"delivery_date": "2026-12-01"}, expected_modified=MODIFIED)

    def test_version_of_another_record_is_not_sent(self):
        with patch.object(graph, "submit_document", return_value=ok()) as sub:
            graph.call_generic_tool_node(self._tool_state("submit-sales-order", {"name": "SO-1"}, snapshot_name="SO-9"))
        sub.assert_called_once_with("Sales Order", "SO-1")

    def test_switch_turns_sending_off(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_SEND_MODIFIED", "false")
        with patch.object(graph, "submit_document", return_value=ok()) as sub:
            graph.call_generic_tool_node(self._tool_state("submit-sales-order", {"name": "SO-1"}))
        sub.assert_called_once_with("Sales Order", "SO-1")

    def test_create_never_sends_a_version(self):
        state = {
            "detected_intent": "create-sales-order",
            "collected_fields": {"customer": "Acme", "items": [{"item_code": "A", "qty": 1}], "delivery_date": "2026-12-01"},
            "last_read_snapshot": {"name": "SO-1", "modified": MODIFIED},
        }
        with patch.object(graph, "create_document", return_value=ok()) as create:
            graph.call_generic_tool_node(state)
        assert "expected_modified" not in create.call_args.kwargs

    def test_a_refused_write_is_a_conflict_that_is_not_retried(self):
        conflict = {
            "status": "system_error", "success": False, "data": None, "has_more": False,
            "error": "Conflict: Sales Order SO-1 was changed by someone else after it was last read, so nothing was changed.",
        }
        with patch.object(graph, "submit_document", return_value=conflict):
            out = graph.call_generic_tool_node(self._tool_state("submit-sales-order", {"name": "SO-1"}))
        assert out["failure_classification"] == "conflict_stale_data"
        assert "changed by someone else" in out["final_response"]
        assert "review the current document" in out["final_response"]

        merged = {**self._tool_state("submit-sales-order", {"name": "SO-1"}), **out, "retry_count": 0}
        retry = graph._retry_and_escalation_node_impl(merged)
        assert retry["is_workflow_complete"] is True
        assert "retry_count" not in retry
        assert graph.route_after_retry_and_escalation({**merged, **retry}) == "format_response"

    def test_an_ordinary_failure_is_still_not_a_conflict(self):
        failure = {"status": "system_error", "success": False, "data": None, "has_more": False, "error": "HTTP 500"}
        with patch.object(graph, "submit_document", return_value=failure):
            out = graph.call_generic_tool_node(self._tool_state("submit-sales-order", {"name": "SO-1"}))
        assert out["failure_classification"] == "tool_system_failure"
