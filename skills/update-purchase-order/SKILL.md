---
name: update-purchase-order
intent: update-purchase-order
description: Update an existing Purchase Order in ERPNext. It can change the schedule (delivery) date, the transaction date, and the quantity or rate of items already on a Draft order. Use when the user asks to change, modify, reschedule, or update a purchase order's schedule or delivery date, or an item's quantity or price. NOT for adding or removing items. NOT for Sales Orders (use update-sales-order). NOT for cancelling (use cancel-purchase-order) or submitting (use submit-purchase-order). A plain date update of a draft needs no confirmation; updating a submitted PO is confirmed first; changing item quantities or rates is confirmed first, with the old and new values.
allowed_roles:
  # Updating a PO is less risky than cancelling/submitting; purchasing team + admins
  # NOTE: Broader than cancel/submit (same reasoning as update-sales-order vs cancel/submit-sales-order)
  - Purchase User
  - Purchase Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Purchase Order
required_fields:
  - name
optional_fields:
  - schedule_date
  - transaction_date
  - item_updates
require_any_of:
  - schedule_date
  - transaction_date
  - item_updates
field_hints:
  item_updates: "which item code, and its new quantity and/or rate"
payload_structure:
  fixed: {}
  field_mappings:
    name: name
    schedule_date: schedule_date
    transaction_date: transaction_date
    item_updates: item_updates
  child_defaults: {}
defaults: []
validation_rules:
  - field: schedule_date
    must_be_after: transaction_date
    on_fail: must be strictly after transaction_date
keywords:
  - update purchase order
  - change purchase order
  - modify purchase order
  - change schedule date
  - update po
  - change po
  - modify po
  - reschedule purchase order
  - reschedule po
  - update purchase order quantity
  - change purchase order quantity
  - update purchase order rate
  - change purchase order rate
response_template: "Successfully updated Purchase Order: {name}!"
error_template: "Failed to update purchase order: {error}"
examples:
  - "Update schedule date of PUR-ORD-2026-00013 to 2026-10-01"
  - "Reschedule purchase order PUR-ORD-2026-00020 to 2026-11-15"
  - "Modify PO PUR-ORD-2026-00014 schedule date to next Friday"
  - "Change the quantity of ITEM-DESK-001 on PUR-ORD-2026-00021 to 60"
  - "Set the rate of ITEM-ALUM-008 on PUR-ORD-2026-00022 to 310"
---

# Update Purchase Order

## When to use
Trigger this skill when the user wants to update (modify) an existing Purchase Order document in ERPNext: its dates, or the quantity or rate of items that are already on a Draft order.

**IMPORTANT intent boundaries:**
- Use `cancel-purchase-order` when the user wants to CANCEL a PO
- Use `submit-purchase-order` when the user wants to SUBMIT/FINALIZE a PO
- Use `purchase-order-lookup` to VIEW a PO without changing it
- Use `update-sales-order` when changing a Sales Order
- Use THIS skill for schedule date changes and item quantity or rate changes on a Purchase Order
- Adding items to an order or removing items from it is not supported here. Say so; do not try to do it.

## Required information
- **name**: The Purchase Order ID to update
- At least one change from the list below

## What can be changed
- **schedule_date**: The new schedule/delivery date, as YYYY-MM-DD
- **transaction_date**: The transaction date as YYYY-MM-DD (if being changed)
- **item_updates**: changes to items that are already on the order. A list where each entry has:
  - `item_code`: the item code exactly as it appears on the order
  - `qty`: the new quantity, a number above zero (only if the user asked to change the quantity)
  - `rate`: the new rate per unit, zero or more (only if the user asked to change the price)

  Example: "change the quantity of ITEM-DESK-001 to 60 and the rate of ITEM-ALUM-008 to 310" gives
  `[{"item_code": "ITEM-DESK-001", "qty": 60}, {"item_code": "ITEM-ALUM-008", "rate": 310}]`.
  Only include `qty` or `rate` when the user stated it. Never guess a number.

## ERPNext details
- Doctype: `Purchase Order`
- Updatable fields: `schedule_date` (primary), `transaction_date`, and quantity and rate of existing item rows
- Item quantity and rate changes are only accepted while the order is a Draft
- Every item row of the order is kept; only the named rows are changed
- Note: Submitted POs may have restricted edit fields — ERPNext will surface any validation errors

## Tool
Tool name: `update_document` on DocType `Purchase Order`.

## Steps
1. Extract the PO name/ID and every change the user asked for.
2. Validate schedule_date > transaction_date if both provided.
3. If the change is to item quantities or rates, show the old and new values and ask for confirmation.
4. Update the document via `update_document`.
5. Write verification confirms each change is saved.
