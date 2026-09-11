---
name: cancel-purchase-order
intent: cancel-purchase-order
description: Cancel an existing Purchase Order in ERPNext. Use when the user asks to cancel, abort, stop, or void a purchase order. This is a DESTRUCTIVE operation — requires confirmation naming the exact PO before executing. NOT for Sales Orders (use cancel-sales-order). NOT for updating a PO (use update-purchase-order).
allowed_roles:
  # Cancellation is destructive and reverses committed purchasing decisions.
  # Restricted to managers and admins — same reasoning as cancel-sales-order.
  # Purchase User is intentionally excluded: line-level purchasers cannot cancel committed POs.
  - Purchase Manager
  - System Manager
  - Administrator
tool: cancel_document
doctype: Purchase Order
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
  - cancel purchase order
  - cancel the purchase order
  - cancel po
  - cancel the po
  - abort purchase order
  - abort po
  - stop purchase order
  - stop po
  - void purchase order
  - void po
  - cancel procurement
response_template: "Successfully cancelled Purchase Order: {name}!"
error_template: "Failed to cancel purchase order: {error}"
examples:
  - "Cancel purchase order PUR-ORD-2026-00013"
  - "Abort the purchase order for Summit Traders"
  - "Cancel the PO for ITEM-DOCK-006"
---

# Cancel Purchase Order

## When to use
Trigger this skill when the user wants to CANCEL an existing Purchase Order in ERPNext.

⚠️ **This is a destructive operation** — the confirmation gate will pause execution and ask the user to confirm naming the exact PO before proceeding.

**IMPORTANT intent boundaries:**
- Use `cancel-sales-order` when cancelling a Sales Order (not a PO)
- Use `update-purchase-order` when the user wants to CHANGE a PO (not cancel)
- Use `purchase-order-lookup` to VIEW a PO without cancelling it
- Use THIS skill only when the user explicitly wants to CANCEL/VOID a PO

## Required information
- **name**: The Purchase Order ID to cancel (e.g. PUR-ORD-2026-00013)

## Preconditions (checked automatically by Step 5.5)
- The PO must exist
- The PO must be in Submitted (docstatus=1) status — only submitted POs can be cancelled
- Draft POs cannot be cancelled — they should be deleted or abandoned instead

## Confirmation gate
The existing Step 6 confirmation gate fires for this skill (operation = "cancel").
User must explicitly confirm with "Yes" before the cancellation executes.

## ERPNext details
- Doctype: `Purchase Order`
- Operation: sets `docstatus = 2` (cancelled)

## Tool
Tool name: `cancel_document` on DocType `Purchase Order`.
