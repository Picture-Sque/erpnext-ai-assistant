---
name: create-customer
intent: create-customer
description: Create a new Customer record in ERPNext. Use when the user asks to create, add, register, or onboard a new customer. The customer's type, group, territory and website can be given. This always asks for confirmation first and mentions existing customers with a similar name. NOT for Suppliers. NOT for looking up or listing customers (use customer-lookup). NOT for changing an existing customer (use update-customer). NOT for placing an order or a quotation for a customer.
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Customer
required_fields:
  - customer_name
optional_fields:
  - customer_type
  - customer_group
  - territory
  - website
payload_structure:
  fixed: {}
  field_mappings:
    customer_name: customer_name
    customer_type: customer_type
    customer_group: customer_group
    territory: territory
    website: website
  child_defaults: {}
defaults:
  - field: customer_type
    value: Company
validation_rules: []
field_values:
  customer_type:
    - Company
    - Individual
    - Partnership
keywords:
  - create customer
  - create a customer
  - add customer
  - add a customer
  - add a new customer
  - register customer
  - register a customer
  - onboard customer
response_template: "Successfully created Customer: {name} ('{customer_name}')!"
error_template: "Failed to create customer: {error}"
examples:
  - "Create a customer called Northwind Traders"
  - "Add a new customer Blue Harbour Foods in the Commercial customer group, territory India"
  - "Register customer Priya Nair as an Individual"
---

# Create Customer

## When to use
Trigger this skill when the user wants to create a brand new Customer record in ERPNext.

**IMPORTANT intent boundaries:**
- Use `customer-lookup` to FIND, LIST or VIEW customers
- Use `update-customer` to CHANGE an existing customer
- Use `create-quotation` or `create-sales-order` to quote or sell to an existing customer
- Customers only. Not Suppliers.

## Required information
- **customer_name**: The full name of the new customer, exactly as the user gave it. Never shorten or "correct" it.

## Optional information
- **customer_type**: `Company`, `Individual` or `Partnership` (default: Company). Use Individual only when the user says it is a person.
- **customer_group**: the customer group, only if the user named one
- **territory**: the territory, only if the user named one
- **website**: the website, only if the user gave one

Only fill an optional field when the user stated it. Never guess a group or territory.

## What happens
- An existing customer with exactly this name stops the request: nothing is created.
- The user is always asked to confirm. The question lists the details and any existing customers with a similar name.
- Credit limits, accounts, tax details and payment terms cannot be set here.

## ERPNext details
- Doctype: `Customer`
- Customers are named by their customer name
- The group and territory must already exist in ERPNext; ERPNext reports it if they do not

## Tool
Tool name: `create_document` on DocType `Customer`.

## Steps
1. Extract the customer name and any details the user gave.
2. Stop if a customer with exactly that name exists.
3. Show the details and any similar customers, and ask for confirmation.
4. Create the customer via `create_document`.
5. Write verification confirms it was created.
