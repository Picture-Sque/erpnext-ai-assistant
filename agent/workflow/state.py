from typing import Annotated, TypedDict, Optional
from langgraph.graph.message import add_messages

class AgentState(TypedDict, total=False):
    """
    Defines the state structure for our LangGraph conversation flow.
    Supports the 9-step reasoning pipeline:
      1 User Request
      2 Intent/Planning
      3 Entity Resolution + Read RBAC
      4 Result Handling
      5 Write RBAC
      6 Confirmation
      7 Skill Execution
      8 Result Validation
      9 Final Response
    """
    # =========================================================================
    # Existing Core Conversation & Execution State
    # =========================================================================
    
    # Step 1 User Request / Step 9 Final Response:
    # messages: Accumulates conversation history (messages list).
    # Uses add_messages reducer so append operations merge correctly.
    messages: Annotated[list, add_messages]
    
    # Step 2 Intent/Planning:
    # detected_intent: Tracks what intent the LLM has classified for the user's input.
    detected_intent: str
    
    # Step 2 Intent/Planning / Step 3 Entity Resolution:
    # collected_fields: A dictionary representing slots/entity fields extracted so far.
    collected_fields: dict
    
    # Step 9 Final Response:
    # final_response: The compiled response message that will be sent back to the client.
    final_response: str
    
    # Step 3 Entity Resolution + Read RBAC / Step 5 Write RBAC:
    # user_roles: Stores the list of roles for the authenticated user from the JWT payload.
    user_roles: list[str]
    
    # Step 7 Skill Execution / Step 8 Result Validation / Step 9 Final Response:
    # Workflow completion and tool execution state
    is_workflow_complete: bool
    all_required_filled: bool
    missing_parameters: list
    tool_raw_response: dict
    target_tool: str
    target_doctype: str

    # =========================================================================
    # New Scaffolding Fields for 9-Step Pipeline Upgrades
    # =========================================================================

    # -------------------------------------------------------------------------
    # 1. RBAC Tracking (Split Read vs. Write Access)
    # -------------------------------------------------------------------------
    
    # Step 3 Entity Resolution + Read RBAC:
    # Result of the read-access permission check performed during entity resolution.
    # None = not yet evaluated this turn.
    read_rbac_passed: Optional[bool]

    # Step 5 Write RBAC:
    # Result of the write/operation-specific permission check performed before confirmation.
    # None = not yet evaluated, or not applicable (read-only request).
    write_rbac_passed: Optional[bool]

    # Step 5 Write RBAC:
    # The specific operation being checked (e.g. "create", "update", "cancel", "submit", "delete"),
    # so the write RBAC gate knows exactly what operation it is authorizing.
    write_rbac_operation: Optional[str]

    # -------------------------------------------------------------------------
    # 2. Entity Resolution & Disambiguation
    # -------------------------------------------------------------------------
    
    # Step 3 Entity Resolution + Read RBAC:
    # Mapping of entity references to resolved ERPNext records, e.g.
    # {"customer": {"name": "CUST-00042", "matched_on": "Acme Corp", "confidence": "high"}}. Default empty dict.
    resolved_entities: dict

    # Step 3 Entity Resolution + Read RBAC / Step 4 Result Handling:
    # When entity resolution finds multiple plausible matches, stores the candidate list here
    # (each dict carrying doctype, name/id, and distinguishing display fields) so the disambiguation
    # node can present them to the user. None when no ambiguity exists.
    ambiguous_candidates: Optional[list[dict]]

    # -------------------------------------------------------------------------
    # 3. Clarification Bounding
    # -------------------------------------------------------------------------
    
    # Step 4 Result Handling:
    # Counter for how many clarification round-trips have occurred for the CURRENT unresolved slot/entity
    # in this conversation turn sequence. Default 0. Resettable when a new, different ambiguity arises.
    clarification_attempts: int

    # Step 4 Result Handling:
    # Identifies WHAT is being clarified (e.g. "customer_entity", "delivery_date_field") so the attempts
    # counter is scoped correctly and does not carry over to a different ambiguous field.
    clarification_target: Optional[str]

    # -------------------------------------------------------------------------
    # 4. Confirmation Gate (Destructive / Consequential Operations)
    # -------------------------------------------------------------------------
    
    # Step 6 Confirmation:
    # When a destructive/consequential operation (cancel/delete/submit, or any operation flagged as
    # requiring confirmation) is awaiting user approval, stores:
    # {"operation": str, "doctype": str, "target_name": str, "scope_description": str, "requested_at": <timestamp/turn_id>}.
    # None when nothing is pending.
    pending_confirmation: Optional[dict]

    # Step 6 Confirmation:
    # One of "confirmed", "rejected", None (not yet answered), set once the user responds to a pending confirmation.
    confirmation_result: Optional[str]

    # -------------------------------------------------------------------------
    # 5. Bulk Operation Scope
    # -------------------------------------------------------------------------
    
    # Step 6 Confirmation:
    # For bulk destructive/consequential actions, stores:
    # {"doctype": str, "filters": dict, "affected_count": int, "confirmed": bool}.
    # None for non-bulk operations.
    bulk_operation_scope: Optional[dict]

    # -------------------------------------------------------------------------
    # 6. Precondition / Staleness Tracking
    # -------------------------------------------------------------------------
    
    # Step 5 Write RBAC / Step 6 Confirmation:
    # Whether required-state checks (target exists, correct document status, dependencies satisfied)
    # passed before a write operation. None = not yet checked.
    preconditions_validated: Optional[bool]

    # Step 5 Write RBAC / Step 7 Skill Execution:
    # The most recently read state of the target document (for staleness/concurrency comparison
    # before a consequential update). None if not applicable.
    last_read_snapshot: Optional[dict]

    # -------------------------------------------------------------------------
    # 7. Write Verification & Chained Follow-up Execution
    # -------------------------------------------------------------------------
    
    # Step 8 Result Validation:
    # Whether a post-write re-read confirmed the change actually landed in ERPNext.
    # None = not yet verified, or verification not applicable (no write occurred this turn).
    write_verified: Optional[bool]

    # Step 8 Result Validation / Step 2 Intent/Planning:
    # For multi-step/chained requests, remaining steps to execute after the current one completes
    # and is verified (e.g. ["notify_customer"]). Default empty list. Drives Step 8 -> Step 2 loop-back.
    pending_followup_steps: list[str]

    # -------------------------------------------------------------------------
    # 8. Retry & Escalation
    # -------------------------------------------------------------------------
    
    # Step 4 Result Handling / Step 8 Result Validation:
    # Number of retries attempted for the current failed operation. Default 0.
    # Must reset when a genuinely new operation begins.
    retry_count: int

    # Step 4 Result Handling / Step 8 Result Validation:
    # Classification of failure: one of "empty_result", "permission_denied", "auth_session_failure",
    # "validation_error", "conflict_stale_data", "transient_rate_limit", "tool_system_failure",
    # "unsupported_operation", or None (no failure). Used to decide whether to retry, clarify, stop, or escalate.
    failure_classification: Optional[str]

    # Step 4 Result Handling / Step 8 Result Validation:
    # Whether this conversation/turn has been escalated to human support. Default False.
    escalated: bool

    # Step 4 Result Handling / Step 8 Result Validation:
    # Human-readable reason if escalated (e.g. "repeated permission denial on Purchase Order approval after 2 retries").
    # None if not escalated.
    escalation_reason: Optional[str]

    # -------------------------------------------------------------------------
    # 9. Idempotency (Create-Operation Safety)
    # -------------------------------------------------------------------------
    
    # Step 5 Write RBAC / Step 7 Skill Execution:
    # Whether the agent checked for an existing equivalent record before a create operation,
    # when duplicate creation would be harmful. Default False.
    idempotency_check_performed: bool
