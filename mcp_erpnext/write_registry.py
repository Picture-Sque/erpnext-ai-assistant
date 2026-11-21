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

WRITE_OPERATIONS: Tuple[str, ...] = ("create", "update", "submit", "cancel", "delete", "convert")

# Tool name -> operation name. The five document write tools, plus convert_document, which creates
# a draft of one document type from another with ERPNext's own mapper (a delivery note from a sales
# order). convert_document is chat-only: it is not an MCP tool, so external MCP clients cannot use it.
TOOL_TO_OPERATION: Dict[str, str] = {
    "create_document": "create",
    "update_document": "update",
    "submit_document": "submit",
    "cancel_document": "cancel",
    "delete_document": "delete",
    "convert_document": "convert",
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
#   row_sync      {header field: {table, field}}: ERPNext derives this header field from the rows of
#                 `table`, so changing it means setting `field` on every row (verified live for
#                 the Sales Order delivery date and the Purchase Order schedule date: a
#                 header-only change is accepted and silently has no effect)
#   choices       {field: [allowed values]}
#   identity_slot the slot that names the record an update acts on, when that is not "name"
#   confirm_always  every use of this operation needs an explicit Yes (create, for master data)
#   similar_by    {field, slot}: before a create, look for existing records with a similar name
#   partial_rows  (convert only) {table, match_on, qty_field, slot}: the conversation may name some of the
#                 rows of the mapped draft and ask for fewer units of them. It can only ever REDUCE: a row
#                 not named is left out, a named row cannot exceed what ERPNext mapped, and no row is added
#   sources       (convert only) {source doctype: {method, source_docstatus, linked_by}}: the document
#                 types a new document may be made from, ERPNext's mapping method for each, the state
#                 the source must be in, and how to find documents already made from it
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
                "row_sync": {"delivery_date": {"table": "items", "field": "delivery_date"}},
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
            "convert": {
                "roles": _roles("Sales User", "Sales Manager"),
                "confirm_always": True,
                "identity_slot": "quotation",
                "partial_rows": {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"},
                "sources": {
                    "Quotation": {
                        "method": "erpnext.selling.doctype.quotation.quotation.make_sales_order",
                        "source_docstatus": 1,
                        # How to find orders already made from the same quotation: the quotation is
                        # stored on each order row. ERPNext lets a quotation be converted again while
                        # an earlier order is still a draft, so the user is told about them.
                        "linked_by": {"child_doctype": "Sales Order Item", "parent_doctype": "Sales Order", "field": "prevdoc_docname"},
                    },
                },
            },
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
                "row_sync": {"schedule_date": {"table": "items", "field": "schedule_date"}},
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


WRITE_REGISTRY["Delivery Note"] = {
    # Only created from a Sales Order, as a Draft. Creating, updating, submitting and cancelling a
    # Delivery Note directly are not registered, and submitting one posts to stock, so that is a
    # separate decision. ERPNext's own mapper includes only what is still pending on the order.
    "operations": {
        "convert": {
            "roles": _roles("Stock User", "Stock Manager", "Sales User"),
            "confirm_always": True,
            "identity_slot": "sales_order",
            "partial_rows": {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"},
            "sources": {
                "Sales Order": {
                    "method": "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
                    "source_docstatus": 1,
                    "linked_by": {"child_doctype": "Delivery Note Item", "parent_doctype": "Delivery Note", "field": "against_sales_order"},
                    "done_percent_field": "per_delivered",
                    "done_qty_field": "delivered_qty",
                    "done_label": "delivered",
                    "note": "Nothing is posted to stock until the Delivery Note is submitted.",
                    "existing_note": (
                        "Note: Delivery Note records already exist for this Sales Order: {names}. A draft does not reduce "
                        "what is still to deliver, so creating another would repeat it."
                    ),
                },
            },
        },
    },
}

WRITE_REGISTRY["Purchase Receipt"] = {
    # Only created from a Purchase Order, as a Draft; see Delivery Note for the reasoning.
    "operations": {
        "convert": {
            "roles": _roles("Stock User", "Stock Manager", "Purchase User"),
            "confirm_always": True,
            "identity_slot": "purchase_order",
            "partial_rows": {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"},
            "sources": {
                "Purchase Order": {
                    "method": "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
                    "source_docstatus": 1,
                    "linked_by": {"child_doctype": "Purchase Receipt Item", "parent_doctype": "Purchase Receipt", "field": "purchase_order"},
                    "done_percent_field": "per_received",
                    "done_qty_field": "received_qty",
                    "done_label": "received",
                    "note": "Nothing is posted to stock until the Purchase Receipt is submitted.",
                    "existing_note": (
                        "Note: Purchase Receipt records already exist for this Purchase Order: {names}. A draft does not reduce "
                        "what is still to receive, so creating another would repeat it."
                    ),
                },
            },
        },
    },
}

WRITE_REGISTRY["Sales Invoice"] = {
    # Only created from a Sales Order, as a Draft. Submitting an invoice posts to accounts, so submit and
    # cancel are NOT registered: who may do that from chat is a segregation-of-duties decision for the
    # owners of that control (compliance review). Amounts come from the order through ERPNext's mapper.
    "operations": {
        "convert": {
            "roles": _roles("Accounts User", "Accounts Manager", "Sales User"),
            "confirm_always": True,
            "identity_slot": "sales_order",
            "partial_rows": {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"},
            "sources": {
                "Sales Order": {
                    "method": "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
                    "source_docstatus": 1,
                    "linked_by": {"child_doctype": "Sales Invoice Item", "parent_doctype": "Sales Invoice", "field": "sales_order"},
                    "done_percent_field": "per_billed",
                    "done_label": "billed",
                    "note": "Nothing is posted to accounts until the Sales Invoice is submitted.",
                    "existing_note": (
                        "Note: Sales Invoice records already exist for this Sales Order: {names}. A draft does not reduce "
                        "what is still to bill, so creating another would repeat it."
                    ),
                },
            },
        },
    },
}

WRITE_REGISTRY["Purchase Invoice"] = {
    # Only created from a Purchase Order, as a Draft; see Sales Invoice for the reasoning.
    "operations": {
        "convert": {
            "roles": _roles("Accounts User", "Accounts Manager", "Purchase User"),
            "confirm_always": True,
            "identity_slot": "purchase_order",
            "partial_rows": {"table": "items", "match_on": "item_code", "qty_field": "qty", "slot": "items"},
            "sources": {
                "Purchase Order": {
                    "method": "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
                    "source_docstatus": 1,
                    "linked_by": {"child_doctype": "Purchase Invoice Item", "parent_doctype": "Purchase Invoice", "field": "purchase_order"},
                    "done_percent_field": "per_billed",
                    "done_label": "billed",
                    "note": "Nothing is posted to accounts until the Purchase Invoice is submitted.",
                    "existing_note": (
                        "Note: Purchase Invoice records already exist for this Purchase Order: {names}. A draft does not reduce "
                        "what is still to bill, so creating another would repeat it."
                    ),
                },
            },
        },
    },
}

WRITE_REGISTRY["Quotation"] = {
    # The customer is stored in party_name (with quotation_to = "Customer"), but the skill asks
    # the user for the "customer", so the duplicate check reads that slot.
    "duplicate_keys": ["party_name"],
    "duplicate_slots": {"party_name": "customer"},
    "operations": {
        "create": {
            "roles": _roles("Sales User", "Sales Manager"),
            "fields": ["quotation_to", "party_name", "company", "transaction_date", "valid_till", "items"],
            "child_fields": {"items": ["item_code", "qty", "rate"]},
            "date_fields": ["transaction_date", "valid_till"],
        },
        "update": {
            "roles": _roles("Sales User", "Sales Manager"),
            "fields": ["valid_till", "item_updates"],
            "child_fields": {"item_updates": ["item_code", "qty", "rate"]},
            "date_fields": ["valid_till"],
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
}


WRITE_REGISTRY["Customer"] = {
    # Master data: not submittable, so every change lands on live records. A create always asks
    # (with a note of similar existing names) and an update always asks, with a before and after.
    # Credit limits, accounts, tax, payment terms and the like are not listed: not writable.
    "duplicate_keys": ["customer_name"],
    "operations": {
        "create": {
            "roles": _roles("Sales User", "Sales Manager"),
            "fields": ["customer_name", "customer_type", "customer_group", "territory", "website"],
            "child_fields": {},
            "choices": {"customer_type": ["Company", "Individual", "Partnership"]},
            "confirm_always": True,
            "similar_by": {"field": "customer_name", "slot": "customer_name"},
        },
        "update": {
            "roles": _roles("Sales User", "Sales Manager"),
            "fields": ["customer_type", "customer_group", "territory", "website"],
            "child_fields": {},
            "choices": {"customer_type": ["Company", "Individual", "Partnership"]},
            # The user names the customer (and ambiguity is resolved with them), so the record is
            # identified by this slot, which is not itself something an update may change.
            "identity_slot": "customer_name",
            "confirm": ["customer_type", "customer_group", "territory", "website"],
        },
    },
}


WRITE_REGISTRY["Supplier"] = {
    # Master data, like Customer. The supplier is asked for as "the supplier", so the create slot
    # and the update's identity slot are both `supplier`, which is also what entity resolution
    # already resolves against existing suppliers. Payment holds, tax, payment terms, accounts,
    # the disabled and frozen flags and the purchasing switches are not listed: not writable.
    "duplicate_keys": ["supplier_name"],
    "duplicate_slots": {"supplier_name": "supplier"},
    "operations": {
        "create": {
            "roles": _roles("Purchase User", "Purchase Manager"),
            "fields": ["supplier_name", "supplier_type", "supplier_group", "country", "website"],
            "child_fields": {},
            "choices": {"supplier_type": ["Company", "Individual", "Partnership"]},
            "confirm_always": True,
            "similar_by": {"field": "supplier_name", "slot": "supplier"},
        },
        "update": {
            "roles": _roles("Purchase User", "Purchase Manager"),
            "fields": ["supplier_type", "supplier_group", "country", "website"],
            "child_fields": {},
            "choices": {"supplier_type": ["Company", "Individual", "Partnership"]},
            "identity_slot": "supplier",
            "confirm": ["supplier_type", "supplier_group", "country", "website"],
        },
    },
}


WRITE_REGISTRY["Item"] = {
    # Master data with a deliberately short write list. An item code is its name and cannot be
    # renamed here; the stock unit is set when the item is created and not changed afterwards,
    # because stock already counted in it would no longer add up. Prices, valuation, the stock,
    # sales and purchase switches, the disabled flag, variants, barcodes, unit conversions, tax
    # and per-company defaults are not listed: not writable.
    "duplicate_keys": ["item_code"],
    "operations": {
        "create": {
            "roles": _roles("Stock Manager", "Item Manager"),
            "fields": ["item_code", "item_name", "item_group", "stock_uom", "description", "brand"],
            "child_fields": {},
            "confirm_always": True,
            "similar_by": {"field": "item_code", "slot": "item_code"},
        },
        "update": {
            "roles": _roles("Stock Manager", "Item Manager"),
            "fields": ["item_name", "item_group", "description", "brand"],
            "child_fields": {},
            "identity_slot": "item_code",
            "confirm": ["item_name", "item_group", "description", "brand"],
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


def get_duplicate_slot(doctype: Any, key: str) -> str:
    """
    The conversation slot that holds the value of duplicate key `key`. It is the key itself
    unless the registry maps it (a Quotation's party_name is asked for as the "customer").
    """
    spec = get_write_spec(doctype)
    slots = spec.get("duplicate_slots") if spec else None
    if isinstance(slots, dict) and isinstance(slots.get(key), str) and slots[key]:
        return slots[key]
    return key


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


def get_convert_sources(doctype: Any) -> Dict[str, Dict[str, Any]]:
    """
    The document types `doctype` may be created from: {source doctype: {method, source_docstatus,
    linked_by}}. A source without a mapping method is not usable and is left out.
    """
    op_spec = get_operation_spec(doctype, "convert")
    raw = op_spec.get("sources") if op_spec else None
    if not isinstance(raw, dict):
        return {}
    clean: Dict[str, Dict[str, Any]] = {}
    for source, spec in raw.items():
        if not (isinstance(source, str) and source and isinstance(spec, dict)):
            continue
        method = spec.get("method")
        if not (isinstance(method, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+", method)):
            continue
        linked = spec.get("linked_by")

        def text(key: str) -> Optional[str]:
            value = spec.get(key)
            return value if isinstance(value, str) and value.strip() else None

        clean[source] = {
            "method": method,
            "source_docstatus": spec.get("source_docstatus") if isinstance(spec.get("source_docstatus"), int) else 1,
            "linked_by": linked if isinstance(linked, dict) and all(isinstance(linked.get(k), str) for k in ("child_doctype", "parent_doctype", "field")) else None,
            # For a document that is done in parts (delivered, received): the percentage field that
            # reaches 100 when nothing is left, the per-row quantity already done, and the word for it.
            "done_percent_field": text("done_percent_field"),
            "done_qty_field": text("done_qty_field"),
            "done_label": text("done_label"),
            # Wording shown in the confirmation: a standing note about the new document, and what to say
            # about documents already made from the same source.
            "note": text("note"),
            "existing_note": text("existing_note"),
        }
    return clean


def get_convert_spec(doctype: Any, source_doctype: Any) -> Optional[Dict[str, Any]]:
    """The mapping spec for making `doctype` from `source_doctype` (case-insensitive), or None."""
    if not isinstance(source_doctype, str) or not source_doctype.strip():
        return None
    wanted = source_doctype.strip().lower()
    for source, spec in get_convert_sources(doctype).items():
        if source.lower() == wanted:
            return dict(spec, source_doctype=source)
    return None


def get_partial_rows(doctype: Any) -> Optional[Dict[str, str]]:
    """How a conversion into `doctype` may be limited to some rows, or None when it may not."""
    op_spec = get_operation_spec(doctype, "convert")
    raw = op_spec.get("partial_rows") if op_spec else None
    if isinstance(raw, dict) and all(isinstance(raw.get(k), str) and raw.get(k) for k in ("table", "match_on", "qty_field", "slot")):
        return {k: raw[k] for k in ("table", "match_on", "qty_field", "slot")}
    return None


def check_partial_rows(doctype: Any, rows: Any) -> WriteDecision:
    """
    Validate the rows a user named for a partial conversion: a list of 1 to MAX_ROW_EDITS objects, each
    with only an item code and a quantity above zero, no item named twice. Whether the item is on the
    source and whether the quantity is within what is pending is checked against the mapped draft.
    """
    spec = get_partial_rows(doctype)
    if spec is None:
        return _deny(f"{doctype!r} cannot be converted in part.", "This document cannot be created from only some of the items.")
    match_on, qty_field = spec["match_on"], spec["qty_field"]
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROW_EDITS:
        return _deny(f"Partial rows must be a list of 1 to {MAX_ROW_EDITS} items.", "Name the items, and how many of each, that should be included.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {match_on, qty_field}:
            return _deny(f"Each partial row may hold only {match_on} and {qty_field}.", f"Each item needs only an item code and a quantity.")
        ident = row.get(match_on)
        if not isinstance(ident, str) or not ident.strip():
            return _deny(f"A partial row needs a {match_on}.", "Each item needs an item code.")
        if ident.strip() in seen:
            return _deny(f"{ident!r} appears twice.", f"{ident.strip()} appears more than once.")
        seen.add(ident.strip())
        quantity = to_number(row.get(qty_field))
        if quantity is None or quantity <= 0:
            return _deny(f"{qty_field} for {ident!r} must be a number above zero.", f"The quantity for {ident.strip()} must be a number above zero.")
    return _allow()


def get_identity_slot(doctype: Any, operation: Any) -> Optional[str]:
    """The slot that names the target record of an operation, or None when it is "name"."""
    op_spec = get_operation_spec(doctype, operation)
    slot = op_spec.get("identity_slot") if op_spec else None
    return slot if isinstance(slot, str) and slot else None


def is_confirm_always(doctype: Any, operation: Any) -> bool:
    op_spec = get_operation_spec(doctype, operation)
    return bool(op_spec and op_spec.get("confirm_always") is True)


def get_similar_by(doctype: Any, operation: Any) -> Optional[Dict[str, str]]:
    """{field, slot} to look for similar existing records before a create, or None."""
    op_spec = get_operation_spec(doctype, operation)
    spec = op_spec.get("similar_by") if op_spec else None
    if isinstance(spec, dict) and isinstance(spec.get("field"), str) and isinstance(spec.get("slot"), str):
        return {"field": spec["field"], "slot": spec["slot"]}
    return None


def get_choices(doctype: Any, operation: Any) -> Dict[str, List[str]]:
    op_spec = get_operation_spec(doctype, operation)
    raw = op_spec.get("choices") if op_spec else None
    if not isinstance(raw, dict):
        return {}
    return {
        k: [v for v in vals if isinstance(v, str)]
        for k, vals in raw.items()
        if isinstance(k, str) and isinstance(vals, (list, tuple))
    }


def get_row_sync(doctype: Any, operation: Any) -> Dict[str, Dict[str, str]]:
    """Header fields ERPNext derives from child rows: {header field: {table, field}}."""
    op_spec = get_operation_spec(doctype, operation)
    raw = op_spec.get("row_sync") if op_spec else None
    if not isinstance(raw, dict):
        return {}
    clean: Dict[str, Dict[str, str]] = {}
    for header, spec in raw.items():
        if not (isinstance(header, str) and header and isinstance(spec, dict)):
            continue
        table, field = spec.get("table"), spec.get("field")
        if isinstance(table, str) and table and isinstance(field, str) and field:
            clean[header] = {"table": table, "field": field}
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

    for key, allowed_values in get_choices(doctype, operation).items():
        if key in fields and fields[key] not in allowed_values:
            return _deny(
                f"Field '{key}' must be one of {allowed_values}, got {fields[key]!r}.",
                f"'{key}' must be one of: {', '.join(allowed_values)}.",
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

    if operation == "convert":
        # `fields` carries the source: {"source_doctype": "Quotation"}. The converted document itself
        # is produced by ERPNext's mapper under the user's own session, so no field list applies.
        source = fields.get("source_doctype") if isinstance(fields, dict) else None
        if get_convert_spec(doctype, source) is None:
            label = source if isinstance(source, str) and source.strip() else "that document"
            return _deny(
                f"No convert source {source!r} registered for {doctype!r}.",
                f"The assistant cannot create a {str(doctype).strip()} from {label}.",
            )
        if isinstance(fields, dict) and fields.get("items") is not None:
            partial = check_partial_rows(doctype, fields["items"])
            if not partial:
                return partial

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

    if operation == "convert":
        # A conversion has no field list; what must hold is that the source is a registered one and
        # that the skill asks for the slot that names the source document.
        source = skill.get("source_doctype")
        if get_convert_spec(doctype, source) is None:
            problems.append(f"skill '{name}': '{source}' is not a registered source for converting into {doctype}")
        identity = get_identity_slot(doctype, operation)
        if identity and identity not in (skill.get("required_fields") or []):
            problems.append(f"skill '{name}': must require '{identity}', which names the document to convert")
        partial = get_partial_rows(doctype)
        allowed_optional = {partial["slot"]} if partial else set()
        extra = sorted(set(skill.get("optional_fields") or []) - allowed_optional)
        if extra:
            problems.append(f"skill '{name}': optional fields {extra} are not allowed on a conversion into {doctype}")
        return problems

    if operation in ("create", "update"):
        allowed = set(get_writable_fields(doctype, operation))
        declared = set(skill.get("required_fields") or []) | set(skill.get("optional_fields") or [])
        payload_structure = skill.get("payload_structure") or {}
        mappings = payload_structure.get("field_mappings") or {}
        declared |= set(skill.get("require_any_of") or [])
        declared -= {"name", "id", "filters"}
        identity = get_identity_slot(doctype, operation)
        if identity:
            # The slot that names the record is not a field the operation changes, but the skill
            # must ask for it, or an update would have no target.
            declared.discard(identity)
            if identity not in (skill.get("required_fields") or []):
                problems.append(f"skill '{name}': must require '{identity}', which names the record to {operation}")
        if operation == "create":
            # A create builds its payload from `fixed` and `field_mappings` ({document field: slot}),
            # so the slot a user is asked for may have another name than the field it fills
            # (a Quotation's "customer" slot fills party_name). What must be writable is the
            # document field each slot lands in, and every field the payload sets.
            slot_to_field = {slot: field for field, slot in mappings.items() if isinstance(field, str) and isinstance(slot, str)}
            declared = {slot_to_field.get(slot, slot) for slot in declared}
            declared |= {k for k in mappings if isinstance(k, str)}
            declared |= {k for k in (payload_structure.get("fixed") or {}) if isinstance(k, str)}
        undeclared = sorted(declared - allowed)
        if undeclared:
            problems.append(
                f"skill '{name}': fields {undeclared} are not writable in the registry for {operation} on {doctype}"
            )
    return problems
