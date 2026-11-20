"""
The ERPNext permission preflight inside the precondition step.

The write registry and the skill decide what the assistant may do for a role; this adds what
ERPNext itself says about the signed-in user. Both must allow. An unknown answer never blocks.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.workflow import graph
from mcp_erpnext.permissions import ALLOWED, DENIED, UNKNOWN, PermissionCheck

SO = {"name": "SO-1", "docstatus": 0, "modified": "2026-11-20 10:15:30.123456", "delivery_date": "2026-11-01", "items": [{"item_code": "A", "qty": 1, "rate": 5}]}


@pytest.fixture(autouse=True)
def audit(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(graph, "log_audit_event", mock)
    return mock


def ok(data):
    return {"status": "success", "success": True, "data": data, "error": None, "has_more": False}


def state(intent, operation, collected, **extra):
    base = {"detected_intent": intent, "write_rbac_operation": operation, "collected_fields": collected, "resolved_entities": {}}
    base.update(extra)
    return base


def run(st, outcome, doc=SO):
    check = MagicMock(return_value=PermissionCheck(outcome, "because"))
    with patch.object(graph, "check_permission", check), patch.object(graph, "get_document", return_value=ok(doc)), \
            patch.object(graph, "get_list", return_value={"status": "success", "data": []}):
        out = graph.precondition_validation_node(st)
    return out, check


class TestDenied:
    def test_erpnext_saying_no_stops_the_write_before_anything_is_asked(self, audit):
        out, check = run(state("update-sales-order", "update", {"name": "SO-1", "delivery_date": "2026-12-01"}), DENIED)
        assert out["preconditions_validated"] is False
        assert out["failure_classification"] == "permission_denied"
        assert "Permission Denied" in out["final_response"]
        assert "not allowed to update Sales Order SO-1" in out["final_response"]
        assert "Nothing was executed" in out["final_response"]
        check.assert_called_once_with("update", "Sales Order", "SO-1")

    def test_the_denial_is_audited_with_its_own_classification(self, audit):
        run(state("submit-sales-order", "submit", {"name": "SO-1"}), DENIED)
        kwargs = audit.call_args.kwargs
        assert kwargs["operation"] == "submit" and kwargs["doctype"] == "Sales Order"
        assert kwargs["target_name"] == "SO-1" and kwargs["outcome"] == "denied"
        assert kwargs["failure_classification"] == "permission_denied_erpnext"

    def test_the_denial_ends_the_turn_as_a_permission_denial(self):
        out, _ = run(state("submit-sales-order", "submit", {"name": "SO-1"}), DENIED)
        merged = {**state("submit-sales-order", "submit", {"name": "SO-1"}), **out}
        retry = graph._retry_and_escalation_node_impl(merged)
        assert retry["is_workflow_complete"] is True
        assert "retry_count" not in retry
        assert graph.route_after_retry_and_escalation({**merged, **retry}) == "rbac_denied_response"
        assert graph.rbac_denied_response_node({**merged, **retry})["final_response"] == out["final_response"]

    def test_it_applies_to_every_write_operation(self):
        for intent, op, collected in (
            ("submit-sales-order", "submit", {"name": "SO-1"}),
            ("cancel-sales-order", "cancel", {"name": "SO-1"}),
            ("update-sales-order", "update", {"name": "SO-1", "po_no": "X"}),
        ):
            doc = {**SO, "docstatus": 1} if op == "cancel" else SO
            out, check = run(state(intent, op, collected), DENIED, doc)
            assert out["preconditions_validated"] is False, op
            assert check.call_args.args[0] == op

    def test_a_create_is_refused_without_naming_a_record(self):
        out, check = run(
            state("create-sales-order", "create", {"customer": "Acme", "items": [{"item_code": "A", "qty": 1}]}), DENIED
        )
        assert out["preconditions_validated"] is False
        assert "not allowed to create Sales Order." in out["final_response"]
        check.assert_called_once_with("create", "Sales Order", None)


class TestNotDenied:
    def test_allowed_goes_on_to_the_remaining_checks(self):
        out, check = run(state("update-sales-order", "update", {"name": "SO-1", "delivery_date": "2026-12-01"}), ALLOWED)
        assert out["preconditions_validated"] is True
        check.assert_called_once_with("update", "Sales Order", "SO-1")

    def test_unknown_is_not_a_denial(self):
        out, _ = run(state("update-sales-order", "update", {"name": "SO-1", "delivery_date": "2026-12-01"}), UNKNOWN)
        assert out["preconditions_validated"] is True

    def test_a_create_that_is_allowed_proceeds(self):
        out, _ = run(
            state("create-sales-order", "create", {"customer": "Acme", "items": [{"item_code": "A", "qty": 1}]}), ALLOWED
        )
        assert out["preconditions_validated"] is True

    def test_the_other_checks_still_run_after_an_allowed_answer(self):
        # ERPNext allowing it does not override the draft-only rule.
        out, _ = run(
            state("update-sales-order", "update", {"name": "SO-1", "item_updates": [{"item_code": "A", "qty": 2}]}),
            ALLOWED,
            {**SO, "docstatus": 1},
        )
        assert out["preconditions_validated"] is False
        assert "only allowed on a Draft" in out["final_response"]

    def test_a_cancelled_document_is_still_refused_when_erpnext_allows_it(self):
        out, _ = run(state("update-sales-order", "update", {"name": "SO-1", "po_no": "X"}), ALLOWED, {**SO, "docstatus": 2})
        assert out["preconditions_validated"] is False


class TestOrdering:
    def test_a_missing_document_is_reported_before_permissions_are_asked(self):
        check = MagicMock(return_value=PermissionCheck(DENIED))
        with patch.object(graph, "check_permission", check), \
                patch.object(graph, "get_document", return_value={"status": "not_found", "data": None, "error": "nope"}):
            out = graph.precondition_validation_node(state("submit-sales-order", "submit", {"name": "SO-9"}))
        assert out["failure_classification"] == "not_found"
        check.assert_not_called()

    def test_a_read_that_is_forbidden_is_reported_as_such_without_the_extra_call(self):
        check = MagicMock(return_value=PermissionCheck(ALLOWED))
        with patch.object(graph, "check_permission", check), \
                patch.object(graph, "get_document", return_value={"status": "permission_denied", "data": None, "error": "no"}):
            out = graph.precondition_validation_node(state("submit-sales-order", "submit", {"name": "SO-1"}))
        assert out["failure_classification"] == "permission_denied"
        check.assert_not_called()

    def test_bulk_requests_are_checked_at_doctype_level(self):
        check = MagicMock(return_value=PermissionCheck(ALLOWED))
        with patch.object(graph, "check_permission", check), patch.object(graph, "get_document") as get_doc:
            out = graph.precondition_validation_node(
                state("cancel-sales-order", "cancel", {"filters": [["customer", "=", "X"]]})
            )
        assert out["preconditions_validated"] is True
        check.assert_called_once_with("cancel", "Sales Order", None)
        get_doc.assert_not_called()

    def test_read_only_requests_never_ask(self):
        check = MagicMock(return_value=PermissionCheck(DENIED))
        with patch.object(graph, "check_permission", check):
            out = graph.precondition_validation_node({"detected_intent": "customer-lookup", "collected_fields": {}})
        assert out == {}
        check.assert_not_called()
