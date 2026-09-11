---
name: submit-purchase-order
intent: submit-purchase-order
description: Submit a draft Purchase Order in ERPNext to finalize/confirm it. Use when the user asks to submit, finalize, confirm, or approve a purchase order. This is a consequential operation — requires confirmation naming the exact PO before executing. NOT for Sales Orders (use submit-sales-order). NOT for creating a new PO (use create-purchase-order).
allowed_roles:
  # Submission finalizes the PO and commits budget/payment obligations.
  # Restricted to managers and admins — same reasoning as submit-sales-order.
  # Purchase User intentionally excluded from submission: approval authority requires manager role.
  - Purchase Manager
  - System Manager
  - Administrator
tool: submit_document
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
  - submit purchase order
  - submit po
  - finalize purchase order
  - finalize po
  - confirm purchase order
  - confirm po
  - approve purchase order
  - approve po
  - authorize purchase order
  - authorize po
response_template: "Successfully submitted Purchase Order: {name}!"
error_template: "Failed to submit purchase order: {error}"
examples:
  - "Submit purchase order PUR-ORD-2026-00013"
  - "Finalize the draft PO for Summit Traders"
  - "Approve the purchase order PUR-ORD-2026-00014"
---

# Submit Purchase Order

## When to use
Trigger this skill when the user wants to SUBMIT (finalize/approve) a Draft Purchase Order in ERPNext. Submission is irreversible without cancellation.

⚠️ **This is a consequential operation** — the confirmation gate will pause execution and ask the user to confirm naming the exact PO before proceeding.

**IMPORTANT intent boundaries:**
- Use `submit-sales-order` when submitting a Sales Order (not a PO)
- Use `create-purchase-order` when the user wants to CREATE a new PO
- Use `cancel-purchase-order` when the user wants to CANCEL a submitted PO
- Use THIS skill only when the user wants to SUBMIT/FINALIZE a Draft PO

## Required information
- **name**: The Purchase Order ID to submit (e.g. PUR-ORD-2026-00013)

## Preconditions (checked automatically by Step 5.5)
- The PO must exist
- The PO must be in Draft (docstatus=0) status — only Draft POs can be submitted
- Already-submitted or cancelled POs cannot be submitted again

## Confirmation gate
The existing Step 6 confirmation gate fires for this skill (operation = "submit").
User must explicitly confirm with "Yes" before the submission executes.

## ERPNext details
- Doctype: `Purchase Order`
- Operation: sets `docstatus = 1` (submitted)

## Tool
Tool name: `submit_document` on DocType `Purchase Order`.
