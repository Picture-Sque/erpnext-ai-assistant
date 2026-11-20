---
name: quotation-lookup
intent: quotation-lookup
description: Look up ONE specific Quotation by its ID (e.g. SAL-QTN-2026-00007) and show its status, customer, validity, items and totals. Use when the user asks to "show", "find", "get", "view", "check status of", or "look up" a quotation by ID. NOT for creating, updating, cancelling, or submitting quotations; those have dedicated skills. NOT for Sales Orders (use sales-order-lookup).
allowed_roles:
  # Read-only lookup, same access as looking up a sales order
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_document
doctype: Quotation
required_fields:
  - name
optional_fields: []
query_parameters:
  fields:
    - name
    - quotation_to
    - party_name
    - customer_name
    - transaction_date
    - valid_till
    - status
    - docstatus
    - grand_total
    - currency
    - items
defaults: []
validation_rules: []
keywords:
  - show quotation
  - show me quotation
  - find quotation
  - get quotation
  - look up quotation
  - lookup quotation
  - view quotation
  - quotation details
  - check quotation
  - open quotation
  - status of quotation
  - quotation status
response_template: "Here are the details for Quotation: {name}"
error_template: "Failed to look up quotation: {error}"
not_found_message: "No Quotation found matching '{name}'."
examples:
  - "Show me quotation SAL-QTN-2026-00007"
  - "What's the status of quotation SAL-QTN-2026-00008?"
  - "Get the details of quotation SAL-QTN-2026-00009"
---

# Quotation Lookup

## When to use
Trigger this skill when the user wants to VIEW the details or status of one specific Quotation, identified by its ID. This is a **read-only** skill.

**IMPORTANT intent boundaries:**
- Use `cancel-quotation` when the user says "cancel the quotation"
- Use `update-quotation` when the user says "change/update the quotation"
- Use `submit-quotation` when the user says "submit/finalize the quotation"
- Use `sales-order-lookup` for Sales Orders

## Required information
- **name**: The Quotation ID (e.g. SAL-QTN-2026-00007)

## ERPNext details
- Doctype: `Quotation`
- Key fields: `name`, `party_name`, `customer_name`, `transaction_date`, `valid_till`, `status`, `docstatus`, `grand_total`, `items`

## Tool
Tool name: `get_document` on DocType `Quotation`.

## Steps
1. Extract the Quotation ID from the user's message.
2. Call `get_document` with that ID.
3. Return the quotation's status, customer, validity, items and totals.
