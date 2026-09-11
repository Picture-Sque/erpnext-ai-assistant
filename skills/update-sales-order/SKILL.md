---
name: update-sales-order
intent: update-sales-order
description: Update fields on an existing Sales Order in ERPNext. Use when the user asks to change the delivery date or update an order.
allowed_roles:
  # Updating fields is less destructive than cancelling, but still requires edit rights
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
  - change delivery date
  - change order
  - modify sales order
response_template: "Successfully updated Sales Order: {name}!"
error_template: "Failed to update sales order: {error}"
examples:
  - "Update delivery date of SO-0001 to 2026-10-15"
  - "Change Acme Corp's order delivery to next Friday"
---

# Update Sales Order

## When to use
Trigger this skill when the user wants to update fields on an existing Sales Order document in ERPNext.

## Required information
- **name**: The ID or resolvable reference of the Sales Order to update.
- **delivery_date**: The new delivery date for the order.

## ERPNext details
- Doctype: `Sales Order`

## Tool
Tool name: `update_document` on DocType `Sales Order`.
