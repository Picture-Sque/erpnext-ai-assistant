---
name: submit-sales-order
intent: submit-sales-order
description: Submit a draft Sales Order in ERPNext. Use when the user asks to submit, finalize, or confirm an order.
allowed_roles:
  # Submission finalizes the order and affects ledgers, restricted to managers/admins
  - Sales Manager
  - System Manager
  - Administrator
tool: submit_document
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
  - submit sales order
  - submit order
  - finalize order
  - confirm sales order
response_template: "Successfully submitted Sales Order: {name}!"
error_template: "Failed to submit sales order: {error}"
examples:
  - "Submit sales order SO-0001"
  - "Please finalize the draft order for Acme Corp"
---

# Submit Sales Order

## When to use
Trigger this skill when the user wants to submit a draft Sales Order document in ERPNext to finalize it.

## Required information
- **name**: The ID or resolvable reference of the Sales Order to submit.

## ERPNext details
- Doctype: `Sales Order`

## Tool
Tool name: `submit_document` on DocType `Sales Order`.
