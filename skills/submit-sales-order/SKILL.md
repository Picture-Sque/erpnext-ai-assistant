---
name: submit-sales-order
intent: submit-sales-order
description: Submit a draft Sales Order in ERPNext to finalize it. Use when the user asks to submit, finalize, confirm, or approve a sales order. This is a consequential operation and requires confirmation naming the exact Sales Order before executing. NOT for Purchase Orders (use submit-purchase-order). NOT for creating a new order (use create-sales-order).
allowed_roles:
  # Submission finalizes the order and affects ledgers; restricted to managers and admins.
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
  - submit the sales order
  - submit so
  - finalize sales order
  - confirm sales order
  - approve sales order
response_template: "Successfully submitted Sales Order: {name}!"
error_template: "Failed to submit sales order: {error}"
examples:
  - "Submit sales order SAL-ORD-2026-00037"
  - "Finalize the draft sales order SAL-ORD-2026-00041"
  - "Approve sales order SAL-ORD-2026-00042"
---

# Submit Sales Order

## When to use
Trigger this skill when the user wants to SUBMIT (finalize/approve) a Draft Sales Order in ERPNext. A submitted order can only be cancelled afterwards, not edited.

⚠️ **This is a consequential operation.** The confirmation gate pauses execution and asks the user to confirm the exact Sales Order before it is submitted.

**IMPORTANT intent boundaries:**
- Use `submit-purchase-order` when submitting a Purchase Order (not a Sales Order)
- Use `create-sales-order` when the user wants to CREATE a new order
- Use `cancel-sales-order` when the user wants to CANCEL a submitted order
- Use THIS skill only when the user wants to SUBMIT/FINALIZE a Draft Sales Order

## Required information
- **name**: The Sales Order ID to submit (e.g. SAL-ORD-2026-00037)

## Preconditions (checked automatically by Step 5.5)
- The Sales Order must exist
- The Sales Order must be a Draft (docstatus=0); only drafts can be submitted
- Already submitted or cancelled orders cannot be submitted again

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit").
The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Sales Order`
- Operation: sets `docstatus = 1` (submitted)

## Tool
Tool name: `submit_document` on DocType `Sales Order`.
