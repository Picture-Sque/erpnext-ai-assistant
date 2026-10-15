"""
tests/test_compound_chaining.py

Tests for compound request chaining.
"""

import sys
import os
import pytest
from unittest.mock import patch
from langchain_core.messages import HumanMessage, AIMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workflow"))

from workflow.graph import (
    compiled_graph,
    CHAIN_LOW_STOCK_THRESHOLD,
    plan_compound_chain_node,
    prepare_chain_step_node,
    retry_and_escalation_node,
    result_validation_node,
    confirmation_node
)
from workflow.state import AgentState

def test_full_chains():
    state = AgentState(
        messages=[HumanMessage(content="find best-selling item, check stock, create PO if stock is low")],
        user_roles=["Sales User", "Stock User", "Purchase User"],
        pending_followup_steps=["check stock", "create PO if stock is low"],
    )
    
    with patch("workflow.graph.invoke_structured_llm") as mock_planner:
        mock_planner.return_value = {
            "steps": [
                {"skill": "sales-analytics-report", "slots": {"query_type": "best_selling"}, "uses": {}, "condition": None},
                {"skill": "check-inventory", "slots": {}, "uses": {"item_code": "$step0.item_code"}, "condition": None},
                {"skill": "create-purchase-order", "slots": {"qty": 50}, "uses": {"item_code": "$step1.item_code"}, 
                 "condition": {"field": "$step1.actual_qty", "operator": "<=", "value": CHAIN_LOW_STOCK_THRESHOLD}}
            ]
        }
        res = plan_compound_chain_node(state)
        
        assert "chain_plan" in res
        assert len(res["chain_plan"]) == 3
        assert res["chain_step_index"] == 0
        assert res["pending_followup_steps"] == []
        assert res["chain_aborted"] == False

def test_non_low_stock():
    # Condition: step1.actual_qty <= 10. Actual is 15.
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report"},
            {"skill": "check-inventory"},
            {"skill": "create-purchase-order", "condition": {"field": "$step1.actual_qty", "operator": "<=", "value": CHAIN_LOW_STOCK_THRESHOLD}}
        ],
        chain_step_index=2,
        chain_results={"1": {"actual_qty": 15}},
        chain_aborted=False
    )
    res = prepare_chain_step_node(state)
    assert res["chain_aborted"] is False
    assert res["is_workflow_complete"] is True
    assert "condition not met" in res["final_response"].lower()
    assert "was not run" in res["final_response"].lower()

def test_mid_chain_failure():
    # Mid-chain failure (e.g. not_found) fails only that step; the chain is not aborted
    # and routing continues with the next runnable step.
    from workflow.graph import route_after_retry_and_escalation
    state = AgentState(
        chain_plan=[{"skill": "step1"}, {"skill": "step2"}],
        chain_step_index=1,
        chain_results={"0": {"item_code": "SKU-001"}},
        failure_classification="not_found"
    )
    res = retry_and_escalation_node(state)
    assert res.get("chain_aborted") is not True
    assert res["chain_step_status"]["1"]["status"] == "failed"
    assert res["chain_step_status"]["0"]["status"] == "done"
    assert route_after_retry_and_escalation({**state, **res}) == "prepare_chain_step"

def test_confirmation_logic():
    # A chained create whose parameters come from an earlier step ('uses') needs
    # confirmation, and the prompt includes the earlier findings.
    state = AgentState(
        chain_plan=[{"skill": "step1"}, {"skill": "create-purchase-order", "uses": {"item_code": "$step0.item_code"}}],
        chain_step_index=1,
        chain_results={"0": {"item_code": "SKU-001", "actual_qty": 5}},
        write_rbac_operation="create",
        detected_intent="create-purchase-order",
        preconditions_validated=True,
        collected_fields={"name": "NEW-PO"}
    )
    res = confirmation_node(state)
    assert "pending_confirmation" in res
    assert res["pending_confirmation"]["step_index"] == 1
    assert res["chain_step_status"]["1"]["status"] == "awaiting_confirmation"
    assert "Step 0 findings" in res["final_response"]
    assert "SKU-001" in res["final_response"]
    assert "actual_qty: 5" in res["final_response"]

def test_invalid_planning():
    state = AgentState(messages=[HumanMessage(content="complex req")])
    with patch("workflow.graph.invoke_structured_llm") as mock_planner:
        mock_planner.return_value = {} # Invalid missing 'steps'
        res = plan_compound_chain_node(state)
        assert res["chain_aborted"] is True
        assert res["is_workflow_complete"] is True
        assert "couldn't plan" in res["final_response"].lower()

def test_rbac_denial():
    # RBAC denial fails that step with the denial reason; the chain is not aborted.
    state = AgentState(
        chain_plan=[{"skill": "step1"}, {"skill": "step2"}],
        chain_step_index=1,
        chain_results={"0": {"item_code": "SKU-001"}},
        failure_classification="permission_denied",
        messages=[AIMessage(content="Permission Denied")]
    )
    res = retry_and_escalation_node(state)
    assert res.get("chain_aborted") is not True
    assert res["escalated"] is True
    assert res["chain_step_status"]["1"]["status"] == "failed"
    assert "Repeated permission denial" in res["chain_step_status"]["1"]["message"]

def test_single_step_request():
    state = AgentState(
        messages=[HumanMessage(content="single req")],
    )
    with patch("workflow.graph.invoke_structured_llm") as mock_planner:
        mock_planner.return_value = {"steps": [{"skill": "sales-analytics-report", "slots": {}}]}
        res = plan_compound_chain_node(state)
        # Should fallback to normal route
        assert res == {}
def test_low_condition_boundary():
    # If actual_qty is EXACTLY 10 (CHAIN_LOW_STOCK_THRESHOLD), condition "<=" should pass
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report"},
            {"skill": "check-inventory"},
            {"skill": "create-purchase-order", "condition": {"field": "$step1.actual_qty", "operator": "<=", "value": CHAIN_LOW_STOCK_THRESHOLD}}
        ],
        chain_step_index=2,
        chain_results={"1": {"actual_qty": CHAIN_LOW_STOCK_THRESHOLD}},
        chain_aborted=False
    )
    res = prepare_chain_step_node(state)
    # Since condition passed, it should NOT abort, but prepare the step.
    assert res.get("chain_aborted") is not True

def test_unresolved_ref_aborts():
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report"},
            {"skill": "check-inventory", "uses": {"item_code": "$step99.item_code"}} # bad ref
        ],
        chain_step_index=1,
        chain_results={"0": {"item_code": "SKU-001"}},
        chain_aborted=False
    )
    res = prepare_chain_step_node(state)
    # The step with the bad reference fails and is reported; nothing else is left to run.
    assert res["chain_step_status"]["1"]["status"] == "failed"
    assert res["is_workflow_complete"] is True
    assert "unresolved reference" in res["final_response"].lower()

def test_low_threshold_constant():
    # val is omitted; should fallback to CHAIN_LOW_STOCK_THRESHOLD
    state = AgentState(
        chain_plan=[
            {"skill": "check-inventory"},
            {"skill": "create-purchase-order", "condition": {"field": "$step0.actual_qty", "operator": "<="}}
        ],
        chain_step_index=1,
        chain_results={"0": {"actual_qty": CHAIN_LOW_STOCK_THRESHOLD}},
        chain_aborted=False
    )
    res = prepare_chain_step_node(state)
    # should NOT abort, condition met
    assert res.get("chain_aborted") is not True
    assert res.get("detected_intent") == "create-purchase-order"

def test_invalid_plans():
    state = AgentState(messages=[HumanMessage(content="test")])
    with patch("workflow.graph.invoke_structured_llm") as mock_planner:
        # Unknown skill
        mock_planner.return_value = {"steps": [{"skill": "fake-skill", "slots": {}}, {"skill": "check-inventory", "slots": {}}]}
        res = plan_compound_chain_node(state)
        assert res["chain_aborted"] is True
        
        # 2 writes are allowed; they are serialized so each is handled (and, if
        # consequential, confirmed) on its own.
        mock_planner.return_value = {"steps": [{"skill": "create-purchase-order", "slots": {}}, {"skill": "create-sales-order", "slots": {}}]}
        res = plan_compound_chain_node(state)
        assert res.get("chain_aborted") is False
        assert res["chain_plan"][1]["depends_on"] == [0]

        # Forward ref
        mock_planner.return_value = {"steps": [{"skill": "check-inventory", "slots": {}, "uses": {"item_code": "$step1.item_code"}}, {"skill": "sales-analytics-report", "slots": {}}]}
        res = plan_compound_chain_node(state)
        assert res["chain_aborted"] is True

def test_multi_turn_chain():
    # If a chain needs clarification, graph pauses and chain_plan persists.
    # We just ensure confirmation_node includes findings even if turn > 1.
    state = AgentState(
        messages=[HumanMessage(content="create PO"), AIMessage(content="qty?"), HumanMessage(content="50")],
        chain_plan=[{"skill": "step1"}, {"skill": "create-purchase-order", "uses": {"item_code": "$step0.item_code"}}],
        chain_step_index=1,
        chain_results={"0": {"item_code": "SKU-001", "actual_qty": 5}},
        write_rbac_operation="create",
        detected_intent="create-purchase-order",
        preconditions_validated=True,
        collected_fields={"name": "NEW-PO", "supplier": "Acme", "qty": 50}
    )
    res = confirmation_node(state)
    assert "pending_confirmation" in res
    assert "Step 0 findings" in res["final_response"]
    assert "SKU-001" in res["final_response"]


def test_chain_exports_list_resolution():
    state = AgentState(
        chain_plan=[
            {"skill": "sales-analytics-report"},
            {"skill": "check-inventory", "uses": {"item_code": "$step0.top_item_code"}}
        ],
        chain_step_index=1,
        chain_results={"0": {"list_data": [{"item_code": "SKU-BEST", "qty": 100}]}},
        chain_aborted=False
    )
    with patch("workflow.graph.loaded_skills", [{"name": "sales-analytics-report", "chain_exports": {"top_item_code": "item_code"}}]):
        res = prepare_chain_step_node(state)
        
    assert res.get("chain_aborted") is not True
    assert res.get("collected_fields", {}).get("item_code") == "SKU-BEST"
