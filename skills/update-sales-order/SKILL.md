---
name: update-sales-order
intent: update-sales-order
description: Update the delivery date of an existing Sales Order in ERPNext. Use when the user asks to change, modify, reschedule, or update a sales order's delivery date. NOT for Purchase Orders (use update-purchase-order). NOT for cancelling (use cancel-sales-order) or submitting (use submit-sales-order).
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
  - delivery_date
optional_fields: []
payload_structure:
  fixed: {}
  field_mappings:
    name: name
    delivery_date: delivery_date
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
response_template: "Successfully updated Sales Order: {name}!"
error_template: "Failed to update sales order: {error}"
examples:
  - "Update sales order SAL-ORD-2026-00037 delivery date to 2026-10-15"
  - "Reschedule sales order SAL-ORD-2026-00038 to 2026-11-02"
  - "Change the delivery date of SAL-ORD-2026-00039 to next Friday"
---

# Update Sales Order

## When to use
Trigger this skill when the user wants to change the delivery date of an existing Sales Order in ERPNext.

**IMPORTANT intent boundaries:**
- Use `update-purchase-order` when changing a Purchase Order
- Use `cancel-sales-order` when the user wants to CANCEL an order
- Use `submit-sales-order` when the user wants to SUBMIT/FINALIZE an order
- Use `sales-order-lookup` to VIEW an order without changing it

## Required information
- **name**: The Sales Order ID to update (e.g. SAL-ORD-2026-00037)
- **delivery_date**: The new delivery date, as YYYY-MM-DD

## ERPNext details
- Doctype: `Sales Order`
- Updatable field: `delivery_date`
- Submitted orders may restrict which fields can change; ERPNext reports any validation error

## Tool
Tool name: `update_document` on DocType `Sales Order`.

## Steps
1. Extract the Sales Order ID and the new delivery date from the user's message.
2. Update the document via `update_document`.
3. Write verification confirms the new delivery date is saved.
