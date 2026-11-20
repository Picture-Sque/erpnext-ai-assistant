---
name: create-supplier
intent: create-supplier
description: Create a new Supplier record in ERPNext. Use when the user asks to create, add, register, or onboard a new supplier or vendor. The supplier's type, group, country and website can be given. This always asks for confirmation first and mentions existing suppliers with a similar name. NOT for Customers (use create-customer). NOT for looking up or listing suppliers (use supplier-lookup). NOT for changing an existing supplier (use update-supplier). NOT for placing a purchase order with a supplier.
allowed_roles:
  - Purchase User
  - Purchase Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Supplier
required_fields:
  - supplier
optional_fields:
  - supplier_type
  - supplier_group
  - country
  - website
payload_structure:
  fixed: {}
  field_mappings:
    supplier_name: supplier
    supplier_type: supplier_type
    supplier_group: supplier_group
    country: country
    website: website
  child_defaults: {}
defaults:
  - field: supplier_type
    value: Company
validation_rules: []
field_values:
  supplier_type:
    - Company
    - Individual
    - Partnership
keywords:
  - create supplier
  - create a supplier
  - add supplier
  - add a supplier
  - add a new supplier
  - register supplier
  - register a supplier
  - onboard supplier
  - create vendor
  - add a vendor
response_template: "Successfully created Supplier: {name} ('{supplier}')!"
error_template: "Failed to create supplier: {error}"
examples:
  - "Create a supplier called Harbour Metals Pvt Ltd"
  - "Add a new supplier Greenfield Packaging in the Raw Material supplier group, country India"
  - "Register supplier Ravi Menon as an Individual"
---

# Create Supplier

## When to use
Trigger this skill when the user wants to create a brand new Supplier record in ERPNext.

**IMPORTANT intent boundaries:**
- Use `supplier-lookup` to FIND, LIST or VIEW suppliers
- Use `update-supplier` to CHANGE an existing supplier
- Use `create-purchase-order` to buy from an existing supplier
- Use `create-customer` for customers
- Suppliers only. Not Customers.

## Required information
- **supplier**: The full name of the new supplier, exactly as the user gave it. Never shorten or "correct" it.

## Optional information
- **supplier_type**: `Company`, `Individual` or `Partnership` (default: Company). Use Individual only when the user says it is a person.
- **supplier_group**: the supplier group, only if the user named one
- **country**: the country, only if the user named one
- **website**: the website, only if the user gave one

Only fill an optional field when the user stated it. Never guess a group or country.

## What happens
- An existing supplier with exactly this name stops the request: nothing is created.
- The user is always asked to confirm. The question lists the details and any existing suppliers with a similar name.
- Payment holds, tax details, payment terms and bank accounts cannot be set here.

## ERPNext details
- Doctype: `Supplier`
- Suppliers are named by their supplier name
- The group and country must already exist in ERPNext; ERPNext reports it if they do not

## Tool
Tool name: `create_document` on DocType `Supplier`.

## Steps
1. Extract the supplier name and any details the user gave.
2. Stop if a supplier with exactly that name exists.
3. Show the details and any similar suppliers, and ask for confirmation.
4. Create the supplier via `create_document`.
5. Write verification confirms it was created.
