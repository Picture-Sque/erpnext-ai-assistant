---
name: customer-lookup
intent: customer-lookup
description: Look up an ERPNext customer record by name, ID, or partial match, or list all customers when no specific customer is requested. Use when the user asks "show all customers", "show me all customer", "list customers", "find customer X", "who is X", needs customer details, or when another skill needs to confirm a customer exists.
allowed_roles:
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Customer
required_fields: []
optional_fields:
  - customer_name
query_parameters:
  filters: []
  optional_filters:
    - field: customer_name
      filter: ["customer_name", "like", "%{customer_name}%"]
  fields:
    - customer_name
    - customer_group
    - territory
    - email_id
    - mobile_no
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - customer_name
keywords:
  - "look up customer"
  - "lookup customer"
  - "find customer"
  - "customer details"
  - "search customer"
  - "customer group"
  - "customer info"
  - "customer"
  - "show all customer"
  - "show me all customer"
  - "show all customers"
  - "show me all customers"
  - "list customers"
  - "list all customers"
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
  - "Show me all customers"
  - "Show me all customer"
  - "List all customers"
  - "Look up customer West View Software Ltd."
  - "Show me details of West View Software Ltd."
  - "Find customer Acme"
---

# Customer Lookup

## When to use
Trigger this skill when the user wants to list all customers, or find and confirm details about a specific customer, or when another skill needs a customer confirmed before proceeding.

## Required information
- None for listing all customers.
- **customer_name**: (Optional) customer name (full or partial), or customer ID for looking up a specific customer.

## ERPNext details
- Doctype: `Customer`
- Useful fields: `customer_name`, `customer_group`, `territory`, `mobile_no`, `email_id`, `disabled`
- Related doctype for contact details: `Contact` (linked via Dynamic Link)

## Tool
Tool name: `get_list` with DocType `Customer`.

## Steps
1. Call `get_list` on DocType `Customer` (with `customer_name` filter if specified, or without filters if user wants all customers).
2. If records are found, format and return customer details/table.
3. If looking up a specific customer and no match is found, state that no customer record was found.
