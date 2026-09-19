---
name: purchase-order-lookup
intent: purchase-order-lookup
description: Look up a specific Purchase Order by its ID/name, by supplier name, or check whether a specific item has an existing purchase order. Use when the user asks to "show", "find", "get", "view", "check", or "look up" a purchase order, OR asks "does item X have a pending purchase order?" or "is there a PO for item Y?". NOT for creating, updating, cancelling, or submitting purchase orders.
allowed_roles:
  # Read-only lookup — purchasing team and admins
  - Purchase User
  - Purchase Manager
  - Stock User
  - Stock Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
  - Sales Manager
tool: get_list
doctype: Purchase Order
required_fields:
  - name
optional_fields:
  - supplier
  - item_code
query_parameters:
  filters:
    - ["name", "like", "%{name}%"]
  optional_filters:
    - field: supplier
      filter: ["supplier", "like", "%{supplier}%"]
  fields:
    - name
    - supplier
    - transaction_date
    - schedule_date
    - status
    - docstatus
    - grand_total
    - currency
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - name
  - supplier
  - item_code
keywords:
  - show purchase order
  - show me purchase order
  - find purchase order
  - get purchase order
  - look up purchase order
  - lookup purchase order
  - view purchase order
  - purchase order details
  - check purchase order
  - fetch purchase order
  - what is in purchase order
  - what's in purchase order
  - pending purchase order
  - existing purchase order
  - does item have purchase order
  - is there a purchase order
  - does it have a po
  - pending po
  - open po
  - po for item
  - purchase order for item
response_template: "Here are the details for Purchase Order: {name}"
error_template: "Failed to look up purchase order: {error}"
not_found_message: "No Purchase Order found matching '{name}'."
examples:
  - "Show me purchase order PUR-ORD-2026-00011"
  - "What's in purchase order PUR-ORD-2026-00013?"
  - "Does ITEM-DESK-001 have a pending purchase order?"
  - "Is there a PO for Ergonomic Standing Desk Pro?"
  - "Find purchase orders from Zuckerman Security"
---

# Purchase Order Lookup

## When to use
Trigger this skill when the user wants to VIEW the details of a Purchase Order, or check whether a pending PO exists for a given item.

Two primary sub-cases:
1. **Direct PO lookup**: User provides a PO name/ID or supplier name → use `get_list` with name/supplier filter.
2. **Item-based PO check**: User asks "does item X have a PO?" → use `get_list` on `Purchase Order Item` child table, filtering by `item_code`, to find any open PO lines for that item.

**IMPORTANT intent boundaries:**
- Use `create-purchase-order` when user says "create/make a new purchase order"
- Use `update-purchase-order` when user says "change/update a purchase order"
- Use `cancel-purchase-order` when user says "cancel a purchase order"
- Use `submit-purchase-order` when user says "submit/finalize a purchase order"

## Required information
- **name**: PO ID/name, supplier name, or item code to check for pending POs.

## ERPNext details
- Doctype: `Purchase Order`
- For item-based queries, also query `Purchase Order Item` child table filtered by `item_code`
- Key fields: `name`, `supplier`, `transaction_date`, `schedule_date`, `status`, `docstatus`, `grand_total`

## Tool
Tool name: `get_list` on DocType `Purchase Order`.

## Steps
1. Extract the PO ID, supplier name, or item code from the user message.
2. If item-based query: search `Purchase Order Item` child table for the item_code, then cross-reference with PO status.
3. Call `get_list` with appropriate filters.
4. Return formatted PO details or "no PO found" message.
