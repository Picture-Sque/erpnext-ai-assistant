---
name: update-sales-order
intent: update-sales-order
description: Update an existing Sales Order in ERPNext. It can change the delivery date, the customer's PO number or PO date, and the quantity or rate of items already on a Draft order. Use when the user asks to change, modify, reschedule, or update a sales order's delivery date, customer PO reference, or an item's quantity or price. NOT for adding or removing items. NOT for Purchase Orders (use update-purchase-order). NOT for cancelling (use cancel-sales-order) or submitting (use submit-sales-order). Changing item quantities or rates is confirmed first, with the old and new values.
allowed_roles:
  # Updating fields is less destructive than cancelling, but still requires edit rights.
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Sales Order
required_fields:
  - name
optional_fields:
  - delivery_date
  - po_no
  - po_date
  - item_updates
require_any_of:
  - delivery_date
  - po_no
  - po_date
  - item_updates
field_hints:
  item_updates: "which item code, and its new quantity and/or rate"
  po_no: "the customer's PO number"
  po_date: "the customer's PO date as YYYY-MM-DD"
payload_structure:
  fixed: {}
  field_mappings:
    name: name
    delivery_date: delivery_date
    po_no: po_no
    po_date: po_date
    item_updates: item_updates
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - update sales order
  - change sales order
  - modify sales order
  - reschedule sales order
  - update so
  - change so delivery date
  - update sales order quantity
  - change sales order quantity
  - update sales order rate
  - change sales order rate
  - update sales order po number
  - change sales order po number
response_template: "Successfully updated Sales Order: {name}!"
error_template: "Failed to update sales order: {error}"
examples:
  - "Update sales order SAL-ORD-2026-00037 delivery date to 2026-10-15"
  - "Reschedule sales order SAL-ORD-2026-00038 to 2026-11-02"
  - "Change the delivery date of SAL-ORD-2026-00039 to next Friday"
  - "Change the quantity of ITEM-DESK-001 on SAL-ORD-2026-00040 to 12"
  - "Set the rate of ITEM-CHAIR-002 on SAL-ORD-2026-00041 to 7200"
  - "Update the customer PO number on SAL-ORD-2026-00042 to PO-8841"
---

# Update Sales Order

## When to use
Trigger this skill when the user wants to change an existing Sales Order in ERPNext: its delivery date, the customer's PO number or date, or the quantity or rate of items that are already on a Draft order.

**IMPORTANT intent boundaries:**
- Use `update-purchase-order` when changing a Purchase Order
- Use `cancel-sales-order` when the user wants to CANCEL an order
- Use `submit-sales-order` when the user wants to SUBMIT/FINALIZE an order
- Use `sales-order-lookup` to VIEW an order without changing it
- Adding items to an order or removing items from it is not supported here. Say so; do not try to do it.

## Required information
- **name**: The Sales Order ID to update (e.g. SAL-ORD-2026-00037)
- At least one change from the list below

## What can be changed
- **delivery_date**: the new delivery date as YYYY-MM-DD
- **po_no**: the customer's PO number, as text
- **po_date**: the customer's PO date as YYYY-MM-DD
- **item_updates**: changes to items that are already on the order. A list where each entry has:
  - `item_code`: the item code exactly as it appears on the order
  - `qty`: the new quantity, a number above zero (only if the user asked to change the quantity)
  - `rate`: the new rate per unit, zero or more (only if the user asked to change the price)

  Example: "change the quantity of ITEM-DESK-001 to 12 and the rate of ITEM-CHAIR-002 to 7200" gives
  `[{"item_code": "ITEM-DESK-001", "qty": 12}, {"item_code": "ITEM-CHAIR-002", "rate": 7200}]`.
  Only include `qty` or `rate` when the user stated it. Never guess a number.

## ERPNext details
- Doctype: `Sales Order`
- Item quantity and rate changes are only accepted while the order is a Draft
- Submitted orders may restrict which fields can change; ERPNext reports any validation error
- Every item row of the order is kept; only the named rows are changed

## Tool
Tool name: `update_document` on DocType `Sales Order`.

## Steps
1. Extract the Sales Order ID and every change the user asked for.
2. If the change is to item quantities or rates, show the old and new values and ask for confirmation.
3. Update the document via `update_document`.
4. Write verification confirms each change is saved.
