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
import math
import os
import re
from dataclasses import dataclass
from datetime import date
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
#   date_fields   fields that must be a real YYYY-MM-DD date
#   row_edits     {field: {table, match_on, positive, non_negative}}: the field is a list of edits
#                 to rows that already exist in `table`, matched on `match_on`. It is not a
#                 replacement table. Values in `positive` must be > 0, in `non_negative` >= 0.
#   draft_only    fields that may only be changed while the document is a Draft
#   confirm       fields that always need an explicit Yes before they are written
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
                "fields": ["delivery_date", "po_no", "po_date", "item_updates"],
                "child_fields": {"item_updates": ["item_code", "qty", "rate"]},
                "date_fields": ["delivery_date", "po_date"],
                "row_edits": {
                    "item_updates": {
                        "table": "items",
                        "match_on": "item_code",
                        "positive": ["qty"],
                        "non_negative": ["rate"],
                    },
                },
                "draft_only": ["item_updates"],
                "confirm": ["item_updates"],
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
                "fields": ["schedule_date", "transaction_date", "item_updates"],
                "child_fields": {"item_updates": ["item_code", "qty", "rate"]},
                "date_fields": ["schedule_date", "transaction_date"],
                "row_edits": {
                    "item_updates": {
                        "table": "items",
                        "match_on": "item_code",
                        "positive": ["qty"],
                        "non_negative": ["rate"],
                    },
                },
                "draft_only": ["item_updates"],
                "confirm": ["item_updates"],
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


def _names(op_spec: Optional[Dict[str, Any]], key: str) -> List[str]:
    raw = op_spec.get(key) if op_spec else None
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    return [f for f in raw if isinstance(f, str) and f]


def get_date_fields(doctype: Any, operation: Any) -> List[str]:
    return _names(get_operation_spec(doctype, operation), "date_fields")


def get_draft_only_fields(doctype: Any, operation: Any) -> List[str]:
    return _names(get_operation_spec(doctype, operation), "draft_only")


def get_confirm_fields(doctype: Any, operation: Any) -> List[str]:
    return _names(get_operation_spec(doctype, operation), "confirm")


def get_row_edits(doctype: Any, operation: Any) -> Dict[str, Dict[str, Any]]:
    """Row-edit declarations for an operation: {field: {table, match_on, positive, non_negative}}."""
    op_spec = get_operation_spec(doctype, operation)
    raw = op_spec.get("row_edits") if op_spec else None
    if not isinstance(raw, dict):
        return {}
    clean: Dict[str, Dict[str, Any]] = {}
    for field, edit in raw.items():
        if not (isinstance(field, str) and field and isinstance(edit, dict)):
            continue
        table, match_on = edit.get("table"), edit.get("match_on")
        if not (isinstance(table, str) and table and isinstance(match_on, str) and match_on):
            continue
        clean[field] = {
            "table": table,
            "match_on": match_on,
            "positive": _names(edit, "positive"),
            "non_negative": _names(edit, "non_negative"),
        }
    return clean


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ROW_EDITS = 50


def is_iso_date(value: Any) -> bool:
    """A real calendar date written YYYY-MM-DD."""
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def to_number(value: Any) -> Optional[float]:
    """
    A finite number from an int, a float or a numeric string; None for anything else
    (booleans, nan, inf, empty or non-numeric text). Whole numbers come back as int.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if isinstance(number, float) and not math.isfinite(number):
        return None
    if isinstance(number, float) and number.is_integer():
        return int(number)
    return number


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


def check_row_edits(doctype: Any, operation: Any, key: str, rows: Any) -> WriteDecision:
    """
    Validate one row-edit field (for example the changes to existing item rows): a list of
    1 to MAX_ROW_EDITS objects, each naming an existing row by its `match_on` field once, and
    changing at least one declared field to a number inside the declared bounds.
    The message in a denial is safe to show the user.
    """
    edit = get_row_edits(doctype, operation).get(key)
    if edit is None:
        return _deny(f"'{key}' is not a row-edit field for {operation} on {doctype}.", f"Invalid '{key}' value.")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROW_EDITS:
        return _deny(f"'{key}' must hold between 1 and {MAX_ROW_EDITS} changes.", f"Invalid '{key}' value.")
    match_on = edit["match_on"]
    editable = [f for f in get_child_fields(doctype, operation).get(key, []) if f != match_on]
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return _deny(f"'{key}' changes must be objects.", f"Invalid '{key}' value.")
        ident = row.get(match_on)
        if not isinstance(ident, str) or not ident.strip():
            return _deny(f"Each '{key}' change needs a '{match_on}'.", f"Each change needs a {match_on}.")
        if ident in seen:
            return _deny(
                f"'{match_on}' {ident!r} appears twice in '{key}'.",
                f"{ident} appears more than once in the changes.",
            )
        seen.add(ident)
        present = [f for f in editable if f in row]
        if not present:
            return _deny(
                f"A '{key}' change for {ident!r} changes nothing.",
                f"The change for {ident} does not say what to change.",
            )
        for f in present:
            number = to_number(row[f])
            if number is None:
                return _deny(f"'{f}' for {ident!r} is not a number.", f"'{f}' for {ident} must be a number.")
            if f in edit["positive"] and number <= 0:
                return _deny(f"'{f}' for {ident!r} must be above zero.", f"'{f}' for {ident} must be above zero.")
            if f in edit["non_negative"] and number < 0:
                return _deny(f"'{f}' for {ident!r} cannot be negative.", f"'{f}' for {ident} cannot be negative.")
    return _allow()


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

    for key in get_date_fields(doctype, operation):
        if key in fields and not is_iso_date(fields[key]):
            return _deny(
                f"Field '{key}' must be a date written YYYY-MM-DD, got {fields[key]!r}.",
                f"'{key}' must be a valid date written YYYY-MM-DD.",
            )

    for key in get_row_edits(doctype, operation):
        if key in fields:
            row_decision = check_row_edits(doctype, operation, key, fields[key])
            if not row_decision:
                return row_decision

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
        declared |= set(skill.get("require_any_of") or [])
        declared -= {"name", "id", "filters"}
        undeclared = sorted(declared - allowed)
        if undeclared:
            problems.append(
                f"skill '{name}': fields {undeclared} are not writable in the registry for {operation} on {doctype}"
            )
    return problems
