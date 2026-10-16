---
name: customer-lookup
intent: customer-lookup
description: Look up an ERPNext customer record (customer group, territory, email, mobile) by name or partial name, or list all customers when no specific customer is requested. Use when the user asks "show all customers", "list customers", "find customer X", "who is X", or needs customer profile details. NOT for a customer's orders (use customer-order-history). NOT for ranking customers by sales (use sales-analytics-report).
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
  - "show all customer"
  - "show me all customer"
  - "show all customers"
  - "show me all customers"
  - "list customers"
  - "list all customers"
  - details of customer
  - their customer group
  - their territory
  - their email
  - their contact
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
error_template: "Failed to look up customer: {error}"
examples:
  - "Show me all customers"
  - "List all customers"
  - "Look up customer West View Software Ltd."
  - "Show me customer details for West View Software Ltd."
  - "Find customer Acme"
---

# Customer Lookup

## When to use
Trigger this skill when the user wants to list all customers, or find and confirm details about a specific customer.

**IMPORTANT intent boundaries:**
- Use `customer-order-history` for the orders a named customer placed
- Use `sales-analytics-report` for "who is our top customer?" style rankings
- Use THIS skill for customer profile details or the customer list

## Optional information
- **customer_name**: Customer name (full or partial) for a specific customer. Leave it out to list all customers.

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
