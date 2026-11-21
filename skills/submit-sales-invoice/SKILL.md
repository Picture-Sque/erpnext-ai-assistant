---
name: submit-sales-invoice
intent: submit-sales-invoice
description: Submit a draft Sales Invoice in ERPNext. Use when the user asks to submit, finalize, post or approve a sales invoice. Submitting posts to accounts, so it requires confirmation naming the exact Sales Invoice before executing. NOT for creating a sales invoice (use convert-sales-order-to-sales-invoice). NOT for cancelling a sales invoice.
allowed_roles:
  # Submitting posts to accounts; limited to the manager role that owns it, so whoever creates the draft cannot also post it.
  - Accounts Manager
  - System Manager
  - Administrator
tool: submit_document
doctype: Sales Invoice
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
  - submit sales invoice
  - submit the sales invoice
  - finalize sales invoice
  - approve sales invoice
  - post sales invoice
response_template: "Successfully submitted Sales Invoice: {name}!"
error_template: "Failed to submit sales invoice: {error}"
examples:
  - "Submit sales invoice ACC-SINV-2026-00001"
  - "Approve sales invoice ACC-SINV-2026-00002"
---

# Submit Sales Invoice

## When to use
Trigger this skill when the user wants to SUBMIT a Draft Sales Invoice in ERPNext. Submitting posts to accounts. A submitted sales invoice can only be cancelled afterwards, and cancelling is done in ERPNext, not here.

This is a consequential operation. The confirmation gate pauses execution and asks the user to confirm the exact Sales Invoice before it is submitted.

**IMPORTANT intent boundaries:**
- Use `convert-sales-order-to-sales-invoice` when the user wants to CREATE the sales invoice
- Cancelling a sales invoice is not done here. Say so if asked.

## Required information
- **name**: The Sales Invoice ID to submit (for example ACC-SINV-2026-00001)

## Preconditions (checked automatically by Step 5.5)
- The Sales Invoice must exist
- The Sales Invoice must be a Draft (docstatus=0)
- The user must hold Accounts Manager. ERPNext's own permission check applies as well.

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit"). The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Sales Invoice`
- Operation: sets `docstatus = 1` (submitted); posts to accounts

## Tool
Tool name: `submit_document` on DocType `Sales Invoice`.
