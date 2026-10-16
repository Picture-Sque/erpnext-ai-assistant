---
name: cancel-sales-order
intent: cancel-sales-order
description: Cancel an existing submitted Sales Order in ERPNext. Use when the user asks to cancel, abort, stop, or void a sales order. This is a DESTRUCTIVE operation and requires confirmation naming the exact Sales Order before executing. NOT for Purchase Orders (use cancel-purchase-order). NOT for changing an order (use update-sales-order).
allowed_roles:
  # Cancellation is destructive and reverses a committed sale.
  # Restricted to managers and admins; Sales User is intentionally excluded.
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
  - cancel the sales order
  - cancel so
  - abort sales order
  - stop sales order
  - void sales order
response_template: "Successfully cancelled Sales Order: {name}!"
error_template: "Failed to cancel sales order: {error}"
examples:
  - "Cancel sales order SAL-ORD-2026-00037"
  - "Void the sales order SAL-ORD-2026-00038"
  - "Please cancel SO SAL-ORD-2026-00040"
---

# Cancel Sales Order

## When to use
Trigger this skill when the user wants to CANCEL an existing Sales Order in ERPNext.

⚠️ **This is a destructive operation.** The confirmation gate pauses execution and asks the user to confirm the exact Sales Order before anything is cancelled.

**IMPORTANT intent boundaries:**
- Use `cancel-purchase-order` when cancelling a Purchase Order (not a Sales Order)
- Use `update-sales-order` when the user wants to CHANGE an order (not cancel it)
- Use `sales-order-lookup` to VIEW an order without cancelling it
- Use THIS skill only when the user explicitly wants to CANCEL/VOID a Sales Order

## Required information
- **name**: The Sales Order ID to cancel (e.g. SAL-ORD-2026-00037)

## Preconditions (checked automatically by Step 5.5)
- The Sales Order must exist
- The Sales Order must be Submitted (docstatus=1); only submitted orders can be cancelled
- Draft orders cannot be cancelled

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "cancel").
The user must explicitly reply "Yes" before the cancellation executes.

## ERPNext details
- Doctype: `Sales Order`
- Operation: sets `docstatus = 2` (cancelled)

## Tool
Tool name: `cancel_document` on DocType `Sales Order`.
