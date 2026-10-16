---
name: update-purchase-order
intent: update-purchase-order
description: Update the schedule (delivery) date of an existing Purchase Order in ERPNext. Use when the user asks to change, modify, reschedule, or update a purchase order's schedule or delivery date. NOT for Sales Orders (use update-sales-order). NOT for cancelling (use cancel-purchase-order) or submitting (use submit-purchase-order). A plain update of a draft needs no confirmation; updating a submitted PO is confirmed first.
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
  - schedule_date
optional_fields:
  - transaction_date
payload_structure:
  fixed: {}
  field_mappings:
    name: name
    schedule_date: schedule_date
    transaction_date: transaction_date
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
response_template: "Successfully updated Purchase Order: {name}!"
error_template: "Failed to update purchase order: {error}"
examples:
  - "Update schedule date of PUR-ORD-2026-00013 to 2026-10-01"
  - "Reschedule purchase order PUR-ORD-2026-00020 to 2026-11-15"
  - "Modify PO PUR-ORD-2026-00014 schedule date to next Friday"
---

# Update Purchase Order

## When to use
Trigger this skill when the user wants to update (modify) an existing Purchase Order document in ERPNext.

**IMPORTANT intent boundaries:**
- Use `cancel-purchase-order` when the user wants to CANCEL a PO
- Use `submit-purchase-order` when the user wants to SUBMIT/FINALIZE a PO
- Use `purchase-order-lookup` to VIEW a PO without changing it
- Use `update-sales-order` when changing a Sales Order
- Use THIS skill for schedule date changes on a Purchase Order

## Required information
- **name**: The Purchase Order ID to update
- **schedule_date**: The new schedule/delivery date, as YYYY-MM-DD

## Optional information
- **transaction_date**: The transaction date (if being changed)

## ERPNext details
- Doctype: `Purchase Order`
- Updatable fields: `schedule_date` (primary), `transaction_date`
- Note: Submitted POs may have restricted edit fields — ERPNext will surface any validation errors

## Tool
Tool name: `update_document` on DocType `Purchase Order`.

## Steps
1. Extract the PO name/ID and new field values from the user's message.
2. Validate schedule_date > transaction_date if both provided.
3. Update the document via `update_document`.
4. Write verification confirms the updated fields are reflected.
