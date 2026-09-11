---
name: update-purchase-order
intent: update-purchase-order
description: Update fields on an existing Purchase Order in ERPNext. Use when the user asks to change, modify, or update a purchase order — for example, changing the schedule date or quantity. NOT for cancelling (use cancel-purchase-order) or submitting (use submit-purchase-order). No confirmation required (mirrors update-sales-order pattern — updates are reversible and non-destructive).
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
  - supplier
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
    on_fail: schedule_date must be strictly after transaction_date
keywords:
  - update purchase order
  - change purchase order
  - modify purchase order
  - change delivery date
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
  - "Change the delivery date on the purchase order for Summit Traders"
  - "Modify PO PUR-ORD-2026-00014 schedule date to next Friday"
---

# Update Purchase Order

## When to use
Trigger this skill when the user wants to update (modify) an existing Purchase Order document in ERPNext.

**IMPORTANT intent boundaries:**
- Use `cancel-purchase-order` when the user wants to CANCEL a PO
- Use `submit-purchase-order` when the user wants to SUBMIT/FINALIZE a PO
- Use `purchase-order-lookup` to VIEW a PO without changing it
- Use THIS skill for field updates (schedule_date, etc.)

## Required information
- **name**: The Purchase Order ID to update
- **schedule_date**: The new schedule/delivery date

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
