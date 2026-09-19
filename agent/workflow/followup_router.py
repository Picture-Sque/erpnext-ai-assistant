"""
followup_router.py — Deterministic Follow-Up Context Router

This module implements the pre-classifier node that runs BEFORE classify_intent_node
on every turn. It resolves two classes of context-dependent short messages that the
general LLM classifier handles unreliably:

  Priority 1: Pending slot clarification answers
      e.g. "Standard Selling" after "Which price list would you like?"
  Priority 2: Elliptical follow-ups on a completed read/analytics turn
      e.g. "What about July?" after "Total sales in August 2026"
  Priority 3: Fall-through to the existing classify_intent_node (unchanged)

Hard constraints:
  - Resumed requests re-enter the pipeline at collect_parameters, traversing
    the full RBAC -> preconditions -> confirmation -> tool chain. No shortcuts.
  - Write intents are never stored in last_turn_context and are never resumed here.
  - pending_confirmation active => immediate fall-through to classifier (no follow-up).
  - LLM is only used for slot extraction (schema-validated). Routing is deterministic.
  - LLM failure => Priority 3 (new request). Fail loudly via audit log. No silent fallback.
  - Audit log events: follow_up_resolved, clarification_slot_resolved,
    clarification_slot_abandoned — each include origin_turn_id.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from audit_logger import log_audit_event
from llm import invoke_structured_llm
from workflow.state import AgentState

logger = logging.getLogger("followup_router")

# ============================================================================
# Named Configuration Constants
# ============================================================================

# Maximum number of turns since last_turn_context was recorded for a follow-up
# to still be considered "fresh".
FOLLOWUP_MAX_TURNS: int = 3

# Maximum elapsed wall-clock minutes since last_turn_context.completed_at for
# a follow-up to still be considered "fresh".
FOLLOWUP_MAX_MINUTES: int = 15

# Maximum word count for a message to be treated as a short/elliptical follow-up.
FOLLOWUP_SHORT_MSG_WORDS: int = 12

# Maximum re-ask attempts for a pending slot clarification before escalating.
MAX_SLOT_CLARIFICATION_ATTEMPTS: int = 2

# Ordinal word -> 1-based index map used to resolve "the first one", "option 2", etc.
_ORDINAL_WORDS: Dict[str, int] = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
}


# ============================================================================
# Internal helpers
# ============================================================================

def _parse_ordinal(text: str) -> Optional[int]:
    """
    Try to extract a 1-based ordinal from user text.
    Handles: "1", "option 2", "#3", "the first one", "second", etc.
    Returns None if no ordinal found.
    """
    text_lower = text.strip().lower()

    # Check word ordinals
    for word, idx in _ORDINAL_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
            return idx

    # Numeric: "1", "option 2", "#3", "number 4"
    m = re.search(r"\b(\d+)\b", text_lower)
    if m:
        return int(m.group(1))

    return None


def _is_fresh(last_turn_context: dict, current_turn_id: int) -> bool:
    """
    Return True if last_turn_context is within the configured freshness window
    (both turn count and wall-clock time).
    """
    turns_elapsed = current_turn_id - last_turn_context.get("turn_id", 0)
    if turns_elapsed > FOLLOWUP_MAX_TURNS:
        return False

    completed_at_str = last_turn_context.get("completed_at")
    if completed_at_str:
        try:
            completed_at = datetime.fromisoformat(completed_at_str)
            # Make aware if naive
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            elapsed_minutes = (now - completed_at).total_seconds() / 60
            if elapsed_minutes > FOLLOWUP_MAX_MINUTES:
                return False
        except Exception:
            pass  # Unparseable timestamp — skip time check, trust turn count

    return True


def _resolve_slot_answer(
    reply: str,
    options: Optional[List[str]],
) -> Optional[str]:
    """
    Try to match a user reply to a slot answer.

    Resolution order:
      1. Exact / case-insensitive match to declared options list.
      2. Ordinal reference to options list ("the first one", "option 2", "1").
      3. Valid free-value: only when NO options list was declared (i.e. the slot
         accepts any free-form text). When options ARE present and none matched,
         return None to let the router re-ask or escalate.

    Returns the resolved string value, or None if no match.
    """
    reply_stripped = reply.strip()
    if not reply_stripped:
        return None

    # Strategy 1: exact/case-insensitive match against offered options
    if options:
        reply_lower = reply_stripped.lower()
        for opt in options:
            if opt.strip().lower() == reply_lower:
                return opt  # Return the canonical option spelling

        # Strategy 2: ordinal reference
        ordinal = _parse_ordinal(reply_stripped)
        if ordinal is not None and 1 <= ordinal <= len(options):
            return options[ordinal - 1]

        # Options were offered but nothing matched — do NOT fall through to free value.
        # The caller will re-ask or escalate.
        return None

    # Strategy 3: free value — only when NO options were declared.
    # Any non-empty string is accepted for unconstrained slots.
    return reply_stripped


def _extract_followup_overrides(
    message: str,
    last_skill_name: str,
    last_slots: dict,
    follow_up_slots: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Call the LLM to extract slot overrides from an elliptical follow-up message.
    Returns a dict of overrides (may be empty) or None if the LLM says this is
    NOT a follow-up or if the call fails.

    The LLM is constrained to a Pydantic-validated JSON schema:
      {"is_followup": bool, "overrides": {slot_name: value, ...}}
    Only slots in `follow_up_slots` are eligible for extraction.
    """
    if not follow_up_slots:
        return None

    # Build context string of previous slots for the LLM
    relevant_prev = {k: v for k, v in last_slots.items() if k in follow_up_slots}

    prompt = (
        f"You are a slot-override extractor for ERPNext AI Assistant.\n"
        f"The user just completed a '{last_skill_name}' query with these parameters:\n"
        f"  {relevant_prev}\n\n"
        f"The user sent a SHORT follow-up message: '{message}'\n\n"
        f"Eligible override slots for this skill: {follow_up_slots}\n\n"
        f"Task:\n"
        f"1. Decide if this message is a follow-up to the previous '{last_skill_name}' query "
        f"(is_followup=true) or an entirely new/unrelated request (is_followup=false).\n"
        f"2. If it IS a follow-up, extract only the slots that the user is CHANGING relative to "
        f"the previous query. Do not include slots the user did not mention.\n"
        f"   - Date references: 'July' means date_from=2026-07-01, date_to=2026-07-31 "
        f"(inherit year from previous slots if not stated).\n"
        f"   - 'last month', 'previous month' etc.: compute relative to today.\n"
        f"3. If is_followup=true but no clear overrides can be extracted, set overrides={{}} "
        f"(empty dict) — do NOT guess.\n\n"
        f"Respond ONLY with a JSON object:\n"
        f'  {{"is_followup": true/false, "overrides": {{"slot_name": "value", ...}}}}'
    )

    result = invoke_structured_llm(prompt)
    if result is None:
        logger.warning("[FollowupRouter] LLM returned None for override extraction — treating as new request")
        log_audit_event(
            operation="followup_slot_extraction",
            doctype="unknown",
            target_name="unknown",
            outcome="failed",
            failure_classification="llm_unavailable",
            details="LLM unavailable during follow-up slot extraction"
        )
        return None

    # Validate schema
    if not isinstance(result, dict):
        logger.warning(f"[FollowupRouter] LLM returned non-dict: {result!r} — treating as new request")
        return None

    is_followup = result.get("is_followup")
    if not isinstance(is_followup, bool):
        # Try coerce
        is_followup = str(is_followup).lower() in ("true", "1", "yes")

    if not is_followup:
        logger.info("[FollowupRouter] LLM classified message as NOT a follow-up")
        return None

    overrides = result.get("overrides")
    if overrides is None or not isinstance(overrides, dict):
        overrides = {}

    # Filter to only eligible slots
    overrides = {k: v for k, v in overrides.items() if k in follow_up_slots and v is not None}
    return overrides


# ============================================================================
# Pre-Classifier Node
# ============================================================================

def resolve_followup_node(state: AgentState) -> dict:
    """
    Pre-classifier node — runs BEFORE classify_intent_node on every turn.

    Returns a state patch dict. Key outputs:

      _followup_route: str — routing decision for route_after_followup_router:
          "classify_intent"   — fall through (Priority 3)
          "collect_parameters" — resumed skill, skip classifier (Priority 1 or 2)
          "format_response"   — asking targeted clarification (Priority 2 ambiguous)

    Additional state keys set when resuming:
      detected_intent, collected_fields, last_turn_context,
      pending_slot_clarification, final_response, is_workflow_complete
    """
    messages = state.get("messages", [])
    current_turn_id = len(messages)

    pending_slot_clarification = state.get("pending_slot_clarification")
    last_turn_context = state.get("last_turn_context")
    pending_confirmation = state.get("pending_confirmation")

    # Guard: if a write confirmation is pending, always fall through.
    # The confirmation gate handles the reply, not this router.
    if pending_confirmation:
        logger.info("[FollowupRouter] pending_confirmation present — deferring to classifier/confirmation gate")
        return {"_followup_route": "classify_intent"}

    last_msg = messages[-1].content.strip() if messages else ""
    if not last_msg:
        return {"_followup_route": "classify_intent"}

    # =========================================================================
    # PRIORITY 1: Pending slot clarification
    # =========================================================================
    if pending_slot_clarification:
        origin_skill = pending_slot_clarification.get("origin_skill", "")
        origin_slots = dict(pending_slot_clarification.get("origin_slots", {}))
        missing_slot = pending_slot_clarification.get("missing_slot", "")
        options = pending_slot_clarification.get("options")
        asked_turn_id = pending_slot_clarification.get("asked_turn_id", 0)
        attempts = pending_slot_clarification.get("attempts", 0)

        resolved_value = _resolve_slot_answer(last_msg, options)

        if resolved_value is not None:
            logger.info(
                f"[FollowupRouter] Priority 1: slot clarification resolved — "
                f"slot='{missing_slot}', value='{resolved_value}', origin_skill='{origin_skill}'"
            )
            log_audit_event(
                operation="clarification_slot_resolved",
                doctype=origin_skill,
                target_name=missing_slot,
                outcome="resolved",
                details=f"value='{resolved_value}', origin_turn_id={asked_turn_id}"
            )
            merged_slots = dict(origin_slots)
            merged_slots[missing_slot] = resolved_value
            return {
                "_followup_route": "collect_parameters",
                "detected_intent": origin_skill,
                "collected_fields": merged_slots,
                "pending_slot_clarification": None,
                "is_workflow_complete": False,
            }
        else:
            # Not resolved
            attempts += 1
            if attempts >= MAX_SLOT_CLARIFICATION_ATTEMPTS:
                logger.warning(
                    f"[FollowupRouter] Priority 1: slot clarification exhausted after "
                    f"{attempts} attempts for slot='{missing_slot}' — abandoning, treating as new request"
                )
                log_audit_event(
                    operation="clarification_slot_abandoned",
                    doctype=origin_skill,
                    target_name=missing_slot,
                    outcome="escalated",
                    failure_classification="clarification_exhausted",
                    details=f"Gave up after {attempts} attempts. origin_turn_id={asked_turn_id}"
                )
                return {
                    "_followup_route": "classify_intent",
                    "pending_slot_clarification": None,
                }

            # Re-ask
            question_asked = pending_slot_clarification.get("question_asked", f"Could you clarify the '{missing_slot}'?")
            updated_clarification = dict(pending_slot_clarification)
            updated_clarification["attempts"] = attempts
            options_str = ""
            if options:
                options_str = " Options: " + ", ".join(f"'{o}'" for o in options) + "."
            re_ask_msg = (
                f"I'm sorry, I didn't quite catch that. {question_asked}{options_str} "
                f"(Attempt {attempts} of {MAX_SLOT_CLARIFICATION_ATTEMPTS})"
            )
            logger.warning(
                f"[FollowupRouter] Priority 1: re-asking slot clarification "
                f"(attempt {attempts}) for slot='{missing_slot}'"
            )
            return {
                "_followup_route": "format_response",
                "pending_slot_clarification": updated_clarification,
                "final_response": re_ask_msg,
                "is_workflow_complete": False,
            }

    # =========================================================================
    # PRIORITY 2: Elliptical follow-up on a fresh last_turn_context
    # =========================================================================
    if last_turn_context:
        # Freshness check
        if not _is_fresh(last_turn_context, current_turn_id):
            logger.info(
                f"[FollowupRouter] Priority 2: last_turn_context is stale "
                f"(turn_id={last_turn_context.get('turn_id')}, now={current_turn_id}) — falling through"
            )
            return {"_followup_route": "classify_intent"}

        # Message must be short to qualify as elliptical
        word_count = len(last_msg.split())
        if word_count > FOLLOWUP_SHORT_MSG_WORDS:
            logger.info(
                f"[FollowupRouter] Priority 2: message too long ({word_count} words > {FOLLOWUP_SHORT_MSG_WORDS}) — falling through"
            )
            return {"_followup_route": "classify_intent"}

        skill_name = last_turn_context.get("skill", "")
        last_slots = dict(last_turn_context.get("slots", {}))
        follow_up_slots = last_turn_context.get("follow_up_slots", [])
        asked_turn_id = last_turn_context.get("turn_id", 0)

        if not follow_up_slots:
            logger.info(f"[FollowupRouter] Priority 2: skill '{skill_name}' has no follow_up_slots — falling through")
            return {"_followup_route": "classify_intent"}

        # LLM extraction (schema-validated)
        overrides = _extract_followup_overrides(last_msg, skill_name, last_slots, follow_up_slots)

        if overrides is None:
            # LLM said not a follow-up, or failed
            return {"_followup_route": "classify_intent"}

        if overrides:
            # Merge overrides onto previous slots (new wins)
            merged_slots = dict(last_slots)
            merged_slots.update(overrides)
            logger.info(
                f"[FollowupRouter] Priority 2: resolved follow-up — "
                f"skill='{skill_name}', overrides={overrides}"
            )
            log_audit_event(
                operation="follow_up_resolved",
                doctype=skill_name,
                target_name="follow_up",
                outcome="resolved",
                details=f"overrides={overrides}, origin_turn_id={asked_turn_id}"
            )
            return {
                "_followup_route": "collect_parameters",
                "detected_intent": skill_name,
                "collected_fields": merged_slots,
                "is_workflow_complete": False,
            }
        else:
            # LLM said IS a follow-up, but extracted no overrides — ambiguous
            # Ask one targeted clarification (fixed template, not capability menu)
            question = (
                f"I think you're asking a follow-up to your previous '{skill_name}' query. "
                f"What would you like to change? (e.g. {', '.join(follow_up_slots[:3])})"
            )
            logger.info(
                f"[FollowupRouter] Priority 2: follow-up detected but no overrides extracted — "
                f"asking targeted clarification for skill='{skill_name}'"
            )
            log_audit_event(
                operation="follow_up_resolved",
                doctype=skill_name,
                target_name="follow_up_ambiguous",
                outcome="pending",
                details=f"No overrides extracted. origin_turn_id={asked_turn_id}"
            )
            return {
                "_followup_route": "format_response",
                "pending_slot_clarification": {
                    "origin_skill": skill_name,
                    "origin_slots": last_slots,
                    "missing_slot": follow_up_slots[0],  # most likely needed override
                    "question_asked": question,
                    "options": None,
                    "asked_turn_id": current_turn_id,
                    "attempts": 0,
                },
                "final_response": question,
                "is_workflow_complete": False,
            }

    # =========================================================================
    # PRIORITY 3: Fall through to existing classifier
    # =========================================================================
    return {"_followup_route": "classify_intent"}


# ============================================================================
# Router Function (conditional edge)
# ============================================================================

def route_after_followup_router(state: AgentState) -> str:
    """
    Conditional edge after resolve_followup_node.

    Returns one of:
      "classify_intent"    — Priority 3 fall-through (or pending_confirmation guard)
      "collect_parameters" — Priority 1 or 2 resolved; skip classifier entirely
      "format_response"    — Priority 1 re-ask or Priority 2 targeted clarification
    """
    return state.get("_followup_route", "classify_intent")


# ============================================================================
# Helper called by format_agent_message_node to record last_turn_context
# ============================================================================

def build_last_turn_context(
    skill_name: str,
    collected_fields: dict,
    turn_id: int,
    follow_up_slots: List[str],
) -> dict:
    """
    Build a last_turn_context dict for storage after a successful read-skill turn.
    Only call this for non-write skills.
    """
    return {
        "skill": skill_name,
        "slots": dict(collected_fields),
        "turn_id": turn_id,
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "follow_up_slots": list(follow_up_slots),
    }
