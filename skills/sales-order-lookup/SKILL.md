---
name: sales-order-lookup
intent: sales-order-lookup
description: Look up a specific Sales Order by its ID/name, or by customer name + approximate date. Use when the user asks to "show", "find", "get", "view", "check status of", or "look up" a sales order. NOT for creating, updating, cancelling, or submitting orders — those have dedicated skills. Also NOT for listing multiple orders (use sales-order-report) or analytics (use sales-analytics-report).
allowed_roles:
  # Read-only lookup — same broad access as customer-lookup
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
optional_fields:
  - customer
  - transaction_date
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
keywords:
  - show sales order
  - show me sales order
  - show order
  - find sales order
  - get sales order
  - look up sales order
  - lookup sales order
  - view sales order
  - what is the status of
  - what's the status of
  - status of order
  - order details
  - sales order details
  - check sales order
  - fetch sales order
  - retrieve sales order
  - open sales order
response_template: "Here are the details for Sales Order: {name}"
error_template: "Failed to look up sales order: {error}"
not_found_message: "No Sales Order found matching '{name}'."
examples:
  - "Show me sales order SAL-ORD-2026-00037"
  - "What's the status of sales order SAL-ORD-2026-00038?"
  - "Find the order for Acme Corp from last week"
  - "Get details of SO-0001"
---

# Sales Order Lookup

## When to use
Trigger this skill when the user wants to VIEW the details or status of a specific Sales Order. This is a **read-only** skill. Route to this skill for phrases like "show me", "find", "look up", "check status of", "what's in", "view details of" a sales order.

**IMPORTANT intent boundaries:**
- Use `cancel-sales-order` when user says "cancel the order"
- Use `update-sales-order` when user says "change/update the order"  
- Use `submit-sales-order` when user says "submit/finalize the order"
- Use `sales-order-report` when user asks to LIST/filter multiple orders (e.g. "show me all draft orders from July")
- Use `sales-analytics-report` when user asks for best-selling, top customers, or aggregated metrics

## Required information
- **name**: The Sales Order ID (e.g. SAL-ORD-2026-00037) or a descriptive reference (customer + date) that can resolve to one.

## ERPNext details
- Doctype: `Sales Order`
- Key fields: `name`, `customer`, `transaction_date`, `delivery_date`, `status`, `docstatus`, `grand_total`, `items`

## Tool
Tool name: `get_document` on DocType `Sales Order`.

## Steps
1. Extract the Sales Order ID or reference from the user's message.
2. Call `get_document` with the resolved Sales Order name.
3. Return formatted order details including status, customer, items, and totals.
