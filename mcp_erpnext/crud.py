import os
import re
import json
import logging
import functools
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Callable
import httpx

from mcp_erpnext.aggregation import AGGREGATION_FUNCTIONS, aggregate_rows, group_key
from mcp_erpnext.client import ERPNextClient
from mcp_erpnext.defaults import get_default_company, get_default_warehouse
from mcp_erpnext.errors import ERPNextError, extract_erpnext_error
from mcp_erpnext.write_registry import check_write, get_child_fields, get_row_edits, get_row_sync, to_number

logger = logging.getLogger("mcp_erpnext.crud")

ORDER_BY_PATTERN = re.compile(
    r"^\s*[a-zA-Z_][a-zA-Z0-9_]*(?:\s+(?:asc|desc))?"
    r"(?:\s*,\s*[a-zA-Z_][a-zA-Z0-9_]*(?:\s+(?:asc|desc))?)*\s*$",
    re.IGNORECASE
)

IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Upper bound on the number of groups one aggregate() call asks ERPNext for.
# If ERPNext returns this many, the result is flagged truncated.
MAX_AGGREGATE_GROUPS = 5000

DEFAULT_WHITELIST = [
    "Customer", "Item", "Sales Order", "Bin",
    "Quotation", "Sales Invoice", "Purchase Order",
    # Added for new skills (Batch A-C, September 2026):
    "Supplier",              # create-purchase-order entity resolution
    "Sales Order Item",      # sales-analytics-report aggregate on child table
    "Purchase Order Item",   # purchase-order-lookup item-based PO check
    "Company",               # Dynamic company resolution
    "Warehouse",             # Dynamic warehouse resolution
    # Added for Tier 1 reporting skills:
    "Delivery Note",         # delivery-status-tracking
    "Delivery Note Item",    # delivery-status-tracking child table filter
    "Purchase Receipt",      # purchase-receipt-report
    "Purchase Receipt Item", # purchase-receipt-report child table filter
    "Stock Ledger Entry",    # stock-movement-analysis
]


def get_allowed_doctypes() -> List[str]:
    env_val = os.getenv("DOCTYPE_WHITELIST") or os.getenv("ALLOWED_DOCTYPES")
    if env_val:
        items = [item.strip() for item in env_val.split(",") if item.strip()]
        if items:
            return items
    return DEFAULT_WHITELIST


def is_doctype_allowed(doctype_name: str) -> bool:
    if not doctype_name:
        return False
    allowed = get_allowed_doctypes()
    allowed_lower = {dt.lower() for dt in allowed}
    return doctype_name.strip().lower() in allowed_lower


def _format_result(
    status: str,
    data: Any = None,
    error: Optional[str] = None,
    has_more: bool = False,
    pagination: Optional[dict] = None,
) -> dict:
    """
    Format standard tool response.
    status must be one of: "success", "empty", "permission_denied", "not_found", "system_error"

    `pagination` is only passed by read tools on success/empty. It adds the keys
    total_count, offset, limit and truncated; write tools and error results keep the
    original five-key envelope.
    """
    result = {
        "status": status,
        "data": data,
        "error": error,
        "has_more": has_more,
        "success": status in ("success", "empty")
    }
    if pagination is not None:
        result.update(pagination)
    return result


def _page_meta(
    total_count: Optional[int],
    offset: int,
    limit: Optional[int],
    truncated: bool,
) -> dict:
    """Pagination metadata. total_count is None when it is not known."""
    return {
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
    }


def _parse_offset(offset: Any) -> Optional[int]:
    """Return a non-negative int offset, or None when the value is unusable."""
    if offset is None:
        return 0
    if isinstance(offset, bool):
        return None
    try:
        value = int(offset)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def get_list(
    doctype: str,
    filters: Optional[dict] = None,
    fields: Optional[list] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    client: Optional[ERPNextClient] = None,
    offset: Optional[int] = 0,
    include_total: bool = False,
    group_by: Optional[str] = None,
    parent_doctype: Optional[str] = None,
    or_filters: Optional[list] = None,
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result(
            "permission_denied",
            error=f"DocType '{doctype}' is not permitted by whitelist. Allowed DocTypes: {', '.join(get_allowed_doctypes())}"
        )

    if order_by is not None:
        if not isinstance(order_by, str) or not ORDER_BY_PATTERN.match(order_by):
            return _format_result(
                "system_error",
                error=f"Invalid order_by clause: '{order_by}'. Must be valid comma-separated field names with optional ASC/DESC."
            )

    start = _parse_offset(offset)
    if start is None:
        return _format_result(
            "system_error",
            error=f"Invalid offset: {offset!r}. Must be a non-negative integer."
        )

    if group_by is not None and (not isinstance(group_by, str) or not IDENTIFIER_PATTERN.match(group_by)):
        return _format_result(
            "system_error",
            error=f"Invalid group_by field: {group_by!r}. Must be a single field name."
        )

    # Child tables (for example Sales Order Item) only return their fields when ERPNext is told
    # which parent doctype they belong to. Filters on parent fields use the four-item form
    # [parent_doctype, field, operator, value].
    if parent_doctype is not None and not is_doctype_allowed(parent_doctype):
        return _format_result(
            "permission_denied",
            error=f"DocType '{parent_doctype}' is not permitted by whitelist."
        )

    query_params = {}
    if or_filters:
        # A row matches when ANY of these conditions holds (combined with `filters` by AND).
        query_params["or_filters"] = json.dumps(or_filters)
    if parent_doctype:
        query_params["parent"] = parent_doctype
    if group_by:
        query_params["group_by"] = group_by
    if filters:
        if isinstance(filters, list) or isinstance(filters, dict):
            query_params["filters"] = json.dumps(filters)
        else:
            query_params["filters"] = str(filters)
    if fields:
        if isinstance(fields, list):
            query_params["fields"] = json.dumps(fields)
        else:
            query_params["fields"] = str(fields)
    if order_by:
        query_params["order_by"] = order_by.strip()

    actual_limit = limit if limit else 20
    query_params["limit_page_length"] = str(actual_limit + 1)
    if start:
        query_params["limit_start"] = str(start)

    try:
        c = client or ERPNextClient()
        response = c.get(f"/api/resource/{doctype}", params=query_params, timeout=60.0)
        if response.is_success:
            try:
                resp_json = response.json()
                data = resp_json.get("data", [])
                has_more = len(data) > actual_limit
                if has_more:
                    data = data[:actual_limit]
            except Exception:
                return _format_result("system_error", error="Invalid JSON response from ERPNext")

            # The total is known for free when this page is the whole result set.
            # Otherwise it needs a second call, which only happens when the caller asks.
            total_count: Optional[int] = None
            if not has_more:
                total_count = start + len(data)
            elif include_total:
                count_res = get_count(doctype, filters=filters, client=c)
                if count_res["status"] in ("success", "empty"):
                    total_count = count_res["data"]
                else:
                    logger.warning(f"total_count unavailable for {doctype}: {count_res.get('error')}")
            return _format_result(
                "success" if data else "empty",
                data=data,
                has_more=has_more,
                pagination=_page_meta(total_count, start, actual_limit, has_more),
            )
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


def _guard_write(operation: str, doctype: str, fields: Any = None) -> Optional[dict]:
    """
    Registry, role, read-only and field checks for a write. Returns a permission_denied
    envelope when the write is not allowed, otherwise None. Runs before anything is sent
    to ERPNext and before any default is applied, so it sees exactly what the caller asked for.
    """
    decision = check_write(operation, doctype, fields)
    if decision:
        return None
    logging.getLogger("mcp_erpnext.crud").warning(
        "Write denied: operation=%s doctype=%s reason=%s", operation, doctype, decision.reason
    )
    return _format_result("permission_denied", error=decision.public_message or "Permission denied.")


# Prefix of the error text for a write ERPNext refused because the document changed after it
# was last read. The envelope keeps its five keys and existing statuses; callers recognise
# the prefix to tell a conflict from an ordinary failure.
CONFLICT_PREFIX = "Conflict:"


def is_conflict_error(error: Any) -> bool:
    return isinstance(error, str) and error.startswith(CONFLICT_PREFIX)


def _is_conflict_response(response: Any) -> bool:
    """
    True when ERPNext refused a write because the `modified` we sent is older than the
    stored one (Frappe raises TimestampMismatchError). Detection is deliberately lenient:
    it looks at the exception type and at the standard message wording.
    """
    if getattr(response, "is_success", False):
        return False
    texts = []
    try:
        body = response.json()
        if isinstance(body, dict):
            texts.extend(str(body.get(k, "")) for k in ("exc_type", "exception", "_server_messages", "message"))
    except Exception:
        pass
    try:
        texts.append(str(response.text))
    except Exception:
        pass
    blob = " ".join(texts).lower()
    return "timestampmismatcherror" in blob or "has been modified after you have opened it" in blob


def _valid_expected_modified(value: Any) -> bool:
    return value is None or (isinstance(value, str) and bool(value.strip()))


def create_document(
    doctype: str,
    fields: dict,
    client: Optional[ERPNextClient] = None,
    default_company_resolver: Optional[Callable] = None,
    default_warehouse_resolver: Optional[Callable] = None,
    logger: Optional[logging.Logger] = None,
) -> dict:
    log = logger or logging.getLogger("mcp_erpnext.crud")
    c = client or ERPNextClient()
    comp_resolver = default_company_resolver or functools.partial(get_default_company, client=c)
    wh_resolver = default_warehouse_resolver or functools.partial(get_default_warehouse, client=c)

    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    denied = _guard_write("create", doctype, fields)
    if denied is not None:
        return denied

    payload = dict(fields)

    # Dynamic default resolution for Sales Order, Purchase Order and Quotation. A Quotation needs
    # a company and a date but, unlike the orders, its item rows take no warehouse or row date.
    if doctype in ("Sales Order", "Purchase Order", "Quotation"):
        try:
            if not payload.get("company"):
                payload["company"] = comp_resolver()

            items = payload.get("items")
            default_wh = None
            if doctype != "Quotation" and isinstance(items, list) and any(isinstance(it, dict) and not it.get("warehouse") for it in items):
                default_wh = wh_resolver(payload.get("company"))
        except RuntimeError as e:
            err_msg = str(e)
            if "Default company is not configured" in err_msg:
                err_msg = "Default company is not configured. Set DEFAULT_COMPANY or pass a company."
            log.error(f"Failed to resolve default company or warehouse: {err_msg}")
            return _format_result("system_error", error=err_msg)

        if not payload.get("transaction_date"):
            payload["transaction_date"] = datetime.now().strftime("%Y-%m-%d")
        if doctype == "Sales Order":
            if not payload.get("delivery_date"):
                payload["delivery_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if default_wh and not item.get("warehouse"):
                        item["warehouse"] = default_wh
                    if not item.get("delivery_date"):
                        item["delivery_date"] = payload["delivery_date"]
        elif doctype == "Purchase Order":
            if not payload.get("schedule_date"):
                payload["schedule_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if default_wh and not item.get("warehouse"):
                        item["warehouse"] = default_wh
                    if not item.get("schedule_date"):
                        item["schedule_date"] = payload["schedule_date"]

    try:
        c = client or ERPNextClient()
        response = c.post(f"/api/resource/{doctype}", json=payload, timeout=60.0)
        if response.is_success:
            try:
                resp_json = response.json()
                data = resp_json.get("data", resp_json)
                return _format_result("success", data=data)
            except Exception:
                return _format_result("system_error", error="Invalid JSON response")
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


def update_document(
    doctype: str,
    name: str,
    fields: dict,
    client: Optional[ERPNextClient] = None,
    document_getter: Optional[Callable] = None,
    logger: Optional[logging.Logger] = None,
    expected_modified: Optional[str] = None,
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    denied = _guard_write("update", doctype, fields)
    if denied is not None:
        return denied

    return _update_unchecked(
        doctype, name, fields, client=client, document_getter=document_getter, logger=logger,
        expected_modified=expected_modified,
    )


def _apply_row_edits(
    doctype: str, name: str, key: str, spec: dict, edits: list, rows: list
) -> tuple:
    """
    Change the rows named by `edits` (matched on spec["match_on"]) and return (rows, None),
    or (None, message) when an edit cannot be applied safely. Each edit must match exactly
    one existing row: no match or several matches is reported, never guessed.
    """
    match_on = spec["match_on"]
    editable = [f for f in get_child_fields(doctype, "update").get(key, []) if f != match_on]
    if not rows:
        return None, f"Cannot change items on {doctype} {name}: it has no item rows."
    for edit in edits:
        ident = edit.get(match_on)
        matches = [r for r in rows if str(r.get(match_on)) == str(ident)]
        if not matches:
            return None, f"{ident} is not on {doctype} {name}, so nothing was changed."
        if len(matches) > 1:
            return None, (
                f"{ident} appears on {len(matches)} rows of {doctype} {name}, so it is unclear which to change. "
                f"Nothing was changed. Please change it in ERPNext."
            )
        for field in editable:
            if field in edit:
                number = to_number(edit[field])
                if number is None:
                    return None, f"'{field}' for {ident} is not a number, so nothing was changed."
                matches[0][field] = number
    return rows, None


def _update_unchecked(
    doctype: str,
    name: str,
    fields: dict,
    client: Optional[ERPNextClient] = None,
    document_getter: Optional[Callable] = None,
    logger: Optional[logging.Logger] = None,
    expected_modified: Optional[str] = None,
) -> dict:
    """
    PUT a document. Callers must already have passed the doctype whitelist and the write
    guard for the operation they represent (update, submit or cancel). submit and cancel
    send docstatus, which the field check on a plain update rightly refuses.
    """
    log = logger or logging.getLogger("mcp_erpnext.crud")
    c = client or ERPNextClient()
    doc_getter = document_getter or functools.partial(get_document, client=c)

    if not _valid_expected_modified(expected_modified):
        return _format_result("system_error", error="Invalid expected_modified: must be the document's modified timestamp.")

    payload = dict(fields)

    # Row edits ("change the quantity of X to 5") are not a replacement table. ERPNext replaces
    # a child table with whatever rows it is sent, so the full current rows are read, the
    # matching rows are changed, and every row is sent back. Anything less would delete rows.
    row_edit_specs = {k: v for k, v in get_row_edits(doctype, "update").items() if k in payload}
    edit_targets = {}
    for key, spec in row_edit_specs.items():
        edits = payload.pop(key)
        edit_targets.setdefault(spec["table"], []).append((key, spec, edits))

    # Parent-synced fields. ERPNext derives some header dates from the item rows: the Sales Order
    # delivery_date and the Purchase Order schedule_date. A header-only change is accepted and
    # silently has no effect (verified against a live instance), so the new value is also set on
    # every row. The registry declares which fields behave this way. A partial update is never
    # sent when the current document cannot be read.
    sync_specs = {
        field: spec
        for field, spec in get_row_sync(doctype, "update").items()
        if field in payload and spec["table"] not in payload
    }
    first_synced = next(iter(sync_specs), "delivery_date")
    if sync_specs or edit_targets:
        what = (
            "apply item changes to"
            if edit_targets
            else f"synchronise item {first_synced.replace('_', ' ')}s for"
        )
        try:
            current_doc_res = doc_getter(doctype, name)
            if current_doc_res.get("status") != "success":
                err_msg = f"Cannot {what} {doctype} {name}: {current_doc_res.get('error')}"
                return _format_result(current_doc_res.get("status", "system_error"), error=err_msg)

            current_doc = current_doc_res.get("data") or {}
            tables: Dict[str, list] = {}
            for header, spec in sync_specs.items():
                rows = tables.get(spec["table"])
                if rows is None:
                    rows = [dict(it) for it in (current_doc.get(spec["table"]) or [])]
                for it in rows:
                    it[spec["field"]] = payload[header]
                tables[spec["table"]] = rows
            for table, groups in edit_targets.items():
                rows = tables.get(table)
                if rows is None:
                    rows = [dict(it) for it in (current_doc.get(table) or [])]
                for key, spec, edits in groups:
                    rows, edit_error = _apply_row_edits(doctype, name, key, spec, edits, rows)
                    if edit_error:
                        return _format_result("system_error", error=edit_error)
                tables[table] = rows
            for table, rows in tables.items():
                if rows:
                    payload[table] = rows
        except RuntimeError as e:
            if edit_targets:
                log.warning(f"Failed to read {doctype} {name} before applying item changes: {e}")
            else:
                log.warning(f"Failed to synchronize items {first_synced} for {doctype} {name}: {e}")
            return _format_result("system_error", error=f"Cannot {what} {doctype} {name}: {e}")

    # Send back the `modified` value the caller last read. ERPNext then refuses the write if
    # the document changed in between, instead of silently overwriting the other change.
    if expected_modified:
        payload["modified"] = expected_modified.strip()

    try:
        c = client or ERPNextClient()
        response = c.put(f"/api/resource/{doctype}/{name}", json=payload, timeout=60.0)
        if response.is_success:
            try:
                resp_json = response.json()
                data = resp_json.get("data", resp_json)
                return _format_result("success", data=data)
            except Exception:
                return _format_result("system_error", error="Invalid JSON response")
        elif expected_modified and _is_conflict_response(response):
            return _format_result(
                "system_error",
                error=(
                    f"{CONFLICT_PREFIX} {doctype} {name} was changed by someone else after it was last read, "
                    f"so nothing was changed."
                ),
            )
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


def delete_document(doctype: str, name: str, client: Optional[ERPNextClient] = None) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    denied = _guard_write("delete", doctype)
    if denied is not None:
        return denied

    try:
        c = client or ERPNextClient()
        response = c.delete(f"/api/resource/{doctype}/{name}", timeout=60.0)
        if response.is_success:
            return _format_result("success", data="deleted")
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


def get_document(doctype: str, name: str, client: Optional[ERPNextClient] = None) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    try:
        c = client or ERPNextClient()
        response = c.get(f"/api/resource/{doctype}/{name}", timeout=60.0)
        if response.is_success:
            try:
                resp_json = response.json()
                data = resp_json.get("data", resp_json)
                return _format_result("success", data=data)
            except Exception:
                return _format_result("system_error", error="Invalid JSON response")
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


# Fields searched per doctype. A row matches when the query is contained in ANY of them, so
# a document can be found by its ID or by its display name. Doctypes not listed here are
# searched by `name` only.
SEARCH_FIELDS_MAP = {
    "Customer": ["name", "customer_name"],
    "Item": ["name", "item_code", "item_name"],
    "Sales Order": ["name"],
    "Bin": ["item_code"],
    "Quotation": ["name"],
    "Sales Invoice": ["name"],
    "Purchase Order": ["name"],
    "Supplier": ["name", "supplier_name"],
    "Warehouse": ["name", "warehouse_name"],
    "Company": ["name", "company_name"],
    "Delivery Note": ["name"],
    "Purchase Receipt": ["name"],
}
DISPLAY_FIELDS_MAP = {
    "Customer": ["name", "customer_name", "customer_group", "territory"],
    "Item": ["name", "item_code", "item_name", "item_group"],
    "Sales Order": ["name", "customer", "status"],
    "Bin": ["name", "item_code", "warehouse", "actual_qty"],
    "Quotation": ["name", "customer", "status"],
    "Sales Invoice": ["name", "customer", "status"],
    "Purchase Order": ["name", "supplier", "status"],
    "Supplier": ["name", "supplier_name", "supplier_group"],
    "Warehouse": ["name", "warehouse_name", "company"],
    "Company": ["name", "company_name"],
    "Delivery Note": ["name", "customer", "status"],
    "Purchase Receipt": ["name", "supplier", "status"],
}

# Rows fetched per search so they can be ranked before the best ones are returned.
SEARCH_FETCH_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 10


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards so a user typing % or _ matches those characters literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_tier(row: dict, search_fields: List[str], needle: str) -> int:
    """0 = a search field equals the query, 1 = starts with it, 2 = contains it (case-insensitive)."""
    best = 2
    for field in search_fields:
        value = row.get(field)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text == needle:
            return 0
        if text.startswith(needle):
            best = 1
    return best


def search_document(
    doctype: str,
    query: str,
    client: Optional[ERPNextClient] = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> dict:
    """
    Find documents by a partial name or ID.

    Every field in SEARCH_FIELDS_MAP for the doctype is searched, `%` and `_` in the query
    are matched literally, and the results are ranked: exact, then starts-with, then contains.
    If any document matches the query exactly, ONLY the exact matches are returned, so a user
    who types a full, existing name is not asked to choose between it and longer names that
    merely contain it.
    """
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    needle = str(query or "").strip()
    if not needle:
        return _format_result("system_error", error="Search query must not be empty.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return _format_result("system_error", error=f"Invalid limit: {limit!r}. Must be a positive integer.")

    search_fields = SEARCH_FIELDS_MAP.get(doctype, ["name"])
    display_fields = list(DISPLAY_FIELDS_MAP.get(doctype, ["name"]))
    for field in search_fields:  # ranking reads these, so they must be in the result rows
        if field not in display_fields:
            display_fields.append(field)

    escaped = _escape_like(needle)
    res = get_list(
        doctype,
        fields=display_fields,
        or_filters=[[field, "like", f"%{escaped}%"] for field in search_fields],
        limit=SEARCH_FETCH_LIMIT,
        client=client,
    )
    if res["status"] not in ("success", "empty"):
        return res

    rows = res.get("data") or []
    fetched_all = not res.get("has_more")
    lowered = needle.lower()
    ranked = sorted(
        ((_match_tier(row, search_fields, lowered), str(row.get("name", "")), row) for row in rows),
        key=lambda item: (item[0], item[1]),
    )
    exact = [row for tier, _, row in ranked if tier == 0]

    if not exact and not fetched_all:
        # More partial matches exist than were fetched, so an exact match could be among the
        # ones not seen. Ask for exact matches directly.
        exact_res = get_list(
            doctype,
            fields=display_fields,
            or_filters=[[field, "=", needle] for field in search_fields],
            limit=limit,
            client=client,
        )
        if exact_res["status"] in ("success", "empty"):
            exact = exact_res.get("data") or []

    if exact:
        shown = exact[:limit]
        return _format_result(
            "success",
            data=shown,
            has_more=len(exact) > limit,
            pagination=_page_meta(len(exact), 0, limit, len(exact) > limit),
        )

    ordered = [row for _, _, row in ranked]
    shown = ordered[:limit]
    has_more = len(ordered) > limit or not fetched_all
    return _format_result(
        "success" if shown else "empty",
        data=shown,
        has_more=has_more,
        pagination=_page_meta(len(ordered) if fetched_all else None, 0, limit, has_more),
    )


def get_count(doctype: str, filters: Optional[dict] = None, client: Optional[ERPNextClient] = None) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    # Counted by the database through ERPNext's own count method: no rows are transferred
    # and there is no ceiling on the number of matching documents.
    query_params = {"doctype": doctype}
    if filters:
        if isinstance(filters, (list, dict)):
            query_params["filters"] = json.dumps(filters)
        else:
            query_params["filters"] = str(filters)

    try:
        c = client or ERPNextClient()
        response = c.get("/api/method/frappe.client.get_count", params=query_params, timeout=60.0)
        if response.is_success:
            try:
                count = response.json().get("message")
            except Exception:
                return _format_result("system_error", error="Invalid JSON response from ERPNext")
            if isinstance(count, bool) or not isinstance(count, int):
                return _format_result("system_error", error="Invalid count returned by ERPNext")
            return _format_result(
                "empty" if count == 0 else "success",
                data=count,
                pagination=_page_meta(count, 0, None, False),
            )
        elif response.status_code == 404:
            return _format_result("not_found", error=extract_erpnext_error(response))
        elif response.status_code in (401, 403):
            return _format_result("permission_denied", error=extract_erpnext_error(response))
        else:
            return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))


def _finish_aggregate(
    groups: Dict[str, Any],
    group_by: str,
    metric: str,
    aggregation_fn: str,
    sort: Optional[str],
    limit: Optional[int],
    truncated: bool,
) -> dict:
    """Sort, limit and wrap grouped values as [{group_by: g, metric: v}, ...]."""
    result_list = [{group_by: g, metric: v} for g, v in groups.items()]
    if not result_list:
        return _format_result("empty", data=[], pagination=_page_meta(0, 0, limit, truncated))

    if sort:
        parts = sort.split()
        reverse = len(parts) == 2 and parts[1].lower() == "desc"
        # Order by group name first, then by the requested field. The sort is stable, so
        # equal values always come out in the same (alphabetical) order.
        result_list.sort(key=lambda x: str(x[group_by]))
        result_list.sort(key=lambda x: x[parts[0]], reverse=reverse)
    if limit:
        result_list = result_list[:limit]

    return _format_result(
        "success",
        data=result_list,
        pagination=_page_meta(len(groups), 0, limit, truncated),
    )


def _aggregate_in_database(
    doctype: str,
    group_by: str,
    metric: str,
    aggregation_fn: str,
    filters: Optional[dict],
    client: Optional[ERPNextClient],
    parent_doctype: Optional[str] = None,
) -> dict:
    """
    Ask ERPNext to group and aggregate. Returns {group value: aggregated value} wrapped in
    an envelope-like dict: {"status": ..., "groups": {...}, "truncated": bool} on success,
    or the failing envelope. "unsupported" means ERPNext did not honour the query.
    """
    sql_fn = AGGREGATION_FUNCTIONS[aggregation_fn]
    target = "name" if aggregation_fn == "count" else metric
    res = get_list(
        doctype,
        filters=filters,
        # Frappe 16 rejects SQL functions written as strings ("sum(x) as y"); it wants the
        # dict form. Verified against Frappe 16.24 for SUM, COUNT, AVG, MAX and MIN.
        fields=[group_by, {sql_fn.upper(): target, "as": "agg_value"}],
        limit=MAX_AGGREGATE_GROUPS,
        group_by=group_by,
        client=client,
        parent_doctype=parent_doctype,
    )
    if res["status"] not in ("success", "empty"):
        return res

    groups: Dict[str, Any] = {}
    for row in res.get("data") or []:
        if not isinstance(row, dict) or "agg_value" not in row:
            # ERPNext ignored the aggregate fields and returned plain documents.
            return {"status": "unsupported"}
        g_key = group_key(row.get(group_by))
        raw = row["agg_value"]
        if aggregation_fn == "count":
            value: Any = int(raw or 0)
        else:
            value = float(raw or 0)
        if g_key in groups:
            # Two raw values that stringify the same cannot be merged safely for avg/min/max.
            return {"status": "unsupported"}
        groups[g_key] = value
    return {"status": "ok", "groups": groups, "truncated": bool(res.get("has_more"))}


def _aggregate_in_python(
    doctype: str,
    group_by: str,
    metric: str,
    aggregation_fn: str,
    filters: Optional[dict],
    client: Optional[ERPNextClient],
    parent_doctype: Optional[str] = None,
) -> dict:
    """Fallback: fetch the rows and aggregate here. Used when the database path is unavailable."""
    fields = ["name", group_by, metric]
    res = get_list(
        doctype, filters=filters, fields=fields, limit=999999, client=client,
        parent_doctype=parent_doctype,
    )
    if res["status"] not in ("success", "empty"):
        return res

    try:
        agg = aggregate_rows(res.get("data") or [], group_by, metric, aggregation_fn)
    except ValueError as e:
        return _format_result("system_error", error=str(e))
    return {"status": "ok", "groups": agg, "truncated": bool(res.get("has_more"))}


def aggregate(
    doctype: str,
    group_by: str,
    metric: str,
    aggregation_fn: str,
    filters: Optional[dict] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    client: Optional[ERPNextClient] = None,
    parent_doctype: Optional[str] = None,
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")
    if parent_doctype is not None and not is_doctype_allowed(parent_doctype):
        return _format_result("permission_denied", error=f"DocType '{parent_doctype}' is not permitted by whitelist.")

    # Field names and the function end up inside the query sent to ERPNext, so they are
    # validated here and never passed through as free text.
    for label, value in (("group_by", group_by), ("metric", metric)):
        if not isinstance(value, str) or not IDENTIFIER_PATTERN.match(value):
            return _format_result("system_error", error=f"Invalid {label} field: {value!r}. Must be a single field name.")
    if aggregation_fn not in AGGREGATION_FUNCTIONS:
        return _format_result(
            "system_error",
            error=f"Unsupported aggregation_fn: {aggregation_fn!r}. Use one of: {', '.join(AGGREGATION_FUNCTIONS)}."
        )
    if sort:
        sort_parts = str(sort).split()
        if (
            len(sort_parts) not in (1, 2)
            or sort_parts[0] not in (group_by, metric)
            or (len(sort_parts) == 2 and sort_parts[1].lower() not in ("asc", "desc"))
        ):
            return _format_result(
                "system_error",
                error=f"Invalid sort: {sort!r}. Use '<{group_by}|{metric}> [asc|desc]'."
            )
        sort = " ".join(sort_parts)
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        return _format_result("system_error", error=f"Invalid limit: {limit!r}. Must be a non-negative integer.")

    outcome = _aggregate_in_database(doctype, group_by, metric, aggregation_fn, filters, client, parent_doctype)
    if outcome["status"] in ("system_error", "unsupported"):
        # Only a failure of the aggregate query itself justifies the slower path. Permission
        # and not-found results are returned as they are.
        logger.warning(
            f"Database-side aggregation unavailable for {doctype} ({outcome.get('error') or 'ignored by ERPNext'}); "
            "falling back to Python-side aggregation."
        )
        outcome = _aggregate_in_python(doctype, group_by, metric, aggregation_fn, filters, client, parent_doctype)

    if outcome["status"] != "ok":
        return outcome
    return _finish_aggregate(
        outcome["groups"], group_by, metric, aggregation_fn, sort, limit, outcome["truncated"]
    )


def cancel_document(
    doctype: str,
    name: str,
    client: Optional[ERPNextClient] = None,
    expected_modified: Optional[str] = None,
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    denied = _guard_write("cancel", doctype)
    if denied is not None:
        return denied

    return _update_unchecked(doctype, name, {"docstatus": 2}, client=client, expected_modified=expected_modified)


def submit_document(
    doctype: str,
    name: str,
    client: Optional[ERPNextClient] = None,
    expected_modified: Optional[str] = None,
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    denied = _guard_write("submit", doctype)
    if denied is not None:
        return denied

    return _update_unchecked(doctype, name, {"docstatus": 1}, client=client, expected_modified=expected_modified)
