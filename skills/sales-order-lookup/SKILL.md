---
name: sales-order-lookup
intent: sales-order-lookup
description: Look up ONE specific Sales Order by its ID (e.g. SAL-ORD-2026-00037) and show its status, customer, items and totals. Use when the user asks to "show", "find", "get", "view", "check status of", or "look up" a sales order by ID. NOT for creating, updating, cancelling, or submitting orders; those have dedicated skills. NOT for listing several orders (use sales-order-report), a customer's orders (use customer-order-history), or analytics (use sales-analytics-report).
allowed_roles:
  # Read-only lookup, same broad access as customer-lookup
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - Stock User
  - System Manager
  - Administrator
tool: get_document
doctype: Sales Order
required_fields:
  - name
optional_fields: []
query_parameters:
  fields:
    - name
    - customer
    - transaction_date
    - delivery_date
    - status
    - docstatus
    - grand_total
    - currency
    - items
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - name
keywords:
  - show sales order
  - show me sales order
  - find sales order
  - get sales order
  - look up sales order
  - lookup sales order
  - view sales order
  - sales order details
  - check sales order
  - fetch sales order
  - retrieve sales order
  - open sales order
  - status of sales order
  - sales order status
response_template: "Here are the details for Sales Order: {name}"
error_template: "Failed to look up sales order: {error}"
not_found_message: "No Sales Order found matching '{name}'."
examples:
  - "Show me sales order SAL-ORD-2026-00037"
  - "What's the status of sales order SAL-ORD-2026-00038?"
  - "Get the details of SAL-ORD-2026-00040"
---

# Sales Order Lookup

## When to use
Trigger this skill when the user wants to VIEW the details or status of one specific Sales Order, identified by its ID. This is a **read-only** skill.

**IMPORTANT intent boundaries:**
- Use `cancel-sales-order` when the user says "cancel the order"
- Use `update-sales-order` when the user says "change/update the order"
- Use `submit-sales-order` when the user says "submit/finalize the order"
- Use `sales-order-report` when the user asks to LIST/filter multiple orders (e.g. "show me all draft orders from July")
- Use `customer-order-history` for all orders of a named customer
- Use `sales-analytics-report` when the user asks for best-selling, top customers, or aggregated metrics

## Required information
- **name**: The Sales Order ID (e.g. SAL-ORD-2026-00037)

## ERPNext details
- Doctype: `Sales Order`
- Key fields: `name`, `customer`, `transaction_date`, `delivery_date`, `status`, `docstatus`, `grand_total`, `items`

## Tool
Tool name: `get_document` on DocType `Sales Order`.

## Steps
1. Extract the Sales Order ID from the user's message.
2. Call `get_document` with that ID.
3. Return the order's status, customer, items and totals.
