"""
run_report: run an ERPNext built-in report through a fixed allowlist.

Security model (everything fails closed):
  * Only reports named in REPORT_ALLOWLIST can run. Anything else, including a report that
    exists in ERPNext, is refused with the same message as an unknown one.
  * A report runs only when the caller holds one of its `allowed_roles`. An empty
    `allowed_roles`, or missing / empty caller roles, denies access.
  * `user_roles` is a keyword-only argument for the CALLER to fill from trusted, authenticated
    context. It must never be built from request arguments.
  * Report filters are checked against the per-report declaration (name, type, required,
    allowed choices). Unknown filters are rejected, not passed through.
  * Post-processing is limited to a fixed set of row-filter operators and the fixed aggregation
    functions in mcp_erpnext.aggregation. Results are capped at `max_rows`.

The allowlist below covers operational reports only. Financial reports (registers, receivables,
statements) are deliberately absent until per-user identity is in place.

This module is not wired into the chatbot or any skill.
"""

import json
import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from mcp_erpnext.aggregation import AGGREGATION_FUNCTIONS, aggregate_rows
from mcp_erpnext.client import ERPNextClient
from mcp_erpnext.crud import IDENTIFIER_PATTERN, _format_result, _page_meta
from mcp_erpnext.errors import extract_erpnext_error

logger = logging.getLogger("mcp_erpnext.reports")

REPORT_RUN_PATH = "/api/method/frappe.desk.query_report.run"
REPORT_TIMEOUT_SECONDS = 120.0

# Absolute ceiling on rows returned, whatever a report's own max_rows says.
HARD_MAX_ROWS = 1000
# Operators allowed in row_filters.
ROW_FILTER_OPERATORS = ("=", "!=", ">", ">=", "<", "<=", "contains")
MAX_ROW_FILTERS = 10
MAX_STRING_FILTER_LENGTH = 140

_STOCK_ROLES = ["Stock User", "Stock Manager", "System Manager"]
_SALES_ROLES = ["Sales User", "Sales Manager", "System Manager"]

# Filter declaration: {"type": "str" | "date" | "int" | "choice", "required": bool, "choices": [...]}
REPORT_ALLOWLIST: Dict[str, Dict[str, Any]] = {
    "Stock Projected Qty": {
        "allowed_roles": _STOCK_ROLES,
        "max_rows": 500,
        "filters": {
            "company": {"type": "str", "required": True},
            "warehouse": {"type": "str"},
            "item_code": {"type": "str"},
            "item_group": {"type": "str"},
        },
    },
    "Stock Ledger": {
        "allowed_roles": _STOCK_ROLES,
        "max_rows": 500,
        "filters": {
            "company": {"type": "str", "required": True},
            "from_date": {"type": "date", "required": True},
            "to_date": {"type": "date", "required": True},
            "item_code": {"type": "str"},
            "warehouse": {"type": "str"},
        },
    },
    "Sales Analytics": {
        "allowed_roles": _SALES_ROLES,
        "max_rows": 500,
        "filters": {
            "company": {"type": "str", "required": True},
            "tree_type": {"type": "choice", "required": True, "choices": [
                "Customer", "Item", "Customer Group", "Item Group", "Territory", "Order Type", "Project"]},
            "doc_type": {"type": "choice", "required": True, "choices": ["Sales Order", "Delivery Note", "Quotation"]},
            "value_quantity": {"type": "choice", "required": True, "choices": ["Value", "Quantity"]},
            "from_date": {"type": "date", "required": True},
            "to_date": {"type": "date", "required": True},
            "range": {"type": "choice", "required": True, "choices": ["Monthly", "Weekly", "Quarterly", "Yearly"]},
        },
    },
}


def get_report_allowlist() -> Dict[str, Dict[str, Any]]:
    """The reports that may run. A seam for the shared security policy to replace."""
    return REPORT_ALLOWLIST


def _denied(report_name: Any) -> dict:
    # The same message for unknown, unlisted and role-denied reports, so a caller cannot
    # use the error text to discover which reports exist.
    return _format_result("permission_denied", error=f"Report '{report_name}' is not available.")


def _authorize(report_name: Any, user_roles: Optional[Iterable[str]]) -> Optional[Dict[str, Any]]:
    """Return the report's allowlist entry when the caller may run it, else None."""
    if not isinstance(report_name, str) or user_roles is None or isinstance(user_roles, (str, bytes)):
        return None
    spec = get_report_allowlist().get(report_name)
    if not spec:
        return None
    allowed = {r for r in (spec.get("allowed_roles") or []) if isinstance(r, str) and r}
    if not allowed:  # empty allowed_roles denies everyone
        return None
    held = {r for r in user_roles if isinstance(r, str) and r}
    return spec if allowed & held else None


def _validate_filters(spec: dict, filters: Any) -> "tuple[Optional[dict], Optional[str]]":
    """Check report filters against the declaration. Returns (clean filters, error message)."""
    declared = spec.get("filters") or {}
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        return None, "filters must be an object of filter name to value."

    unknown = sorted(str(k) for k in filters if k not in declared)
    if unknown:
        return None, f"Unsupported filter(s): {', '.join(unknown)}. Allowed: {', '.join(declared)}."

    clean: Dict[str, Any] = {}
    for name, rule in declared.items():
        if name not in filters or filters[name] in (None, ""):
            if rule.get("required"):
                return None, f"Missing required filter: {name}."
            continue
        value = filters[name]
        kind = rule.get("type", "str")
        if kind == "str":
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_STRING_FILTER_LENGTH \
                    or any(ord(ch) < 32 for ch in value):
                return None, f"Invalid value for filter {name}."
            clean[name] = value.strip()
        elif kind == "date":
            try:
                if not isinstance(value, str):
                    raise ValueError
                clean[name] = date.fromisoformat(value).isoformat()
            except ValueError:
                return None, f"Invalid date for filter {name}: use YYYY-MM-DD."
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                return None, f"Invalid value for filter {name}: must be an integer."
            clean[name] = value
        elif kind == "choice":
            if value not in (rule.get("choices") or []):
                return None, f"Invalid value for filter {name}. Allowed: {', '.join(rule.get('choices') or [])}."
            clean[name] = value
        else:  # an allowlist entry with an unknown type is a configuration error: refuse
            return None, f"Filter {name} has an unsupported type."
    return clean, None


def _fieldnames(columns: List[Any]) -> List[str]:
    """Column fieldnames. Some reports describe columns as 'Label:Type:Width' strings."""
    names: List[str] = []
    for col in columns:
        if isinstance(col, dict):
            names.append(str(col.get("fieldname") or col.get("label") or ""))
        else:
            names.append(str(col).split(":")[0])
    return names


def _normalize_rows(result: List[Any], names: List[str], add_total_row: bool) -> "tuple[List[dict], Optional[dict]]":
    """
    Rows as dicts keyed by fieldname. When the report adds a total row, it is the last row and
    is a list lined up with the columns; it is returned separately so it is never counted as data.
    """
    totals: Optional[dict] = None
    rows = list(result)
    if add_total_row and rows and isinstance(rows[-1], (list, tuple)):
        last = rows.pop()
        totals = {n: v for n, v in zip(names, last) if n}

    out: List[dict] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({n: v for n, v in zip(names, row) if n})
    return out, totals


def _compare(cell: Any, op: str, value: Any) -> bool:
    if cell is None:
        return op == "!="
    if op == "contains":
        return str(value).lower() in str(cell).lower()
    try:
        left, right = float(cell), float(value)
    except (TypeError, ValueError):
        left, right = str(cell).lower(), str(value).lower()
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    return left <= right


def _validate_row_filters(row_filters: Any, fieldnames: List[str]) -> "tuple[Optional[list], Optional[str]]":
    if row_filters is None:
        return [], None
    if not isinstance(row_filters, (list, tuple)) or len(row_filters) > MAX_ROW_FILTERS:
        return None, f"row_filters must be a list of at most {MAX_ROW_FILTERS} [field, operator, value] items."
    clean = []
    for item in row_filters:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            return None, "Each row filter must be [field, operator, value]."
        field, op, value = item
        if field not in fieldnames:
            return None, f"Unknown column in row filter: {field!r}."
        if op not in ROW_FILTER_OPERATORS:
            return None, f"Unsupported operator {op!r}. Use one of: {', '.join(ROW_FILTER_OPERATORS)}."
        if isinstance(value, (dict, list, tuple)):
            return None, "Row filter values must be plain values."
        clean.append((field, op, value))
    return clean, None


def run_report(
    report_name: str,
    filters: Optional[dict] = None,
    *,
    user_roles: Optional[Iterable[str]],
    row_filters: Optional[list] = None,
    group_by: Optional[str] = None,
    metric: Optional[str] = None,
    aggregation_fn: Optional[str] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    client: Optional[ERPNextClient] = None,
) -> dict:
    """
    Run an allowlisted ERPNext report for a caller holding `user_roles`.

    `user_roles` has no default on purpose: the caller must pass roles taken from trusted
    authentication context (or None, which denies). `filters` are the report's own filters.
    `row_filters` ([field, operator, value]) and `group_by`/`metric`/`aggregation_fn` post-process
    the returned rows. `sort` is "<column> [asc|desc]". The result is capped at the report's
    `max_rows` (never above HARD_MAX_ROWS) and flagged `truncated` when rows were left out.

    Success data: {"report", "columns", "rows", "totals"}.
    """
    spec = _authorize(report_name, user_roles)
    if spec is None:
        return _denied(report_name)

    clean_filters, error = _validate_filters(spec, filters)
    if error:
        return _format_result("system_error", error=error)

    max_rows = min(int(spec.get("max_rows") or HARD_MAX_ROWS), HARD_MAX_ROWS)
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        return _format_result("system_error", error=f"Invalid limit: {limit!r}. Must be a positive integer.")
    effective_limit = min(limit, max_rows) if limit else max_rows

    sort_parts: List[str] = []
    if sort:
        sort_parts = str(sort).split()
        if len(sort_parts) not in (1, 2) or (len(sort_parts) == 2 and sort_parts[1].lower() not in ("asc", "desc")):
            return _format_result("system_error", error=f"Invalid sort: {sort!r}. Use '<column> [asc|desc]'.")

    aggregating = any(x is not None for x in (group_by, metric, aggregation_fn))
    if aggregating:
        if aggregation_fn not in AGGREGATION_FUNCTIONS:
            return _format_result(
                "system_error",
                error=f"Unsupported aggregation_fn: {aggregation_fn!r}. Use one of: {', '.join(AGGREGATION_FUNCTIONS)}."
            )
        if not isinstance(group_by, str) or not IDENTIFIER_PATTERN.match(group_by):
            return _format_result("system_error", error="group_by is required for aggregation and must be a column name.")
        if aggregation_fn != "count" and (not isinstance(metric, str) or not IDENTIFIER_PATTERN.match(metric)):
            return _format_result("system_error", error="metric is required for this aggregation and must be a column name.")

    try:
        c = client or ERPNextClient()
        response = c.get(
            REPORT_RUN_PATH,
            params={"report_name": report_name, "filters": json.dumps(clean_filters)},
            timeout=REPORT_TIMEOUT_SECONDS,
        )
    except Exception as e:
        return _format_result("system_error", error=str(e))

    if not response.is_success:
        if response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        if response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        return _format_result("system_error", error=extract_erpnext_error(response))

    try:
        message = response.json().get("message")
    except Exception:
        return _format_result("system_error", error="Invalid JSON response from ERPNext")
    if not isinstance(message, dict):
        return _format_result("system_error", error="Unexpected report response from ERPNext")
    if message.get("prepared_report") and not isinstance(message.get("result"), list):
        return _format_result(
            "system_error",
            error=f"Report '{report_name}' runs as a background prepared report and cannot be run synchronously."
        )
    columns, result = message.get("columns"), message.get("result")
    if not isinstance(columns, list) or not isinstance(result, list):
        return _format_result("system_error", error="Unexpected report response from ERPNext")

    names = _fieldnames(columns)
    rows, totals = _normalize_rows(result, names, bool(message.get("add_total_row")))

    parsed_filters, error = _validate_row_filters(row_filters, names)
    if error:
        return _format_result("system_error", error=error)
    for field, op, value in parsed_filters:
        rows = [r for r in rows if _compare(r.get(field), op, value)]

    column_info = [
        {"fieldname": n, "label": (col.get("label") if isinstance(col, dict) else n) or n,
         "fieldtype": col.get("fieldtype") if isinstance(col, dict) else None}
        for n, col in zip(names, columns) if n
    ]

    if aggregating:
        for label, field in (("group_by", group_by), ("metric", metric if aggregation_fn != "count" else None)):
            if field is not None and field not in names:
                return _format_result("system_error", error=f"Unknown column for {label}: {field!r}.")
        try:
            groups = aggregate_rows(rows, group_by, metric, aggregation_fn)
        except ValueError as e:
            return _format_result("system_error", error=str(e))
        value_key = metric if aggregation_fn != "count" else (metric or "count")
        rows = [{group_by: g, value_key: v} for g, v in groups.items()]
        out_names = [group_by, value_key]
        column_info = [{"fieldname": group_by, "label": group_by, "fieldtype": None},
                       {"fieldname": value_key, "label": value_key, "fieldtype": None}]
        totals = None
    else:
        out_names = names

    if sort_parts:
        sort_field = sort_parts[0]
        if sort_field not in out_names:
            return _format_result("system_error", error=f"Invalid sort: {sort!r}. Unknown column {sort_field!r}.")
        reverse = len(sort_parts) == 2 and sort_parts[1].lower() == "desc"
        present = [r for r in rows if r.get(sort_field) not in (None, "")]
        missing = [r for r in rows if r.get(sort_field) in (None, "")]
        try:
            present.sort(key=lambda r: float(r[sort_field]), reverse=reverse)
        except (TypeError, ValueError):
            present.sort(key=lambda r: str(r[sort_field]).lower(), reverse=reverse)
        rows = present + missing  # rows without a value always sort last

    total_rows = len(rows)
    truncated = total_rows > effective_limit
    rows = rows[:effective_limit]

    data = {"report": report_name, "columns": column_info, "rows": rows, "totals": totals}
    return _format_result(
        "success" if rows else "empty",
        data=data,
        has_more=truncated,
        pagination=_page_meta(total_rows, 0, effective_limit, truncated),
    )
