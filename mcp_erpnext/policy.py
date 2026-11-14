"""
Shared security policy for MCP ERPNext operations.

This module provides pure, isolated authorization and input validation functions.
It has zero upward dependencies on agent, tools, workflow, ai_assistant, main, or dotenv.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple

from mcp_erpnext.auth import RequestContext
from mcp_erpnext.crud import (
    IDENTIFIER_PATTERN,
    ORDER_BY_PATTERN,
    get_allowed_doctypes,
)
from mcp_erpnext.reports import get_report_allowlist


@dataclass(frozen=True)
class PolicyDecision:
    """
    Result of a policy authorization or validation check.

    Attributes:
        allowed: True if the request is permitted, False otherwise.
        reason: Detailed explanation suitable for audit logs and system debugging.
        public_message: Safe, sanitized message for external callers/clients.
        spec: Optional dictionary (e.g. report allowlist entry) attached when allowed.
    """

    allowed: bool
    reason: str
    public_message: str
    spec: Optional[Dict[str, Any]] = None

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def allow(
        cls,
        spec: Optional[Dict[str, Any]] = None,
        reason: str = "Access allowed.",
        public_message: str = "",
    ) -> "PolicyDecision":
        return cls(allowed=True, reason=reason, public_message=public_message, spec=spec)

    @classmethod
    def deny(
        cls,
        reason: str,
        public_message: str,
    ) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, public_message=public_message, spec=None)


def denied_envelope(decision: PolicyDecision) -> Dict[str, Any]:
    """
    Format standard 5-key permission_denied response matching reports._denied().
    """
    return {
        "status": "permission_denied",
        "data": None,
        "error": decision.public_message,
        "has_more": False,
        "success": False,
    }


# Canonical RequestContext is imported from mcp_erpnext.auth and re-exported here
# for backward compatibility with policy callers.


READ_TOOLS: FrozenSet[str] = frozenset({
    "get_list",
    "get_document",
    "search_document",
    "get_count",
    "aggregate",
    "run_report",
})

WRITE_TOOLS: FrozenSet[str] = frozenset({
    "create_document",
    "update_document",
    "delete_document",
    "cancel_document",
    "submit_document",
})

ALL_TOOLS: FrozenSet[str] = READ_TOOLS | WRITE_TOOLS

RESERVED_IDENTITY_KEYS: FrozenSet[str] = frozenset({
    "user_roles",
    "roles",
    "user_id",
    "read_only",
    "client",
    "credential",
    "credentials",
    "request_id",
})


def check_tool_allowed(ctx: RequestContext, tool_name: Any) -> PolicyDecision:
    """
    Check if a tool is recognized and whether write tools are permitted in the context.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return PolicyDecision.deny(
            reason=f"Tool name must be a non-empty string, got {type(tool_name).__name__}.",
            public_message="Invalid tool request.",
        )

    if tool_name not in ALL_TOOLS:
        return PolicyDecision.deny(
            reason=f"Tool '{tool_name}' is not recognized in allowed tool registry.",
            public_message=f"Tool '{tool_name}' is not available.",
        )

    if tool_name in WRITE_TOOLS and ctx.read_only:
        return PolicyDecision.deny(
            reason=f"Write tool '{tool_name}' is denied because request context is read_only=True.",
            public_message=f"Tool '{tool_name}' is not permitted in read-only mode.",
        )

    return PolicyDecision.allow(reason=f"Tool '{tool_name}' is permitted.")


def check_doctype_allowed(doctype: Any) -> PolicyDecision:
    """
    Case-insensitive check if a DocType is allowed based on crud whitelist.
    """
    if not doctype or not isinstance(doctype, str):
        return PolicyDecision.deny(
            reason=f"DocType must be a non-empty string, got {type(doctype).__name__}.",
            public_message="DocType is not allowed.",
        )

    cleaned = doctype.strip()
    if not cleaned:
        return PolicyDecision.deny(
            reason="DocType cannot be blank or whitespace-only.",
            public_message="DocType is not allowed.",
        )

    allowed = {d.lower() for d in get_allowed_doctypes()}
    if cleaned.lower() in allowed:
        return PolicyDecision.allow(reason=f"DocType '{cleaned}' is allowed.")

    return PolicyDecision.deny(
        reason=f"DocType '{cleaned}' is not in the allowed doctype list.",
        public_message=f"DocType '{cleaned}' is not allowed.",
    )


def validate_order_by(order_by: Any) -> PolicyDecision:
    """
    Validate order_by clause using strict fullmatch against ORDER_BY_PATTERN.
    None represents 'not provided' and is allowed.
    """
    if order_by is None:
        return PolicyDecision.allow(reason="order_by is not provided.")

    if type(order_by) is bool or not isinstance(order_by, str) or not order_by:
        return PolicyDecision.deny(
            reason=f"order_by must be a non-empty string or None, got {type(order_by).__name__}.",
            public_message="Invalid order_by clause.",
        )

    if "\n" in order_by or "\r" in order_by:
        return PolicyDecision.deny(
            reason="Invalid order_by clause: trailing or embedded newlines are not permitted.",
            public_message="Invalid order_by clause.",
        )

    if ORDER_BY_PATTERN.fullmatch(order_by):
        return PolicyDecision.allow(reason=f"order_by clause '{order_by}' is valid.")

    return PolicyDecision.deny(
        reason=f"Invalid order_by clause '{order_by}'.",
        public_message="Invalid order_by clause.",
    )


def validate_identifier(name: Any, label: str = "Identifier") -> PolicyDecision:
    """
    Validate SQL identifier using strict fullmatch against IDENTIFIER_PATTERN.
    """
    if type(name) is bool or not isinstance(name, str) or not name:
        return PolicyDecision.deny(
            reason=f"{label} must be a non-empty string, got {type(name).__name__}.",
            public_message=f"Invalid {label}.",
        )

    if IDENTIFIER_PATTERN.fullmatch(name):
        return PolicyDecision.allow(reason=f"{label} '{name}' is valid.")

    return PolicyDecision.deny(
        reason=f"Invalid {label} '{name}'. Must match {IDENTIFIER_PATTERN.pattern}.",
        public_message=f"Invalid {label}.",
    )


def validate_sort(sort: Any, allowed_fields: Optional[Any] = None) -> PolicyDecision:
    """
    Validate sort parameter for aggregate and report queries.
    Syntax: '<field_name> [asc|desc]'.
    None represents 'not provided' and is allowed.
    """
    if sort is None:
        return PolicyDecision.allow(reason="sort is not provided.")

    if type(sort) is bool or not isinstance(sort, str) or not sort.strip():
        return PolicyDecision.deny(
            reason=f"sort must be a non-empty string or None, got {type(sort).__name__}.",
            public_message="Invalid sort parameter.",
        )

    if "\n" in sort or "\r" in sort:
        return PolicyDecision.deny(
            reason="sort parameter cannot contain newlines.",
            public_message="Invalid sort parameter.",
        )

    parts = sort.strip().split()
    if len(parts) not in (1, 2):
        return PolicyDecision.deny(
            reason=f"Invalid sort format '{sort}'. Expected '<field> [asc|desc]'.",
            public_message="Invalid sort parameter.",
        )

    field_name = parts[0]
    if not IDENTIFIER_PATTERN.fullmatch(field_name):
        return PolicyDecision.deny(
            reason=f"Invalid sort field identifier '{field_name}'. Must match {IDENTIFIER_PATTERN.pattern}.",
            public_message="Invalid sort parameter.",
        )

    if allowed_fields is not None:
        allowed_set = {f for f in allowed_fields if isinstance(f, str) and f}
        if allowed_set and field_name not in allowed_set:
            return PolicyDecision.deny(
                reason=f"Sort field '{field_name}' is not in allowed fields: {sorted(allowed_set)}.",
                public_message="Invalid sort parameter.",
            )

    if len(parts) == 2:
        direction = parts[1].lower()
        if direction not in ("asc", "desc"):
            return PolicyDecision.deny(
                reason=f"Invalid sort direction '{parts[1]}'. Must be 'asc' or 'desc'.",
                public_message="Invalid sort parameter.",
            )

    return PolicyDecision.allow(reason=f"Sort parameter '{sort}' is valid.")


def check_role_access(user_roles: Any, allowed_roles: Any) -> PolicyDecision:
    """
    Strict role check.
    Fails closed: empty, None, bare string/bytes, or missing allowed_roles denies.
    Role matching is exact and case-sensitive.
    """
    if user_roles is None or isinstance(user_roles, (str, bytes)):
        return PolicyDecision.deny(
            reason=f"user_roles must be an iterable of strings (not str/bytes), got {type(user_roles).__name__}.",
            public_message="Permission denied.",
        )

    if not isinstance(user_roles, (list, tuple, set, frozenset)):
        return PolicyDecision.deny(
            reason=f"user_roles must be a list, tuple, set, or frozenset, got {type(user_roles).__name__}.",
            public_message="Permission denied.",
        )

    if allowed_roles is None or isinstance(allowed_roles, (str, bytes)):
        return PolicyDecision.deny(
            reason="Action has no valid allowed_roles declared.",
            public_message="Permission denied.",
        )

    if not isinstance(allowed_roles, (list, tuple, set, frozenset)):
        return PolicyDecision.deny(
            reason=f"allowed_roles must be an iterable collection, got {type(allowed_roles).__name__}.",
            public_message="Permission denied.",
        )

    clean_allowed = {r for r in allowed_roles if isinstance(r, str) and r}
    if not clean_allowed:
        return PolicyDecision.deny(
            reason="Action requires declared roles, but none are configured.",
            public_message="Permission denied.",
        )

    clean_user = {r for r in user_roles if isinstance(r, str) and r}
    matching = clean_user & clean_allowed
    if matching:
        return PolicyDecision.allow(
            reason=f"Role check passed with matching role(s): {', '.join(sorted(matching))}."
        )

    reason = (
        f"Your role(s) ({', '.join(sorted(clean_user)) or 'none'}) do not include any of the "
        f"required roles ({', '.join(sorted(clean_allowed))}) for this action."
    )
    return PolicyDecision.deny(reason=reason, public_message="Permission denied.")


def check_report_access(ctx: RequestContext, report_name: Any) -> PolicyDecision:
    """
    Check authorization to run a named report.
    Returns identical public_message for unknown, unlisted, and role-denied reports.
    """
    denial_msg = f"Report '{report_name}' is not available."

    if not isinstance(report_name, str) or not report_name:
        return PolicyDecision.deny(
            reason=f"Report name must be a non-empty string, got {type(report_name).__name__}.",
            public_message=denial_msg,
        )

    allowlist = get_report_allowlist()
    spec = allowlist.get(report_name)
    if not spec or not isinstance(spec, dict):
        return PolicyDecision.deny(
            reason=f"Report '{report_name}' is not in the report allowlist.",
            public_message=denial_msg,
        )

    raw_allowed = spec.get("allowed_roles")
    if not isinstance(raw_allowed, (list, tuple, set, frozenset)) or not raw_allowed:
        return PolicyDecision.deny(
            reason=f"Report '{report_name}' has no allowed roles configured.",
            public_message=denial_msg,
        )

    clean_allowed = {r for r in raw_allowed if isinstance(r, str) and r}
    if not clean_allowed:
        return PolicyDecision.deny(
            reason=f"Report '{report_name}' has no valid allowed roles configured.",
            public_message=denial_msg,
        )

    clean_user = {r for r in ctx.roles if isinstance(r, str) and r}
    if not (clean_user & clean_allowed):
        return PolicyDecision.deny(
            reason=(
                f"User roles ({', '.join(sorted(clean_user)) or 'none'}) do not include "
                f"any required roles ({', '.join(sorted(clean_allowed))}) for report '{report_name}'."
            ),
            public_message=denial_msg,
        )

    return PolicyDecision.allow(
        spec=spec,
        reason=f"Access granted for report '{report_name}'.",
        public_message="",
    )


def authorize_tool_call(
    ctx: RequestContext,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> PolicyDecision:
    """
    Single entry point for authorizing MCP tool calls.

    Order of checks:
    1. Deny if arguments contains reserved identity keys.
    2. Check tool exists and adheres to read_only mode.
    3. Check doctype (and parent_doctype if present) for tools that operate on doctypes.
    4. Validate order_by clause if present.
    5. Validate identifier for group_by and metric if present.
    6. Validate sort clause if present.
    7. Check report access if tool_name == 'run_report'.
    """
    args = arguments if arguments is not None else {}
    if not isinstance(args, dict):
        return PolicyDecision.deny(
            reason=f"Arguments must be a dictionary or None, got {type(args).__name__}.",
            public_message="Invalid request parameters.",
        )

    # 1. Deny if arguments contains any reserved identity key
    found_reserved = RESERVED_IDENTITY_KEYS.intersection(args.keys())
    if found_reserved:
        return PolicyDecision.deny(
            reason=f"Reserved identity parameter(s) passed in arguments: {sorted(found_reserved)}.",
            public_message="Invalid request parameters: reserved parameter provided.",
        )

    # 2. check_tool_allowed
    tool_dec = check_tool_allowed(ctx, tool_name)
    if not tool_dec:
        return tool_dec

    # 3. For tools that take a doctype, require it and check allowed
    if tool_name != "run_report":
        doctype = args.get("doctype")
        if doctype is None:
            return PolicyDecision.deny(
                reason=f"Tool '{tool_name}' requires 'doctype' argument.",
                public_message="Missing required parameter: doctype.",
            )
        dt_dec = check_doctype_allowed(doctype)
        if not dt_dec:
            return dt_dec

        if "parent_doctype" in args:
            parent_dec = check_doctype_allowed(args.get("parent_doctype"))
            if not parent_dec:
                return parent_dec

    # 4. validate_order_by if present
    if "order_by" in args:
        ob_dec = validate_order_by(args.get("order_by"))
        if not ob_dec:
            return ob_dec

    # 5. validate_identifier for group_by and metric if present
    if "group_by" in args:
        gb_dec = validate_identifier(args.get("group_by"), label="group_by")
        if not gb_dec:
            return gb_dec

    if "metric" in args:
        m_dec = validate_identifier(args.get("metric"), label="metric")
        if not m_dec:
            return m_dec

    # 6. validate sort if present
    if "sort" in args:
        sort_val = args.get("sort")
        if tool_name == "aggregate":
            gb = args.get("group_by")
            m = args.get("metric")
            allowed = {f for f in (gb, m) if isinstance(f, str) and f}
            sort_dec = validate_sort(sort_val, allowed_fields=allowed if allowed else None)
        else:
            sort_dec = validate_sort(sort_val)
        if not sort_dec:
            return sort_dec

    # 7. For run_report, check_report_access
    if tool_name == "run_report":
        report_name = args.get("report_name")
        rep_dec = check_report_access(ctx, report_name)
        if not rep_dec:
            return rep_dec
        return rep_dec

    return PolicyDecision.allow(reason=f"Tool call '{tool_name}' authorized.")
