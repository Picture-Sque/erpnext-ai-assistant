---
name: cancel-sales-order
intent: cancel-sales-order
description: Cancel an existing Sales Order in ERPNext. Use when the user asks to cancel an order, abort an order, or stop a sales order.
allowed_roles:
  # Cancellation is destructive, so we restrict it to managers and admins
  - Sales Manager
  - System Manager
  - Administrator
tool: cancel_document
doctype: Sales Order
required_fields:
  - name
optional_fields: []
payload_structure:
  fixed: {}
  field_mappings:
    name: name
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - cancel sales order
  - cancel order
  - stop order
  - abort order
response_template: "Successfully cancelled Sales Order: {name}!"
error_template: "Failed to cancel sales order: {error}"
examples:
  - "Cancel sales order SO-0001"
  - "Please cancel the order for Acme Corp"
---

# Cancel Sales Order

## When to use
Trigger this skill when the user wants to cancel an existing Sales Order document in ERPNext.

## Required information
- **name**: The ID or resolvable reference of the Sales Order to cancel.

## ERPNext details
- Doctype: `Sales Order`

## Tool
Tool name: `cancel_document` on DocType `Sales Order`.
