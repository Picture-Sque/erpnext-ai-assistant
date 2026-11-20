---
name: submit-quotation
intent: submit-quotation
description: Submit a draft Quotation in ERPNext to finalize it. Use when the user asks to submit, finalize, approve, or issue a quotation. This is a consequential operation and requires confirmation naming the exact Quotation before executing. NOT for Sales Orders (use submit-sales-order). NOT for creating a new quotation (use create-quotation).
allowed_roles:
  # Submission finalizes the offer; restricted to managers and admins, like submitting an order.
  - Sales Manager
  - System Manager
  - Administrator
tool: submit_document
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
  - submit quotation
  - submit the quotation
  - finalize quotation
  - approve quotation
  - issue quotation
response_template: "Successfully submitted Quotation: {name}!"
error_template: "Failed to submit quotation: {error}"
examples:
  - "Submit quotation SAL-QTN-2026-00007"
  - "Finalize the draft quotation SAL-QTN-2026-00008"
  - "Approve quotation SAL-QTN-2026-00009"
---

# Submit Quotation

## When to use
Trigger this skill when the user wants to SUBMIT (finalize/approve) a Draft Quotation in ERPNext. A submitted quotation can only be cancelled afterwards, not edited.

⚠️ **This is a consequential operation.** The confirmation gate pauses execution and asks the user to confirm the exact Quotation before it is submitted.

**IMPORTANT intent boundaries:**
- Use `submit-sales-order` when submitting a Sales Order (not a Quotation)
- Use `create-quotation` when the user wants to CREATE a new quotation
- Use `cancel-quotation` when the user wants to CANCEL a submitted quotation
- Use THIS skill only when the user wants to SUBMIT/FINALIZE a Draft Quotation

## Required information
- **name**: The Quotation ID to submit (e.g. SAL-QTN-2026-00007)

## Preconditions (checked automatically by Step 5.5)
- The Quotation must exist
- The Quotation must be a Draft (docstatus=0); only drafts can be submitted
- Already submitted or cancelled quotations cannot be submitted again

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit").
The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Quotation`
- Operation: sets `docstatus = 1` (submitted)

## Tool
Tool name: `submit_document` on DocType `Quotation`.
