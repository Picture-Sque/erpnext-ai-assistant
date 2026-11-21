---
name: submit-delivery-note
intent: submit-delivery-note
description: Submit a draft Delivery Note in ERPNext. Use when the user asks to submit, finalize, post or approve a delivery note. Submitting posts to stock, so it requires confirmation naming the exact Delivery Note before executing. NOT for creating a delivery note (use convert-sales-order-to-delivery-note). NOT for cancelling a delivery note.
allowed_roles:
  # Submitting posts to stock; limited to the manager role that owns it, so whoever creates the draft cannot also post it.
  - Stock Manager
  - System Manager
  - Administrator
tool: submit_document
doctype: Delivery Note
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
  - submit delivery note
  - submit the delivery note
  - finalize delivery note
  - approve delivery note
  - post delivery note
response_template: "Successfully submitted Delivery Note: {name}!"
error_template: "Failed to submit delivery note: {error}"
examples:
  - "Submit delivery note MAT-DN-2026-00001"
  - "Approve delivery note MAT-DN-2026-00002"
---

# Submit Delivery Note

## When to use
Trigger this skill when the user wants to SUBMIT a Draft Delivery Note in ERPNext. Submitting posts to stock. A submitted delivery note can only be cancelled afterwards, and cancelling is done in ERPNext, not here.

This is a consequential operation. The confirmation gate pauses execution and asks the user to confirm the exact Delivery Note before it is submitted.

**IMPORTANT intent boundaries:**
- Use `convert-sales-order-to-delivery-note` when the user wants to CREATE the delivery note
- Cancelling a delivery note is not done here. Say so if asked.

## Required information
- **name**: The Delivery Note ID to submit (for example MAT-DN-2026-00001)

## Preconditions (checked automatically by Step 5.5)
- The Delivery Note must exist
- The Delivery Note must be a Draft (docstatus=0)
- The user must hold Stock Manager. ERPNext's own permission check applies as well.

## Confirmation gate
The Step 6 confirmation gate fires for this skill (operation = "submit"). The user must explicitly reply "Yes" before the submission executes.

## ERPNext details
- Doctype: `Delivery Note`
- Operation: sets `docstatus = 1` (submitted); posts to stock

## Tool
Tool name: `submit_document` on DocType `Delivery Note`.
