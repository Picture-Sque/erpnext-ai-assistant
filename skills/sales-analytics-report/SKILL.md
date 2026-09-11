---
name: sales-analytics-report
intent: sales-analytics-report
description: >
  Generate computed sales analytics metrics using aggregation. Use this skill for ANY question asking for
  a best seller, worst seller, top customer, total revenue, sales by period, or ranked/sorted summary
  metrics over Sales Orders. This skill handles all variants: best-selling item by quantity (default),
  best-selling item by revenue, worst-selling/slow-moving items, top customer by order count or revenue,
  total sales for a period, and order counts by status. NOT for listing individual orders (use
  sales-order-report), NOT for looking up a specific order (use sales-order-lookup), NOT for checking
  stock (use check-inventory or low-stock-report). Use this when the query asks for "best", "top",
  "worst", "most", "highest", "total", "ranking", "which customer buys the most", etc.
allowed_roles:
  # Analytics is read-only — broad access for business intelligence
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - Stock User
  - Stock Manager
  - System Manager
  - Administrator
tool: aggregate
doctype: Sales Order Item
required_fields:
  - query_type
optional_fields:
  - metric
  - group_by
  - sort
  - date_from
  - date_to
  - limit
  - status_filter
query_parameters:
  # Computed dynamically by the LLM from query_type
  fields:
    - item_code
    - qty
    - amount
    - parent
defaults:
  # Default: aggregate submitted orders only (docstatus=1), best-selling by quantity, top 10
  - field: query_type
    value: best_selling_item_qty
  - field: limit
    value: "10"
  - field: status_filter
    value: submitted
validation_rules: []
keywords:
  - best selling item
  - best seller
  - best-selling
  - top selling item
  - most popular item
  - worst selling item
  - slow moving item
  - slow-moving
  - top customer
  - top clients
  - best customer
  - who buys the most
  - highest revenue
  - total sales
  - sales by month
  - sales total
  - sales summary
  - sales report
  - sales ranking
  - revenue report
  - most ordered
  - most units sold
  - which item sells most
  - which product sells
  - top products
  - order count by status
  - sales performance
  - our best
response_template: "Sales Analytics Report"
error_template: "Failed to generate sales analytics: {error}"
examples:
  - "What's our best-selling item?"
  - "Show me the top 5 items by revenue in the last 4 months"
  - "Which item has the most units sold?"
  - "Who's our top customer by order value?"
  - "What's our total sales for August 2026?"
  - "Show me slow-moving items"
  - "Which customer places the most orders with us?"
---

# Sales Analytics Report

## When to use
Trigger this skill for ALL computed/aggregate sales queries — best sellers, top customers, revenue totals, slow movers, period summaries. This skill maps one of several variants:

**Variant a — Best-selling item by QUANTITY** (default for "best seller"):
- group_by: item_code, metric: qty, aggregation_fn: sum, sort: qty desc
- Example: "What's our best-selling item?", "Most popular item last 4 months"

**Variant b — Best-selling item by REVENUE**:
- group_by: item_code, metric: amount, aggregation_fn: sum, sort: amount desc
- Example: "Which item generates the most revenue?", "Top items by sales value"

**Variant c — Worst-selling / slow-moving items**:
- Same as a/b but sort: asc
- Example: "Slow-moving items", "Worst sellers"

**Variant d — Top customer by order count or value**:
- group_by: customer (on Sales Order, not Sales Order Item), metric: grand_total or count
- Example: "Who's our top customer?", "Which customer buys the most from us?"

**Variant e — Total sales for a period**:
- Sum of grand_total with date range filter on Sales Order
- Example: "What's our total sales in August?", "Revenue last quarter"

**Variant f — Order count by status**:
- group_by: status or docstatus on Sales Order
- Example: "How many draft orders do we have?", "Order count by status"

## Default behavior
- Status filter: **Submitted orders only (docstatus=1)** — this is the ERP convention for "actual sales".
  If the user explicitly says "including drafts" or "all orders", include docstatus in [0, 1].
  # NOTE: This default is intentional and clearly commented here. To override, the parameter
  # extraction should set status_filter = "all" if user's intent includes non-submitted orders.
- Default metric: quantity (qty sum)
- Default limit: top 10 results
- Date range: last 4 months if user says "last 4 months", last 30 days for "recent", etc.

## Required information
- **query_type**: Inferred from user's question. One of: best_selling_item_qty, best_selling_item_revenue, worst_selling_item, top_customer_value, top_customer_count, total_sales_period, order_count_by_status

## Parameter extraction
Extract from the user's message:
- `query_type`: What kind of report? (best seller, top customer, total, worst seller, etc.)
- `metric`: "qty" (default) or "amount" for revenue-based ranking
- `group_by`: "item_code" (default for item-level) or "customer" for customer-level
- `sort`: "qty desc" (default best-seller), "qty asc" (worst-seller), "amount desc" (revenue), etc.
- `date_from`: Start date filter (e.g. "2026-05-11" for "last 4 months")
- `date_to`: End date filter (e.g. "2026-09-11" for current date)
- `limit`: Number of results (default 10)
- `status_filter`: "submitted" (default) or "all"

## ERPNext details
- Primary Doctype: `Sales Order Item` (for item-level aggregation)
- Also uses: `Sales Order` (for customer-level and period aggregation)
- Key fields: `item_code`, `qty`, `amount` (on Sales Order Item); `customer`, `grand_total`, `transaction_date` (on Sales Order)

## Tool
Tool name: `aggregate` on DocType `Sales Order Item` (or `Sales Order` for customer/period variants).

## Steps
1. Identify the query_type from the user's question.
2. Set group_by, metric, aggregation_fn, sort based on variant.
3. Apply date range and status filters if specified.
4. Call `aggregate` tool with the assembled parameters.
5. Return ranked results formatted clearly.
