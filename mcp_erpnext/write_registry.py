"""
Write registry: the single source of truth for what the chat agent may write.

Reads are governed by the doctype whitelist in ``crud.py``. Writes are stricter: a
doctype can only be written when it has an entry here, and only for the operations,
roles and fields the entry declares. Anything not declared is denied (fail closed).

This module has no dependency on ``crud`` or ``policy`` so both can import it. It never
loads ``.env`` and never imports from ``agent``.

ERPNext remains the final authority. Its own role permissions and User Permissions are
enforced on every write because requests carry the caller's own session. The registry
exists to fail early, to give clear messages and to limit what a language model can be
talked into asking for.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from mcp_erpnext.auth import RequestContext, get_request_context
from mcp_erpnext.client import is_strict_identity

logger = logging.getLogger("mcp_erpnext.write_registry")

WRITE_OPERATIONS: Tuple[str, ...] = ("create", "update", "submit", "cancel", "delete")

# Tool name -> operation name. Mirrors the five write tools.
TOOL_TO_OPERATION: Dict[str, str] = {
    "create_document": "create",
    "update_document": "update",
    "submit_document": "submit",
    "cancel_document": "cancel",
    "delete_document": "delete",
}

# Fields that are never writable through the chat, whatever a registry entry says.
# They are set by ERPNext itself or would let a caller forge ownership, state or lineage.
RESERVED_WRITE_FIELDS: FrozenSet[str] = frozenset({
    "name",
    "doctype",
    "docstatus",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "idx",
    "parent",
    "parenttype",
    "parentfield",
    "amended_from",
    "__islocal",
    "__unsaved",
    "__run_link_triggers",
})

# Upper bound on documents touched by one confirmed bulk action.
DEFAULT_BULK_MAX_RECORDS = 25

_ADMIN_ROLES = ["System Manager", "Administrator"]


def _roles(*names: str) -> List[str]:
    return list(names) + _ADMIN_ROLES


# The registry. Role lists match the skills in ``skills/`` so behaviour is unchanged by
# the introduction of the registry. New doctypes and operations are added here first.
#
# Per operation:
#   roles         roles allowed to perform it (empty or missing denies everyone)
#   fields        top-level fields a caller may set (create and update only)
#   child_fields  {child table field: [fields allowed on each row]} (create and update only)
# Per doctype:
#   duplicate_keys  fields used to look for a likely duplicate before a create
WRITE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "Sales Order": {
        "duplicate_keys": ["customer"],
        "operations": {
            "create": {
                "roles": _roles("Sales User", "Sales Manager"),
                "fields": ["customer", "company", "transaction_date", "delivery_date", "items"],
                "child_fields": {
                    "items": ["item_code", "qty", "rate", "delivery_date", "warehouse"],
                },
            },
            "update": {
                "roles": _roles("Sales User", "Sales Manager"),
                "fields": ["delivery_date"],
                "child_fields": {},
            },
            "submit": {"roles": _roles("Sales Manager")},
            "cancel": {"roles": _roles("Sales Manager")},
        },
    },
    "Purchase Order": {
        "duplicate_keys": ["supplier"],
        "operations": {
            "create": {
                "roles": _roles("Purchase User", "Purchase Manager"),
                "fields": ["supplier", "company", "transaction_date", "schedule_date", "items"],
                "child_fields": {
                    "items": ["item_code", "qty", "rate", "schedule_date", "warehouse"],
                },
            },
            "update": {
                "roles": _roles("Purchase User", "Purchase Manager"),
                "fields": ["schedule_date", "transaction_date"],
                "child_fields": {},
            },
            "submit": {"roles": _roles("Purchase Manager")},
            "cancel": {"roles": _roles("Purchase Manager")},
        },
    },
}


def get_write_registry() -> Dict[str, Dict[str, Any]]:
    """The active registry. Read through this function so tests can substitute it."""
    return WRITE_REGISTRY


def writes_disabled() -> bool:
    """Emergency switch: ERPNEXT_WRITES_DISABLED=true makes every chat write fail."""
    return os.getenv("ERPNEXT_WRITES_DISABLED", "").strip().lower() in ("true", "1", "yes")


def send_modified_enabled() -> bool:
    """
    Whether writes carry the `modified` value the caller last read, so ERPNext rejects the
    write when the document changed in between. On by default. ERPNEXT_SEND_MODIFIED=false
    turns it off without a code change, for a site where ERPNext reports it differently.
    """
    return os.getenv("ERPNEXT_SEND_MODIFIED", "true").strip().lower() not in ("false", "0", "no")


def get_bulk_max_records() -> int:
    """Maximum documents one confirmed bulk action may touch (WRITE_BULK_MAX, default 25)."""
    raw = os.getenv("WRITE_BULK_MAX", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning("Ignoring invalid WRITE_BULK_MAX=%r; using %d.", raw, DEFAULT_BULK_MAX_RECORDS)
    return DEFAULT_BULK_MAX_RECORDS


def get_write_spec(doctype: Any) -> Optional[Dict[str, Any]]:
    """Registry entry for a doctype (case-insensitive), or None."""
    if not isinstance(doctype, str) or not doctype.strip():
        return None
    wanted = doctype.strip().lower()
    for name, spec in get_write_registry().items():
        if isinstance(name, str) and name.lower() == wanted and isinstance(spec, dict):
            return spec
    return None


def get_operation_spec(doctype: Any, operation: Any) -> Optional[Dict[str, Any]]:
    spec = get_write_spec(doctype)
    if not spec:
        return None
    ops = spec.get("operations")
    if not isinstance(ops, dict) or not isinstance(operation, str):
        return None
    op_spec = ops.get(operation)
    return op_spec if isinstance(op_spec, dict) else None


def get_duplicate_keys(doctype: Any) -> List[str]:
    spec = get_write_spec(doctype)
    keys = spec.get("duplicate_keys") if spec else None
    if not isinstance(keys, (list, tuple)):
        return []
    return [k for k in keys if isinstance(k, str) and k]


def get_operation_roles(doctype: Any, operation: Any) -> List[str]:
    op_spec = get_operation_spec(doctype, operation)
    roles = op_spec.get("roles") if op_spec else None
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return []
    return sorted({r for r in roles if isinstance(r, str) and r})


def get_writable_fields(doctype: Any, operation: Any) -> List[str]:
    op_spec = get_operation_spec(doctype, operation)
    fields = op_spec.get("fields") if op_spec else None
    if not isinstance(fields, (list, tuple, set, frozenset)):
        return []
    return [f for f in fields if isinstance(f, str) and f and f not in RESERVED_WRITE_FIELDS]


def get_child_fields(doctype: Any, operation: Any) -> Dict[str, List[str]]:
    op_spec = get_operation_spec(doctype, operation)
    child = op_spec.get("child_fields") if op_spec else None
    if not isinstance(child, dict):
        return {}
    clean: Dict[str, List[str]] = {}
    for table, row_fields in child.items():
        if isinstance(table, str) and table and isinstance(row_fields, (list, tuple, set, frozenset)):
            clean[table] = [f for f in row_fields if isinstance(f, str) and f and f not in RESERVED_WRITE_FIELDS]
    return clean


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    reason: str
    public_message: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def _allow(reason: str = "Write allowed.") -> WriteDecision:
    return WriteDecision(True, reason, "")


def _deny(reason: str, public_message: str) -> WriteDecision:
    return WriteDecision(False, reason, public_message)


def _role_decision(user_roles: Any, allowed_roles: Iterable[str]) -> bool:
    """Fail closed: bare strings, non-collections, empty roles or empty allowed roles deny."""
    if user_roles is None or isinstance(user_roles, (str, bytes)):
        return False
    if not isinstance(user_roles, (list, tuple, set, frozenset)):
        return False
    allowed = {r for r in allowed_roles if isinstance(r, str) and r}
    if not allowed:
        return False
    have = {r for r in user_roles if isinstance(r, str) and r}
    return bool(have & allowed)


def _check_fields(doctype: str, operation: str, fields: Any) -> WriteDecision:
    if not isinstance(fields, dict):
        return _deny(
            f"fields must be a dict, got {type(fields).__name__}.",
            "Invalid request parameters.",
        )

    allowed = set(get_writable_fields(doctype, operation))
    child_tables = get_child_fields(doctype, operation)

    for key in fields:
        if not isinstance(key, str) or not key:
            return _deny(f"Non-string field key {key!r}.", "Invalid request parameters.")
        if key in RESERVED_WRITE_FIELDS:
            return _deny(
                f"Field '{key}' is reserved and cannot be written.",
                f"Field '{key}' cannot be set on {doctype}.",
            )
        if key not in allowed:
            return _deny(
                f"Field '{key}' is not declared writable for {operation} on {doctype}.",
                f"Field '{key}' cannot be set on {doctype} through the assistant.",
            )

    for table, row_fields in child_tables.items():
        if table not in fields:
            continue
        rows = fields[table]
        if not isinstance(rows, list):
            return _deny(f"'{table}' must be a list of rows.", f"Invalid '{table}' value.")
        allowed_row = set(row_fields)
        for row in rows:
            if not isinstance(row, dict):
                return _deny(f"'{table}' rows must be objects.", f"Invalid '{table}' value.")
            for rkey in row:
                if not isinstance(rkey, str) or not rkey:
                    return _deny(f"Non-string row key {rkey!r} in '{table}'.", f"Invalid '{table}' value.")
                if rkey in RESERVED_WRITE_FIELDS or rkey not in allowed_row:
                    return _deny(
                        f"Row field '{rkey}' is not declared writable in '{table}' for {doctype}.",
                        f"Field '{rkey}' cannot be set on {doctype} {table} rows.",
                    )

    # A scalar value for a table field, or a list for a plain field, is malformed.
    for key, value in fields.items():
        if key not in child_tables and isinstance(value, (list, dict)):
            return _deny(
                f"Field '{key}' on {doctype} does not accept a nested value.",
                f"Invalid value for '{key}'.",
            )
    return _allow()


def check_write(
    operation: Any,
    doctype: Any,
    fields: Any = None,
    ctx: Optional[RequestContext] = None,
) -> WriteDecision:
    """
    Decide whether the caller may perform ``operation`` on ``doctype`` with ``fields``.

    Order: emergency switch, operation known, doctype and operation registered,
    read-only session, identity and roles, then field validation (create and update).
    With no request context the role and read-only checks cannot run; that is only
    accepted when ERPNEXT_STRICT_IDENTITY is off (tests, scripts and background tools),
    matching how the client treats a missing context. The registry checks always run.
    """
    if writes_disabled():
        return _deny(
            "ERPNEXT_WRITES_DISABLED is set.",
            "Write operations are currently disabled.",
        )

    if not isinstance(operation, str) or operation not in WRITE_OPERATIONS:
        return _deny(f"Unknown write operation {operation!r}.", "Invalid request parameters.")

    op_spec = get_operation_spec(doctype, operation)
    if op_spec is None:
        label = doctype if isinstance(doctype, str) and doctype.strip() else "this record type"
        return _deny(
            f"No write registry entry for {operation} on {doctype!r}.",
            f"The assistant is not set up to {operation} {label} records.",
        )

    active = ctx if ctx is not None else get_request_context()
    if active is None:
        if is_strict_identity(default=False):
            return _deny(
                "No request context and ERPNEXT_STRICT_IDENTITY is enabled.",
                "Permission denied: no authenticated user session.",
            )
    else:
        if active.read_only is not False:
            return _deny(
                "Request context is read_only.",
                "Operation not permitted: this session is read-only.",
            )
        allowed_roles = op_spec.get("roles")
        if not isinstance(allowed_roles, (list, tuple, set, frozenset)) or not _role_decision(
            active.roles, allowed_roles
        ):
            have = ", ".join(sorted(r for r in active.roles if isinstance(r, str))) or "none"
            return _deny(
                f"Roles ({have}) not in allowed roles for {operation} on {doctype}.",
                f"Permission denied: your role does not allow you to {operation} {doctype} records.",
            )

    if operation in ("create", "update"):
        field_decision = _check_fields(str(doctype).strip(), operation, fields)
        if not field_decision:
            return field_decision

    return _allow(f"{operation} on {doctype} permitted.")


def sanitize_payload(doctype: Any, operation: str, fields: Any) -> Tuple[Dict[str, Any], List[str]]:
    """
    Reduce ``fields`` to what the registry declares writable, for callers that build a
    payload from model output. Returns (clean_fields, dropped_names). It never raises and
    never adds keys. ``check_write`` is still the enforcement point; this keeps harmless
    extra keys from failing a request while making sure they are not sent.
    """
    if not isinstance(fields, dict):
        return {}, []
    allowed = set(get_writable_fields(doctype, operation))
    child_tables = get_child_fields(doctype, operation)
    clean: Dict[str, Any] = {}
    dropped: List[str] = []
    for key, value in fields.items():
        if key not in allowed:
            dropped.append(str(key))
            continue
        if key in child_tables and isinstance(value, list):
            allowed_row = set(child_tables[key])
            rows = []
            for row in value:
                if isinstance(row, dict):
                    kept = {k: v for k, v in row.items() if k in allowed_row}
                    dropped.extend(f"{key}[].{k}" for k in row if k not in allowed_row)
                    rows.append(kept)
                else:
                    rows.append(row)
            clean[key] = rows
        else:
            clean[key] = value
    return clean, dropped


def validate_write_skill(skill: Dict[str, Any]) -> List[str]:
    """
    Check a write skill against the registry. Returns a list of problems; empty means
    the skill is consistent. Used at load time so a skill can never declare more roles,
    fields or operations than the registry allows.
    """
    problems: List[str] = []
    tool = skill.get("tool", "")
    operation = TOOL_TO_OPERATION.get(tool)
    if operation is None:
        return problems  # not a write skill

    name = skill.get("name", "?")
    doctype = skill.get("doctype", "")
    op_spec = get_operation_spec(doctype, operation)
    if op_spec is None:
        return [f"skill '{name}': no registry entry for {operation} on '{doctype}'"]

    skill_roles = skill.get("allowed_roles") or []
    if not skill_roles:
        problems.append(f"skill '{name}': a write skill must declare allowed_roles")
    else:
        registry_roles = set(get_operation_roles(doctype, operation))
        extra = sorted(set(skill_roles) - registry_roles)
        if extra:
            problems.append(
                f"skill '{name}': roles {extra} are not allowed by the registry for {operation} on {doctype}"
            )

    if operation in ("create", "update"):
        allowed = set(get_writable_fields(doctype, operation))
        declared = set(skill.get("required_fields") or []) | set(skill.get("optional_fields") or [])
        mappings = (skill.get("payload_structure") or {}).get("field_mappings") or {}
        if operation == "create":
            declared |= {k for k in mappings if isinstance(k, str)}
        declared -= {"name", "id", "filters"}
        undeclared = sorted(declared - allowed)
        if undeclared:
            problems.append(
                f"skill '{name}': fields {undeclared} are not writable in the registry for {operation} on {doctype}"
            )
    return problems
