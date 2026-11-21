---
name: submit-purchase-receipt
intent: submit-purchase-receipt
description: Submit a draft Purchase Receipt in ERPNext. Use when the user asks to submit, finalize, post or approve a purchase receipt. Submitting posts to stock, so it requires confirmation naming the exact Purchase Receipt before executing. NOT for creating a purchase receipt (use convert-purchase-order-to-purchase-receipt). NOT for cancelling a purchase receipt.
allowed_roles:
  # Submitting posts to stock; limited to the manager role that owns it, so whoever creates the draft cannot also post it.
  - Stock Manager
  - System Manager
  - Administrator
tool: submit_document
doctype: Purchase Receipt
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
  - submit purchase receipt
  - submit the purchase receipt
  - finalize purchase receipt
  - approve purchase receipt
  - post purchase receipt
response_template: "Successfully submitted Purchase Receipt: {name}!"
error_template: "Failed to submit purchase receipt: {error}"
examples:
  - "Submit purchase receipt MAT-PRE-2026-00001"
  - "Approve purchase receipt MAT-PRE-2026-00002"
---

# Submit Purchase Receipt

## When to use
Trigger this skill when the user wants to SUBMIT a Draft Purchase Receipt in ERPNext. Submitting posts to stock. A submitted purchase receipt can only be cancelled afterwards, and cancelling is done in ERPNext, not here.

This is a consequential operation. The confirmation gate pauses execution and asks the user to confirm the exact Purchase Receipt before it is submitted.

**IMPORTANT intent boundaries:**
- Use `convert-purchase-order-to-purchase-receipt` when the user wants to CREATE the purchase receipt
- Cancelling a purchase receipt is not done here. Say so if asked.

## Required information
- **name**: The Purchase Receipt ID to submit (for example MAT-PRE-2026-00001)

## Preconditions (checked automatically by Step 5.5)
- The Purchase Receipt must exist
- The Purchase Receipt must be a Draft (docstatus=0)
- The user must hold Stock Manager. ERPNext's own permission check applies as well.

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit"). The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Purchase Receipt`
- Operation: sets `docstatus = 1` (submitted); posts to stock

## Tool
Tool name: `submit_document` on DocType `Purchase Receipt`.
