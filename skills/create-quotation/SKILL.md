---
name: create-quotation
intent: create-quotation
description: Create a new Quotation in ERPNext for a customer, offering items at a price before an order is placed. Use when the user asks to create, raise, draft, or prepare a quotation or a quote for a customer. NOT for Sales Orders (use create-sales-order when the customer is placing an order). NOT for Purchase Orders. NOT for viewing a quotation (use quotation-lookup).
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Quotation
required_fields:
  - customer
  - items
optional_fields:
  - transaction_date
  - valid_till
payload_structure:
  fixed:
    quotation_to: Customer
  field_mappings:
    party_name: customer
    transaction_date: transaction_date
    valid_till: valid_till
    items: items
  child_defaults: {}
defaults:
  - field: transaction_date
    value: today
validation_rules: []
keywords:
  - create quotation
  - create a quotation
  - new quotation
  - make quotation
  - make a quotation
  - raise quotation
  - prepare quotation
  - quotation for customer
  - create quote
  - create a quote
  - quote for customer
response_template: "Successfully created Quotation: {name} for customer '{customer}'!"
error_template: "Failed to create quotation: {error}"
examples:
  - "Create a quotation for Grant Plastics Ltd. with SKU001 qty 5"
  - "Raise a quotation for Apex Logistics Global: 10 ITEM-DESK-001 at 7500 each"
  - "Make a quotation for Beacon Healthcare Group valid till 2026-12-31 for 4 ITEM-CHAIR-002"
---

# Create Quotation

## When to use
Trigger this skill when the user wants to create a new Quotation document in ERPNext: a price offer to a customer, before any order exists.

**IMPORTANT intent boundaries:**
- Use `create-sales-order` when the customer is PLACING an order (a Quotation is only an offer)
- Use `submit-quotation` / `cancel-quotation` for state changes on an EXISTING quotation
- Use `update-quotation` to change an existing quotation
- Use `quotation-lookup` to VIEW a quotation

## Required information
- **customer**: The customer name (entity resolution runs to confirm the exact customer)
- **items**: List of items to quote
  - Each item needs: `item_code`, `qty`
  - Optional: `rate` (the quoted price per unit; ERPNext uses the price list rate when it is left out)

## Optional information
- **transaction_date**: Quotation date as YYYY-MM-DD (default: today)
- **valid_till**: Last date the offer is valid, as YYYY-MM-DD. Leave it out unless the user gave one.

## ERPNext details
- Doctype: `Quotation`
- The customer is stored in `party_name` with `quotation_to` set to `Customer`
- Child table: `items` (each row needs `item_code`, `qty`; `rate` is optional)
- Field: `company` (resolved to the default company)

## Tool
Tool name: `create_document` on DocType `Quotation`.

## Steps
1. Resolve the customer via entity resolution.
2. Validate items (item_code and qty required per line).
3. Apply the default quotation date.
4. Create the Quotation via `create_document`. It is saved as a Draft.
5. Write verification confirms the quotation was created.
