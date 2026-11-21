---
name: submit-purchase-invoice
intent: submit-purchase-invoice
description: Submit a draft Purchase Invoice in ERPNext. Use when the user asks to submit, finalize, post or approve a purchase invoice. Submitting posts to accounts, so it requires confirmation naming the exact Purchase Invoice before executing. NOT for creating a purchase invoice (use convert-purchase-order-to-purchase-invoice). NOT for cancelling a purchase invoice.
allowed_roles:
  # Submitting posts to accounts; limited to the manager role that owns it, so whoever creates the draft cannot also post it.
  - Accounts Manager
  - System Manager
  - Administrator
tool: submit_document
doctype: Purchase Invoice
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
  - submit purchase invoice
  - submit the purchase invoice
  - finalize purchase invoice
  - approve purchase invoice
  - post purchase invoice
response_template: "Successfully submitted Purchase Invoice: {name}!"
error_template: "Failed to submit purchase invoice: {error}"
examples:
  - "Submit purchase invoice ACC-PINV-2026-00001"
  - "Approve purchase invoice ACC-PINV-2026-00002"
---

# Submit Purchase Invoice

## When to use
Trigger this skill when the user wants to SUBMIT a Draft Purchase Invoice in ERPNext. Submitting posts to accounts. A submitted purchase invoice can only be cancelled afterwards, and cancelling is done in ERPNext, not here.

This is a consequential operation. The confirmation gate pauses execution and asks the user to confirm the exact Purchase Invoice before it is submitted.

**IMPORTANT intent boundaries:**
- Use `convert-purchase-order-to-purchase-invoice` when the user wants to CREATE the purchase invoice
- Cancelling a purchase invoice is not done here. Say so if asked.

## Required information
- **name**: The Purchase Invoice ID to submit (for example ACC-PINV-2026-00001)

## Preconditions (checked automatically by Step 5.5)
- The Purchase Invoice must exist
- The Purchase Invoice must be a Draft (docstatus=0)
- The user must hold Accounts Manager. ERPNext's own permission check applies as well.

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit"). The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Purchase Invoice`
- Operation: sets `docstatus = 1` (submitted); posts to accounts

## Tool
Tool name: `submit_document` on DocType `Purchase Invoice`.
