---
name: update-customer
intent: update-customer
description: Update an existing Customer in ERPNext, changing its customer group, territory, customer type or website. Use when the user asks to change, move, reassign, or update a customer's group, territory, type or website. This always asks for confirmation first and shows the old and new values. NOT for credit limits, payment terms, tax details or addresses (not supported here). NOT for renaming a customer. NOT for creating a customer (use create-customer). NOT for looking up a customer (use customer-lookup).
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Customer
required_fields:
  - customer_name
optional_fields:
  - customer_group
  - territory
  - customer_type
  - website
require_any_of:
  - customer_group
  - territory
  - customer_type
  - website
field_hints:
  customer_group: "the customer group to move the customer to"
  territory: "the territory to assign"
  customer_type: "Company, Individual or Partnership"
  website: "the website address"
payload_structure:
  fixed: {}
  field_mappings:
    customer_name: customer_name
    customer_group: customer_group
    territory: territory
    customer_type: customer_type
    website: website
  child_defaults: {}
defaults: []
validation_rules: []
field_values:
  customer_type:
    - Company
    - Individual
    - Partnership
keywords:
  - change customer group
  - update customer group
  - change the customer group
  - update the customer group
  - change customer territory
  - update customer territory
  - change the customer territory
  - update the customer territory
  - change customer website
  - update customer website
  - reassign customer
response_template: "Successfully updated Customer: {name}!"
error_template: "Failed to update customer: {error}"
examples:
  - "Update customer Acme Industries website to https://acme-industries.example"
  - "Change the customer group of Acme Industries to Government"
  - "Reassign customer Acme Industries to territory Rest Of The World"
  - "Change the customer Acme Industries to an Individual"
---

# Update Customer

## When to use
Trigger this skill when the user wants to change an existing Customer's group, territory, type or website.

**IMPORTANT intent boundaries:**
- Use `create-customer` to ADD a new customer
- Use `customer-lookup` to FIND, LIST or VIEW customers (including "what is their customer group")
- Credit limits, payment terms, tax details, addresses and renaming are not supported. Say so; do not try.

## Required information
- **customer_name**: the customer to change, as the user named it. It is matched against existing customers, and the user is asked to pick when several match.
- At least one change from the list below

## What can be changed
- **customer_group**: the group to move the customer to
- **territory**: the territory to assign
- **customer_type**: `Company`, `Individual` or `Partnership`
- **website**: the website address

Only fill a field when the user stated it. Never guess a group or territory.

## What happens
- The user is always asked to confirm, and sees each field with its old and new value.
- The new group or territory must already exist in ERPNext; ERPNext reports it if it does not.

## ERPNext details
- Doctype: `Customer`
- Customers are not submitted documents, so a change takes effect on the live record

## Tool
Tool name: `update_document` on DocType `Customer`.

## Steps
1. Extract the customer and every change the user asked for.
2. Resolve the customer against existing customers.
3. Show the old and new values and ask for confirmation.
4. Update the document via `update_document`.
5. Write verification confirms each change is saved.
