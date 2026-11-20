---
name: cancel-quotation
intent: cancel-quotation
description: Cancel an existing submitted Quotation in ERPNext. Use when the user asks to cancel, withdraw, or void a quotation. This is a DESTRUCTIVE operation and requires confirmation naming the exact Quotation before executing. NOT for Sales Orders (use cancel-sales-order). NOT for changing a quotation (use update-quotation).
allowed_roles:
  # Cancelling withdraws an offer that was issued; restricted to managers and admins.
  - Sales Manager
  - System Manager
  - Administrator
tool: cancel_document
doctype: Quotation
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
  - cancel quotation
  - cancel the quotation
  - withdraw quotation
  - void quotation
response_template: "Successfully cancelled Quotation: {name}!"
error_template: "Failed to cancel quotation: {error}"
examples:
  - "Cancel quotation SAL-QTN-2026-00007"
  - "Withdraw the quotation SAL-QTN-2026-00008"
  - "Please void quotation SAL-QTN-2026-00009"
---

# Cancel Quotation

## When to use
Trigger this skill when the user wants to CANCEL an existing Quotation in ERPNext.

⚠️ **This is a destructive operation.** The confirmation gate pauses execution and asks the user to confirm the exact Quotation before anything is cancelled.

**IMPORTANT intent boundaries:**
- Use `cancel-sales-order` when cancelling a Sales Order (not a Quotation)
- Use `update-quotation` when the user wants to CHANGE a quotation (not cancel it)
- Use `quotation-lookup` to VIEW a quotation without cancelling it
- Use THIS skill only when the user explicitly wants to CANCEL/VOID a Quotation

## Required information
- **name**: The Quotation ID to cancel (e.g. SAL-QTN-2026-00007)

## Preconditions (checked automatically by Step 5.5)
- The Quotation must exist
- The Quotation must be Submitted (docstatus=1); only submitted quotations can be cancelled
- Draft quotations cannot be cancelled

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "cancel").
The user must explicitly reply "Yes" before the cancellation executes.

## ERPNext details
- Doctype: `Quotation`
- Operation: sets `docstatus = 2` (cancelled)

## Tool
Tool name: `cancel_document` on DocType `Quotation`.
