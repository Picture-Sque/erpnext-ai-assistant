---
name: customer-outstanding-balance
intent: customer-outstanding-balance
description: List the unpaid and partly paid submitted Sales Invoices of ONE named customer, with outstanding amounts. Use when the user asks what a customer currently owes, whether a customer has outstanding amounts, or about a customer's pending payments. NOT for all overdue invoices across customers (no cross-customer overdue report skill exists). NOT for customer profile details (use customer-lookup). NOT for a customer's orders (use customer-order-history).
allowed_roles:
  # Receivables are finance data; Sales User deliberately removed from the uploaded draft. Re-add if sales reps must see balances.
  - Accounts User
  - Accounts Manager
  - Sales Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Sales Invoice
required_fields:
  - customer
optional_fields:
  - date_from
  - date_to
query_parameters:
  filters:
    - ["customer", "=", "{customer}"]
    - ["docstatus", "=", "1"]
    - ["outstanding_amount", ">", "0"]
  optional_filters:
    - field: date_from
      filter: ["posting_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["posting_date", "<=", "{date_to}"]
  fields:
    - name
    - customer
    - posting_date
    - due_date
    - grand_total
    - outstanding_amount
    - status
    - currency
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - customer
  - date_from
  - date_to
keywords:
  - customer outstanding
  - outstanding amount
  - customer owes
  - pending payment
  - receivable
  - amount due
response_template: "**Outstanding Invoices: {customer}**"
error_template: "Failed to retrieve outstanding invoices: {error}"
not_found_message: "No outstanding invoices were found for '{customer}'. There are no unpaid submitted invoices on file."
examples:
  - "Does XYZ Bookstore have any outstanding amount?"
  - "How much does Grant Plastics Ltd. owe us?"
  - "Show pending payments for ABC Bookstore"
---

# Customer Outstanding Balance

## When to use
Trigger this skill for the unpaid invoices of one named customer.

**IMPORTANT intent boundaries:**
- Invoicing across all customers has no dedicated overdue report skill yet
- Use `customer-lookup` for profile details
- Use `customer-order-history` for orders (not invoices)

## Required information
- **customer**: customer name; entity resolution confirms the exact customer

## Optional information
- **date_from**, **date_to**: invoice posting date range

## ERPNext details
- Doctype: `Sales Invoice`; only submitted invoices with `outstanding_amount > 0` are returned, so drafts, cancelled and fully paid invoices never appear
- Credit notes (`is_return = 1`) have negative balances and are excluded by the `> 0` filter; mention that unapplied credit notes may reduce what the customer really owes
- Amounts are in each invoice's own `currency`; never add amounts of different currencies. Do not total by hand: show the rows, and when `has_more` is true say the list is incomplete and the total cannot be stated
- UPGRADE (phase 2): a grouped query returning the exact total per currency; until then this skill lists invoices only

## Tool
Tool name: `get_list` on DocType `Sales Invoice`.

## Steps
1. Resolve the customer.
2. Retrieve unpaid submitted invoices with the optional date range.
3. List each invoice with due date and outstanding amount; flag those past due date.
4. State when the list is truncated.
