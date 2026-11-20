---
name: update-supplier
intent: update-supplier
description: Update an existing Supplier in ERPNext, changing its supplier group, country, supplier type or website. Use when the user asks to change, move, reassign, or update a supplier's group, country, type or website. This always asks for confirmation first and shows the old and new values. NOT for payment holds, payment terms, tax details, bank accounts or addresses (not supported here). NOT for renaming a supplier. NOT for creating a supplier (use create-supplier). NOT for looking up a supplier (use supplier-lookup).
allowed_roles:
  - Purchase User
  - Purchase Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Supplier
required_fields:
  - supplier
optional_fields:
  - supplier_group
  - country
  - supplier_type
  - website
require_any_of:
  - supplier_group
  - country
  - supplier_type
  - website
field_hints:
  supplier_group: "the supplier group to move the supplier to"
  country: "the country to set"
  supplier_type: "Company, Individual or Partnership"
  website: "the website address"
payload_structure:
  fixed: {}
  field_mappings:
    supplier: supplier
    supplier_group: supplier_group
    country: country
    supplier_type: supplier_type
    website: website
  child_defaults: {}
defaults: []
validation_rules: []
field_values:
  supplier_type:
    - Company
    - Individual
    - Partnership
keywords:
  - change supplier group
  - update supplier group
  - change the supplier group
  - update the supplier group
  - change supplier country
  - update supplier country
  - change the supplier country
  - update the supplier country
  - change supplier website
  - update supplier website
  - reassign supplier
response_template: "Successfully updated Supplier: {name}!"
error_template: "Failed to update supplier: {error}"
examples:
  - "Update supplier Summit Traders Ltd. website to https://summit-traders.example"
  - "Change the supplier group of Summit Traders Ltd. to Hardware"
  - "Set the country of supplier MA Inc. to Germany"
  - "Change the supplier MA Inc. to an Individual"
---

# Update Supplier

## When to use
Trigger this skill when the user wants to change an existing Supplier's group, country, type or website.

**IMPORTANT intent boundaries:**
- Use `create-supplier` to ADD a new supplier
- Use `supplier-lookup` to FIND, LIST or VIEW suppliers
- Use `update-purchase-order` to change a purchase order
- Payment holds, payment terms, tax details, bank accounts, addresses and renaming are not supported. Say so; do not try.

## Required information
- **supplier**: the supplier to change, as the user named it. It is matched against existing suppliers, and the user is asked to pick when several match.
- At least one change from the list below

## What can be changed
- **supplier_group**: the group to move the supplier to
- **country**: the country to set
- **supplier_type**: `Company`, `Individual` or `Partnership`
- **website**: the website address

Only fill a field when the user stated it. Never guess a group or country.

## What happens
- The user is always asked to confirm, and sees each field with its old and new value.
- The new group or country must already exist in ERPNext; ERPNext reports it if it does not.

## ERPNext details
- Doctype: `Supplier`
- Suppliers are not submitted documents, so a change takes effect on the live record

## Tool
Tool name: `update_document` on DocType `Supplier`.

## Steps
1. Extract the supplier and every change the user asked for.
2. Resolve the supplier against existing suppliers.
3. Show the old and new values and ask for confirmation.
4. Update the document via `update_document`.
5. Write verification confirms each change is saved.
