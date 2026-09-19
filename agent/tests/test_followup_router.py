"""
tests/test_followup_router.py

10 deterministic tests for the resolve_followup_node pre-classifier.

All LLM calls are mocked via unittest.mock.patch so tests run without
network access and produce deterministic results.

Run with:
    python -m pytest agent/tests/test_followup_router.py -v
  or from the agent/ directory:
    python -m pytest tests/test_followup_router.py -v
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from langchain_core.messages import HumanMessage, AIMessage

# Ensure agent/ is on the path when running from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workflow"))

# Module under test
from workflow.followup_router import (
    resolve_followup_node,
    route_after_followup_router,
    build_last_turn_context,
    FOLLOWUP_MAX_TURNS,
    FOLLOWUP_MAX_MINUTES,
    MAX_SLOT_CLARIFICATION_ATTEMPTS,
)


# ============================================================================
# Helpers
# ============================================================================

def _now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def _ago_iso(minutes: int):
    """ISO timestamp N minutes in the past."""
    return (datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _make_messages(*texts, alternating=True):
    """Build a message list alternating Human/AI."""
    msgs = []
    for i, t in enumerate(texts):
        if alternating:
            msgs.append(HumanMessage(content=t) if i % 2 == 0 else AIMessage(content=t))
        else:
            msgs.append(HumanMessage(content=t))
    return msgs


def _analytics_state(
    last_msg: str,
    last_slots: dict,
    turn_id: int,
    completed_at: str = None,
    follow_up_slots: list = None,
    extra_messages: int = 0,
):
    """Build a state with a completed sales-analytics-report last_turn_context."""
    completed_at = completed_at or _now_iso()
    follow_up_slots = follow_up_slots or ["date_from", "date_to", "query_type", "limit"]
    messages = [
        HumanMessage(content="What are our total sales in August 2026?"),
        AIMessage(content="Total sales in August 2026 were ₹2,570,700."),
    ]
    for i in range(extra_messages):
        messages.append(HumanMessage(content=f"extra message {i}"))
    messages.append(HumanMessage(content=last_msg))
    return {
        "messages": messages,
        "detected_intent": "sales-analytics-report",
        "collected_fields": dict(last_slots),
        "is_workflow_complete": True,
        "pending_confirmation": None,
        "pending_slot_clarification": None,
        "last_turn_context": {
            "skill": "sales-analytics-report",
            "slots": dict(last_slots),
            "turn_id": turn_id,
            "completed_at": completed_at,
            "follow_up_slots": list(follow_up_slots),
        },
    }


# ============================================================================
# TEST 1: Analytics → elliptical month follow-up variants
# ============================================================================

@pytest.mark.parametrize("follow_up_msg,expected_date_from,expected_date_to", [
    ("What about July 2026?", "2026-07-01", "2026-07-31"),
    ("and July?", "2026-07-01", "2026-07-31"),
    ("July 2026", "2026-07-01", "2026-07-31"),
])
def test_analytics_elliptical_followup(follow_up_msg, expected_date_from, expected_date_to):
    """Priority 2: short follow-up after analytics → merged slots, routed to collect_parameters."""
    last_slots = {
        "query_type": "total_sales_period",
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
        "status_filter": "submitted",
    }
    state = _analytics_state(
        last_msg=follow_up_msg,
        last_slots=last_slots,
        turn_id=2,
        completed_at=_now_iso(),
    )

    llm_response = {
        "is_followup": True,
        "overrides": {"date_from": expected_date_from, "date_to": expected_date_to},
    }

    with patch("workflow.followup_router.invoke_structured_llm", return_value=llm_response):
        with patch("workflow.followup_router.log_audit_event"):
            result = resolve_followup_node(state)

    assert result["_followup_route"] == "collect_parameters", f"Expected collect_parameters, got {result['_followup_route']}"
    assert result["detected_intent"] == "sales-analytics-report"
    fields = result["collected_fields"]
    assert fields["date_from"] == expected_date_from
    assert fields["date_to"] == expected_date_to
    # Unmodified slots should carry over
    assert fields["query_type"] == "total_sales_period"
    assert fields["status_filter"] == "submitted"


# ============================================================================
# TEST 2: Pending slot clarification → resolved by exact name, case-insensitive, ordinal
# ============================================================================

@pytest.mark.parametrize("user_answer,expected_value,options", [
    ("Standard Selling", "Standard Selling", ["Standard Selling", "Wholesale", "Retail"]),
    ("standard selling", "Standard Selling", ["Standard Selling", "Wholesale", "Retail"]),  # case-insensitive
    ("1", "Standard Selling", ["Standard Selling", "Wholesale", "Retail"]),  # ordinal
    ("the first one", "Standard Selling", ["Standard Selling", "Wholesale", "Retail"]),
    ("option 2", "Wholesale", ["Standard Selling", "Wholesale", "Retail"]),
])
def test_slot_clarification_resolution(user_answer, expected_value, options):
    """Priority 1: pending slot clarification → resolved via exact match / case-insensitive / ordinal."""
    messages = [
        HumanMessage(content="What's the price of ITEM-CHAIR-002?"),
        AIMessage(content="Which price list would you like? Standard Selling, Wholesale, or Retail?"),
        HumanMessage(content=user_answer),
    ]
    state = {
        "messages": messages,
        "detected_intent": "item-lookup",
        "collected_fields": {"item_code": "ITEM-CHAIR-002"},
        "is_workflow_complete": True,
        "pending_confirmation": None,
        "last_turn_context": None,
        "pending_slot_clarification": {
            "origin_skill": "item-lookup",
            "origin_slots": {"item_code": "ITEM-CHAIR-002"},
            "missing_slot": "price_list",
            "question_asked": "Which price list would you like?",
            "options": options,
            "asked_turn_id": 2,
            "attempts": 0,
        },
    }

    with patch("workflow.followup_router.log_audit_event"):
        result = resolve_followup_node(state)

    assert result["_followup_route"] == "collect_parameters", f"Expected collect_parameters, got {result['_followup_route']}"
    assert result["detected_intent"] == "item-lookup"
    assert result["collected_fields"]["price_list"] == expected_value
    assert result["collected_fields"]["item_code"] == "ITEM-CHAIR-002"
    assert result.get("pending_slot_clarification") is None


# ============================================================================
# TEST 3: Pending clarification + unrelated request → clear, fall through
# ============================================================================

def test_slot_clarification_unrelated_request_clears():
    """Priority 1: if user sends something that can't be resolved as a slot answer, abandon clarification."""
    messages = [
        HumanMessage(content="What's the price of ITEM-CHAIR-002?"),
        AIMessage(content="Which price list would you like?"),
        HumanMessage(content="xyzzy_nonexistent_thing_12345"),
    ]
    # No options provided — free value will match any non-empty string
    # So we test the re-ask path by exhausting attempts
    state = {
        "messages": messages,
        "detected_intent": "item-lookup",
        "collected_fields": {"item_code": "ITEM-CHAIR-002"},
        "is_workflow_complete": True,
        "pending_confirmation": None,
        "last_turn_context": None,
        "pending_slot_clarification": {
            "origin_skill": "item-lookup",
            "origin_slots": {"item_code": "ITEM-CHAIR-002"},
            "missing_slot": "price_list",
            "question_asked": "Which price list would you like?",
            "options": ["Standard Selling", "Wholesale"],  # with options, random text won't match
            "asked_turn_id": 2,
            "attempts": MAX_SLOT_CLARIFICATION_ATTEMPTS,  # already exhausted
        },
    }

    with patch("workflow.followup_router.log_audit_event"):
        result = resolve_followup_node(state)

    # Should abandon and fall through to classifier
    assert result["_followup_route"] == "classify_intent"
    assert result.get("pending_slot_clarification") is None


# ============================================================================
# TEST 4: Pending slot clarification bounded re-ask
# ============================================================================

def test_slot_clarification_reasked():
    """Priority 1: unresolved answer that has not yet hit the limit → re-ask."""
    messages = [
        HumanMessage(content="What's the price of ITEM-CHAIR-002?"),
        AIMessage(content="Which price list would you like? Standard Selling or Wholesale?"),
        HumanMessage(content="umm something completely different"),
    ]
    state = {
        "messages": messages,
        "detected_intent": "item-lookup",
        "collected_fields": {"item_code": "ITEM-CHAIR-002"},
        "is_workflow_complete": True,
        "pending_confirmation": None,
        "last_turn_context": None,
        "pending_slot_clarification": {
            "origin_skill": "item-lookup",
            "origin_slots": {"item_code": "ITEM-CHAIR-002"},
            "missing_slot": "price_list",
            "question_asked": "Which price list would you like?",
            "options": ["Standard Selling", "Wholesale"],
            "asked_turn_id": 2,
            "attempts": 0,  # first attempt
        },
    }

    with patch("workflow.followup_router.log_audit_event"):
        result = resolve_followup_node(state)

    # Should re-ask (attempts = 1, below max)
    assert result["_followup_route"] == "format_response"
    assert result.get("pending_slot_clarification", {}).get("attempts") == 1
    assert result.get("is_workflow_complete") is False


# ============================================================================
# TEST 5: Stale context (too many turns) → fall through, do NOT guess
# ============================================================================

def test_stale_context_too_many_turns():
    """Priority 2: last_turn_context is beyond FOLLOWUP_MAX_TURNS → fall through."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="What about July?",
        last_slots=last_slots,
        turn_id=1,  # completed at turn 1
        extra_messages=FOLLOWUP_MAX_TURNS + 1,  # current turn_id will be > turn_id + MAX
    )

    with patch("workflow.followup_router.invoke_structured_llm") as mock_llm:
        result = resolve_followup_node(state)
        mock_llm.assert_not_called()  # LLM must NOT be called for stale context

    assert result["_followup_route"] == "classify_intent"


# ============================================================================
# TEST 6: Stale context (time elapsed) → fall through
# ============================================================================

def test_stale_context_time_elapsed():
    """Priority 2: last_turn_context.completed_at is beyond FOLLOWUP_MAX_MINUTES → fall through."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="What about July?",
        last_slots=last_slots,
        turn_id=2,
        completed_at=_ago_iso(FOLLOWUP_MAX_MINUTES + 5),  # older than the max
    )

    with patch("workflow.followup_router.invoke_structured_llm") as mock_llm:
        result = resolve_followup_node(state)
        mock_llm.assert_not_called()

    assert result["_followup_route"] == "classify_intent"


# ============================================================================
# TEST 7: pending_confirmation active → fall through immediately (no follow-up)
# ============================================================================

def test_pending_confirmation_falls_through():
    """Guard: pending_confirmation present → always fall through to classifier."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="yes",
        last_slots=last_slots,
        turn_id=2,
    )
    state["pending_confirmation"] = {
        "operation": "cancel",
        "doctype": "Sales Order",
        "target_name": "SAL-ORD-2026-00091",
        "scope_description": "Are you sure you want to cancel Sales Order 'SAL-ORD-2026-00091'?",
        "requested_at": 2,
    }

    with patch("workflow.followup_router.invoke_structured_llm") as mock_llm:
        result = resolve_followup_node(state)
        mock_llm.assert_not_called()

    assert result["_followup_route"] == "classify_intent"


# ============================================================================
# TEST 8: LLM says not a follow-up → fall through
# ============================================================================

def test_llm_says_not_followup():
    """Priority 2: LLM returns is_followup=False → fall through to classifier."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="Show me all draft purchase orders",
        last_slots=last_slots,
        turn_id=2,
    )

    llm_response = {"is_followup": False, "overrides": {}}

    with patch("workflow.followup_router.invoke_structured_llm", return_value=llm_response):
        with patch("workflow.followup_router.log_audit_event"):
            result = resolve_followup_node(state)

    assert result["_followup_route"] == "classify_intent"


# ============================================================================
# TEST 9: LLM unavailable for follow-up extraction → fall through, log audit
# ============================================================================

def test_llm_unavailable_falls_through():
    """Priority 2: LLM returns None (unavailable) → Priority 3 fallthrough + audit log."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="What about July?",
        last_slots=last_slots,
        turn_id=2,
    )

    with patch("workflow.followup_router.invoke_structured_llm", return_value=None):
        with patch("workflow.followup_router.log_audit_event") as mock_audit:
            result = resolve_followup_node(state)
            mock_audit.assert_called_once()
            call_kwargs = mock_audit.call_args
            assert call_kwargs[1]["outcome"] == "failed" or (call_kwargs[0] and "failed" in str(call_kwargs))

    assert result["_followup_route"] == "classify_intent"


# ============================================================================
# TEST 10: Separate state dicts → no state leakage
# ============================================================================

def test_no_state_leakage_between_sessions():
    """Two independent states must not share any state."""
    last_slots_a = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    last_slots_b = {"query_type": "top_customer_value"}

    state_a = _analytics_state(last_msg="What about July?", last_slots=last_slots_a, turn_id=2)
    state_b = _analytics_state(last_msg="What about July?", last_slots=last_slots_b, turn_id=2)
    state_b["last_turn_context"]["skill"] = "sales-analytics-report"
    state_b["last_turn_context"]["follow_up_slots"] = ["date_from", "date_to", "query_type", "limit"]

    llm_a = {"is_followup": True, "overrides": {"date_from": "2026-07-01", "date_to": "2026-07-31"}}
    llm_b = {"is_followup": True, "overrides": {"date_from": "2026-06-01", "date_to": "2026-06-30"}}

    with patch("workflow.followup_router.invoke_structured_llm", return_value=llm_a):
        with patch("workflow.followup_router.log_audit_event"):
            result_a = resolve_followup_node(state_a)

    with patch("workflow.followup_router.invoke_structured_llm", return_value=llm_b):
        with patch("workflow.followup_router.log_audit_event"):
            result_b = resolve_followup_node(state_b)

    # Both should be resolved with their respective overrides
    assert result_a["_followup_route"] == "collect_parameters"
    assert result_b["_followup_route"] == "collect_parameters"

    # No cross-contamination
    assert result_a["collected_fields"]["date_from"] == "2026-07-01"
    assert result_b["collected_fields"]["date_from"] == "2026-06-01"
    assert result_a["collected_fields"]["date_from"] != result_b["collected_fields"]["date_from"]

    # State dicts themselves must not have been mutated
    assert state_a["last_turn_context"]["slots"] == last_slots_a
    assert state_b["last_turn_context"]["slots"] == last_slots_b


# ============================================================================
# TEST 11: Ambiguous follow-up (LLM says is_followup=True but no overrides)
#          → targeted clarification question, NOT capability menu
# ============================================================================

def test_ambiguous_followup_asks_targeted_question():
    """Priority 2: LLM says follow-up but extracts no overrides → targeted question, not menu."""
    last_slots = {"query_type": "total_sales_period", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    state = _analytics_state(
        last_msg="can you redo that?",
        last_slots=last_slots,
        turn_id=2,
    )

    llm_response = {"is_followup": True, "overrides": {}}  # no overrides

    with patch("workflow.followup_router.invoke_structured_llm", return_value=llm_response):
        with patch("workflow.followup_router.log_audit_event"):
            result = resolve_followup_node(state)

    assert result["_followup_route"] == "format_response"
    assert result.get("is_workflow_complete") is False
    # Should NOT return the capability menu
    response = result.get("final_response", "")
    assert "Currently, I can help you with" not in response
    # Should ask a targeted question about the previous skill
    assert "sales-analytics-report" in response or "follow-up" in response.lower()
    # Should have set pending_slot_clarification
    psc = result.get("pending_slot_clarification")
    assert psc is not None
    assert psc["origin_skill"] == "sales-analytics-report"


# ============================================================================
# TEST 12: route_after_followup_router maps _followup_route correctly
# ============================================================================

def test_route_after_followup_router():
    """Edge function returns the _followup_route value from state."""
    for route in ("classify_intent", "collect_parameters", "format_response"):
        state = {"_followup_route": route}
        assert route_after_followup_router(state) == route

    # Default when key missing
    assert route_after_followup_router({}) == "classify_intent"


# ============================================================================
# TEST 13: build_last_turn_context helper correctness
# ============================================================================

def test_build_last_turn_context():
    """build_last_turn_context produces correct schema."""
    ctx = build_last_turn_context(
        skill_name="sales-analytics-report",
        collected_fields={"date_from": "2026-08-01", "query_type": "total_sales_period"},
        turn_id=4,
        follow_up_slots=["date_from", "date_to", "query_type"],
    )
    assert ctx["skill"] == "sales-analytics-report"
    assert ctx["slots"]["date_from"] == "2026-08-01"
    assert ctx["turn_id"] == 4
    assert "completed_at" in ctx
    assert ctx["follow_up_slots"] == ["date_from", "date_to", "query_type"]
    # Verify completed_at is a parseable ISO timestamp
    dt = datetime.fromisoformat(ctx["completed_at"])
    assert dt.tzinfo is not None


# ============================================================================
# TEST 14: No last_turn_context and no pending_slot_clarification → Priority 3
# ============================================================================

def test_no_context_falls_through():
    """Priority 3: no prior context at all → always fall through."""
    state = {
        "messages": [HumanMessage(content="What about July?")],
        "detected_intent": "",
        "collected_fields": {},
        "is_workflow_complete": False,
        "pending_confirmation": None,
        "pending_slot_clarification": None,
        "last_turn_context": None,
    }

    with patch("workflow.followup_router.invoke_structured_llm") as mock_llm:
        result = resolve_followup_node(state)
        mock_llm.assert_not_called()

    assert result["_followup_route"] == "classify_intent"
