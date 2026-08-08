---
name: customer-lookup
intent: customer-lookup
description: Look up an ERPNext customer record by name, ID, or partial match. Use when the user asks "find customer X", "who is X", needs customer details, or when another skill needs to confirm a customer exists.
allowed_roles:
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: list_doctype
doctype: Customer
required_fields:
  - customer_name
optional_fields: []
query_parameters:
  filters:
    - ["customer_name", "like", "%{customer_name}%"]
  fields:
    - customer_name
    - customer_group
    - territory
    - email_id
    - mobile_no
defaults: []
validation_rules: []
keywords:
  - "look up customer"
  - "lookup customer"
  - "find customer"
  - "customer details"
  - "search customer"
  - "customer group"
  - "customer info"
  - "customer"
  - details of
  - details for
  - their customer group
  - their group
  - their territory
  - their email
  - their contact
  - who is their
  - what is their
  - what group
context_pronouns:
  entity_field: customer_name
  triggers: ["their", "they", "group", "details", "contact", "who", "what"]
response_template: |
  **Customer Details: {customer_name}**
  - **Customer Group**: {customer_group}
  - **Territory**: {territory}
  - **Email**: {email_id}
  - **Mobile**: {mobile_no}
not_found_message: "Customer Lookup Result:\n\nUnfortunately, we were unable to find any information on the customer \"{customer_name}\" in our database."
examples:
  - "Look up customer West View Software Ltd."
  - "Show me details of West View Software Ltd."
  - "Find customer Acme"
---

# Customer Lookup

## When to use
Trigger this skill when the user wants to find or confirm details about a customer, or when another skill needs a customer confirmed before proceeding.

## Required information
- **Search term**: customer name (full or partial), or customer ID

## ERPNext details
- Doctype: `Customer`
- Useful fields: `customer_name`, `customer_group`, `territory`, `mobile_no`, `email_id`, `disabled`
- Related doctype for contact details: `Contact` (linked via Dynamic Link)

## Tool
Tool name: `list_doctype` with DocType `Customer`.

## Steps
1. Call `list_doctype` with `customer_name` filter.
2. If match found, return formatted customer details.
3. If no match, state that no customer record was found.
