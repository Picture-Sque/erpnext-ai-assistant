---
name: purchase-order-lookup
intent: purchase-order-lookup
description: Look up Purchase Orders by PO ID/name, by supplier, or by an item they contain (e.g. "does item X have a pending purchase order?", "is there a PO for item Y?"). Use when the user asks to "show", "find", "get", "view", "check", or "look up" a purchase order. With no ID, supplier or item it lists recent purchase orders. NOT for creating, updating, cancelling, or submitting purchase orders.
allowed_roles:
  # Read-only lookup: purchasing, stock, accounts and admins
  - Purchase User
  - Purchase Manager
  - Stock User
  - Stock Manager
  - Accounts User
  - Accounts Manager
  - Sales Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Purchase Order
required_fields: []
optional_fields:
  - name
  - supplier
  - item_code
query_parameters:
  filters: []
  optional_filters:
    - field: name
      filter: ["name", "like", "%{name}%"]
    - field: supplier
      filter: ["supplier", "like", "%{supplier}%"]
    # Child-table filter: POs with at least one row for this item.
    - field: item_code
      filter: ["Purchase Order Item", "item_code", "=", "{item_code}"]
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
  - status of purchase order
  - purchase order status
  - pending purchase order
  - existing purchase order
  - does item have purchase order
  - is there a purchase order
  - does it have a po
  - pending po
  - open po
  - po for item
  - purchase order for item
response_template: "Here are the matching Purchase Orders."
error_template: "Failed to look up purchase order: {error}"
not_found_message: "No matching Purchase Order was found."
examples:
  - "Show me purchase order PUR-ORD-2026-00011"
  - "What's in purchase order PUR-ORD-2026-00013?"
  - "Does ITEM-DESK-001 have a pending purchase order?"
  - "Is there a PO for Ergonomic Standing Desk Pro?"
  - "Find purchase orders from Zuckerman Security"
---

# Purchase Order Lookup

## When to use
Trigger this skill when the user wants to VIEW Purchase Orders: one PO by ID, the POs of a supplier, or whether a PO exists for a given item.

**IMPORTANT intent boundaries:**
- Use `create-purchase-order` when the user says "create/make a new purchase order"
- Use `update-purchase-order` when the user says "change/update a purchase order"
- Use `cancel-purchase-order` when the user says "cancel a purchase order"
- Use `submit-purchase-order` when the user says "submit/finalize a purchase order"

## Optional information
Extract whichever the user gave; each one narrows the search.
- **name**: PO ID or part of it (e.g. PUR-ORD-2026-00013)
- **supplier**: Supplier name (full or partial)
- **item_code**: Item code to find POs containing that item. Entity resolution maps an item name to its code.

## ERPNext details
- Doctype: `Purchase Order`
- Item search filters on the `Purchase Order Item` child table
- Key fields: `name`, `supplier`, `transaction_date`, `schedule_date`, `status`, `docstatus`, `grand_total`

## Tool
Tool name: `get_list` on DocType `Purchase Order`.

## Steps
1. Extract the PO ID, supplier name and/or item code from the user message.
2. Call `get_list` with a filter for each value given.
3. Return the matching POs with their status, or the not-found message.
