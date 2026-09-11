---
name: sales-order-report
intent: sales-order-report
description: >
  List and filter Sales Orders by date range and/or status. Use when the user asks to SEE a LIST of
  Sales Orders — "show me all orders from July", "what Sales Orders are in Draft", "list submitted
  orders", "show orders from last month", "find all orders between June and August". This returns
  individual order records, NOT computed metrics. NOT for looking up a single specific order by ID
  (use sales-order-lookup). NOT for best-seller/analytics (use sales-analytics-report). NOT for
  filtering by customer (use customer-order-history). This is a filtered LIST request.
allowed_roles:
  # Read-only list of sales orders
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - Stock User
  - System Manager
  - Administrator
tool: get_list
doctype: Sales Order
required_fields: []
optional_fields:
  - date_from
  - date_to
  - status
  - docstatus
query_parameters:
  filters: []
  optional_filters:
    - field: date_from
      filter: ["transaction_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["transaction_date", "<=", "{date_to}"]
    - field: status
      filter: ["status", "=", "{status}"]
    - field: docstatus
      filter: ["docstatus", "=", "{docstatus}"]
  fields:
    - name
    - customer
    - transaction_date
    - delivery_date
    - status
    - docstatus
    - grand_total
    - currency
defaults: []
validation_rules: []
keywords:
  - list sales orders
  - list orders
  - show all sales orders
  - show all orders
  - sales orders from
  - orders from last month
  - orders from july
  - orders from august
  - orders in draft
  - draft orders
  - submitted orders
  - cancelled orders
  - pending orders
  - orders between
  - orders this month
  - orders last month
  - orders last week
  - filter orders
  - orders by date
  - sales order list
  - all orders
response_template: "Sales Order List"
error_template: "Failed to retrieve sales orders: {error}"
not_found_message: "No Sales Orders found matching the specified filters."
examples:
  - "Show me all orders from July 2026"
  - "What Sales Orders are still in Draft?"
  - "List all submitted orders from last month"
  - "Show orders between June and August"
  - "Find all cancelled sales orders"
---

# Sales Order Report

## When to use
Trigger this skill when the user wants a **filtered list of Sales Orders** — by date range, status, or docstatus. This returns actual order records (not computed aggregates).

**IMPORTANT intent boundaries:**
- Use `sales-order-lookup` for a single, specific order by ID ("show me SAL-ORD-2026-00037")
- Use `customer-order-history` when filtering by a NAMED customer ("show orders from Acme Corp")
- Use `sales-analytics-report` for computed metrics ("best seller", "total revenue", ranked results)
- Use THIS skill for date/status-based list queries ("show all draft orders from July")

## Optional information
- **date_from**: Start date for transaction_date filter (e.g. "2026-07-01")
- **date_to**: End date for transaction_date filter (e.g. "2026-07-31")
- **status**: ERPNext status string (e.g. "Draft", "To Deliver and Bill", "Completed")
- **docstatus**: Numeric docstatus (0=Draft, 1=Submitted, 2=Cancelled)

## Temporal parsing
Convert relative date expressions to ISO dates:
- "July" → date_from: 2026-07-01, date_to: 2026-07-31
- "last month" → previous calendar month
- "this week" → current Mon-Sun range
- "last 4 months" → current date minus 4 months

## ERPNext details
- Doctype: `Sales Order`
- Key fields: `name`, `customer`, `transaction_date`, `delivery_date`, `status`, `docstatus`, `grand_total`

## Tool
Tool name: `get_list` on DocType `Sales Order` with date/status filters.

## Steps
1. Parse date range and status from user message.
2. Convert relative dates to absolute ISO date strings.
3. Call `get_list` with assembled filters.
4. Return list of matching Sales Orders with key fields.
