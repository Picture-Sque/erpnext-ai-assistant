"""
agent/tests/test_compound_graph.py

Tests for the compound-chaining routing fix (branch: fix/compound-chaining).

Coverage:
  (a) fresh compound message with NO pending_followup_steps reaches plan_compound_chain
  (b) non-compound messages do NOT enter plan_compound_chain
  (c) invalid is_compound value is logged and treated as False
  (d) best-seller->stock->PO chain stops at confirmation gate when stock is low
  (e) stock not low -> "no PO needed", no write attempted
"""

import logging
import sys
import os
import pytest
from unittest.mock import patch
from langchain_core.messages import HumanMessage, AIMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workflow"))

from workflow.graph import (
    route_after_classify,
    plan_compound_chain_node,
    prepare_chain_step_node,
    classify_intent_node,
    confirmation_node,
    result_validation_node,
    route_after_result_validation,
    CHAIN_LOW_STOCK_THRESHOLD,
)
from workflow.state import AgentState


# ===========================================================================
# (a) Fresh compound message with NO pending_followup_steps -> planner
# ===========================================================================

def test_fresh_compound_message_reaches_planner():
    """Fresh compound message (is_compound=True, no pending steps) -> plan_compound_chain."""
    state = AgentState(
        detected_intent="sales-analytics-report",
        is_compound=True,
        pending_followup_steps=[],
        messages=[HumanMessage(
            content="Find our best-selling item, check its stock, and create a PO if stock is low"
        )],
    )
    result = route_after_classify(state)
    assert result == "plan_compound_chain", (
        f"Expected 'plan_compound_chain' but got '{result}'. "
        "Fresh compound messages must go to the planner."
    )


def test_in_progress_chain_still_reaches_planner():
    """In-progress chain (pending_followup_steps non-empty) -> plan_compound_chain even with is_compound=False."""
    state = AgentState(
        detected_intent="check-inventory",
        is_compound=False,
        pending_followup_steps=["create PO if stock is low"],
        messages=[HumanMessage(content="check stock of ITEM-DESK-001")],
    )
    result = route_after_classify(state)
    assert result == "plan_compound_chain"


# ===========================================================================
# (b) Non-compound messages do NOT enter plan_compound_chain
# ===========================================================================

def test_single_action_stock_check_bypasses_planner():
    state = AgentState(
        detected_intent="check-inventory",
        is_compound=False,
        pending_followup_steps=[],
        messages=[HumanMessage(content="What is the stock of ITEM-DESK-001?")],
    )
    result = route_after_classify(state)
    assert result == "collect_parameters"


def test_single_action_create_so_bypasses_planner():
    state = AgentState(
        detected_intent="create-sales-order",
        is_compound=False,
        pending_followup_steps=[],
        messages=[HumanMessage(content="Create a sales order for Acme Corp")],
    )
    result = route_after_classify(state)
    assert result == "collect_parameters"


def test_fallback_intent_bypasses_planner():
    state = AgentState(
        detected_intent="fallback",
        is_compound=False,
        pending_followup_steps=[],
        messages=[HumanMessage(content="hello")],
    )
    result = route_after_classify(state)
    assert result == "fallback_response"


# ===========================================================================
# (c) Invalid is_compound value -> logged warning, treated as False
# ===========================================================================

def test_invalid_is_compound_string_treated_as_false(caplog):
    """LLM returns is_compound as string 'yes' -> coerced to False with WARNING."""
    state = AgentState(
        messages=[HumanMessage(content="What is the stock of ITEM-DESK-001?")],
        detected_intent="",
        pending_followup_steps=[],
    )
    mock_res = {
        "intent": "check-inventory",
        "is_compound": "yes",
        "pending_followup_steps": [],
    }
    with patch("workflow.graph.invoke_structured_llm", return_value=mock_res), \
         patch("workflow.graph.loaded_skills", [
             {"name": "check-inventory", "description": "Check inventory", "keywords": []},
         ]), \
         caplog.at_level(logging.WARNING, logger="workflow_graph"):
        result = classify_intent_node(state)

    assert result.get("is_compound") is False
    assert any("non-boolean" in record.message for record in caplog.records)


def test_invalid_is_compound_integer_treated_as_false(caplog):
    """LLM returns is_compound as integer 1 -> coerced to False with WARNING."""
    state = AgentState(
        messages=[HumanMessage(content="Check stock")],
        detected_intent="",
        pending_followup_steps=[],
    )
    mock_res = {
        "intent": "check-inventory",
        "is_compound": 1,
        "pending_followup_steps": [],
    }
    with patch("workflow.graph.invoke_structured_llm", return_value=mock_res), \
         patch("workflow.graph.loaded_skills", [
             {"name": "check-inventory", "description": "Check inventory", "keywords": []},
         ]), \
         caplog.at_level(logging.WARNING, logger="workflow_graph"):
        result = classify_intent_node(state)

    assert result.get("is_compound") is False
    assert any("non-boolean" in record.message for record in caplog.records)


def test_valid_is_compound_true_stored():
    """LLM returns is_compound as Python True (bool) -> stored as True, no coercion."""
    state = AgentState(
        messages=[HumanMessage(
            content="Find our best-selling item, check its stock, and create a PO if stock is low"
        )],
        detected_intent="",
        pending_followup_steps=[],
    )
    mock_res = {
        "intent": "sales-analytics-report",
        "is_compound": True,
        "pending_followup_steps": ["check stock", "create PO if stock is low"],
    }
    with patch("workflow.graph.invoke_structured_llm", return_value=mock_res), \
         patch("workflow.graph.loaded_skills", [
             {"name": "sales-analytics-report", "description": "Sales analytics", "keywords": []},
         ]):
        result = classify_intent_node(state)

    assert result.get("is_compound") is True
    assert result.get("pending_followup_steps") == ["check stock", "create PO if stock is low"]


# ===========================================================================
# (d) best-seller->stock->PO chain stops at confirmation when stock is low
# ===========================================================================

def test_chain_stops_at_confirmation_when_stock_low():
    """When stock is low the chain must stop at the confirmation gate (not execute the write)."""
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report", "slots": {}, "uses": {}, "condition": None},
            {"skill": "check-inventory", "slots": {}, "uses": {"item_code": "$step0.item_code"}, "condition": None},
            {
                "skill": "create-purchase-order",
                "slots": {"qty": 50},
                "uses": {"item_code": "$step1.item_code"},
                "condition": {
                    "field": "$step1.actual_qty",
                    "operator": "<=",
                    "value": CHAIN_LOW_STOCK_THRESHOLD,
                },
            },
        ],
        chain_step_index=2,
        chain_results={
            "0": {"item_code": "ITEM-BEST-001"},
            "1": {"item_code": "ITEM-BEST-001", "actual_qty": CHAIN_LOW_STOCK_THRESHOLD - 1},
        },
        chain_aborted=False,
        write_rbac_operation="create",
        detected_intent="create-purchase-order",
        preconditions_validated=True,
        collected_fields={"qty": 50},
    )

    prep_res = prepare_chain_step_node(state)
    assert prep_res.get("chain_aborted") is not True, "Chain must not abort when condition is met."
    assert prep_res.get("detected_intent") == "create-purchase-order"

    # Build state for confirmation_node: merge prep_res and supply required fields.
    # confirmation_node needs a record identifier (name/id in collected_fields or resolved_entities)
    # and target_doctype. For a chain create, we supply these explicitly to simulate what the
    # full graph would have populated by this point.
    confirm_state = {
        **state,
        **prep_res,
        "collected_fields": {
            **(prep_res.get("collected_fields") or {}),
            "name": "NEW-PO-DRAFT",  # simulated draft identifier for the new PO
        },
        "target_doctype": "Purchase Order",
    }
    confirm_res = confirmation_node(confirm_state)
    assert "pending_confirmation" in confirm_res, (
        f"PO write must await confirmation. confirmation_node returned: {confirm_res}"
    )
    assert "ITEM-BEST-001" in confirm_res.get("final_response", ""), \
        "Confirmation must surface chain findings."


# ===========================================================================
# (e) Stock not low -> "no PO needed", no write
# ===========================================================================

def test_chain_no_po_when_stock_sufficient():
    """When stock > threshold the PO step must be skipped with 'no PO needed' message."""
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report", "slots": {}, "uses": {}, "condition": None},
            {"skill": "check-inventory", "slots": {}, "uses": {}, "condition": None},
            {
                "skill": "create-purchase-order",
                "slots": {},
                "uses": {},
                "condition": {
                    "field": "$step1.actual_qty",
                    "operator": "<=",
                    "value": CHAIN_LOW_STOCK_THRESHOLD,
                },
            },
        ],
        chain_step_index=2,
        chain_results={
            "0": {"item_code": "ITEM-BEST-001"},
            "1": {"item_code": "ITEM-BEST-001", "actual_qty": CHAIN_LOW_STOCK_THRESHOLD + 5},
        },
        chain_aborted=False,
    )
    res = prepare_chain_step_node(state)
    assert res.get("is_workflow_complete") is True
    assert res.get("chain_aborted") is False
    final_resp = res.get("final_response", "").lower()
    assert "condition not met" in final_resp or "no po needed" in final_resp


def test_chain_default_threshold_no_po_when_stock_sufficient():
    """When condition.value omitted and stock > default threshold, no write occurs."""
    state = AgentState(
        chain_plan=[
            {"skill": "check-inventory", "slots": {}, "uses": {}, "condition": None},
            {
                "skill": "create-purchase-order",
                "slots": {},
                "uses": {},
                "condition": {
                    "field": "$step0.actual_qty",
                    "operator": "<=",
                },
            },
        ],
        chain_step_index=1,
        chain_results={
            "0": {"actual_qty": CHAIN_LOW_STOCK_THRESHOLD + 20},
        },
        chain_aborted=False,
    )
    res = prepare_chain_step_node(state)
    assert res.get("is_workflow_complete") is True
    assert res.get("chain_aborted") is False
    final_resp = res.get("final_response", "").lower()
    assert "condition not met" in final_resp or "no po needed" in final_resp


# ===========================================================================
# Planner resets is_compound to False after consuming it
# ===========================================================================

def test_planner_resets_is_compound():
    """After plan_compound_chain_node builds a plan, is_compound must be False."""
    state = AgentState(
        messages=[HumanMessage(
            content="Find our best-selling item, check its stock, and create a PO if stock is low"
        )],
        is_compound=True,
        pending_followup_steps=["check stock", "create PO if stock is low"],
    )
    mock_plan = {
        "steps": [
            {"skill": "sales-analytics-report", "slots": {}, "uses": {}, "condition": None},
            {"skill": "check-inventory", "slots": {}, "uses": {"item_code": "$step0.item_code"}, "condition": None},
            {
                "skill": "create-purchase-order",
                "slots": {},
                "uses": {"item_code": "$step1.item_code"},
                "condition": {"field": "$step1.actual_qty", "operator": "<=", "value": CHAIN_LOW_STOCK_THRESHOLD},
            },
        ]
    }
    with patch("workflow.graph.invoke_structured_llm", return_value=mock_plan):
        res = plan_compound_chain_node(state)

    assert "chain_plan" in res
    assert res.get("is_compound") is False, \
        f"is_compound must be reset after planner consumes it, got {res.get('is_compound')!r}"


# ===========================================================================
# result_validation_node chain progression for read-only skills
# ===========================================================================

def test_result_validation_advances_chain_on_read_only_skill():
    """Read-only skill in a chain must store results and advance chain_step_index."""
    state = AgentState(
        detected_intent="sales-analytics-report",
        chain_plan=[
            {"skill": "sales-analytics-report", "slots": {}, "uses": {}, "condition": None},
            {"skill": "check-inventory", "slots": {}, "uses": {"item_code": "$step0.item_code"}, "condition": None},
        ],
        chain_step_index=0,
        chain_results={},
        chain_aborted=False,
        collected_fields={"query_type": "best_selling_item_qty"},
        tool_raw_response={
            "success": True,
            "data": [{"item_code": "ITEM-DESK-001", "qty": 580.0}],
        },
    )
    res = result_validation_node(state)
    assert res.get("chain_step_index") == 1
    assert "0" in res.get("chain_results", {})
    assert res["chain_results"]["0"].get("item_code") == "ITEM-DESK-001"
    assert res["chain_results"]["0"].get("qty") == 580.0
    # Next step exists, so per-turn fields must be cleared
    assert res.get("detected_intent") == ""
    assert res.get("collected_fields") == {}


def test_route_after_result_validation_chain_flow():
    """route_after_result_validation routes to prepare_chain_step while steps remain, format_agent_message when done."""
    state_in_progress = AgentState(
        chain_plan=[{"skill": "step1"}, {"skill": "step2"}],
        chain_step_index=1,
        chain_aborted=False,
    )
    assert route_after_result_validation(state_in_progress) == "prepare_chain_step"

    state_completed = AgentState(
        chain_plan=[{"skill": "step1"}, {"skill": "step2"}],
        chain_step_index=2,
        chain_aborted=False,
    )
    assert route_after_result_validation(state_completed) == "format_agent_message"

