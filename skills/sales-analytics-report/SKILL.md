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
  - date_from
  - date_to
  - limit
  - status_filter
query_parameters:
  # Columns shown for a result, in this order. Which ones appear depends on query_type.
  fields:
    - item_code
    - customer
    - transaction_date
    - status
    - qty
    - amount
    - grand_total
defaults:
  # Default: aggregate submitted orders only (docstatus=1), best-selling by quantity, top 10
  - field: query_type
    value: best_selling_item_qty
  - field: limit
    value: "10"
  - field: status_filter
    value: submitted
validation_rules: []
# Result columns later chain steps can reference ($stepN.<field>). The group column depends
# on query_type: item_code for item reports, customer for customer reports,
# transaction_date for total_sales_period, status for order_count_by_status.
outputs:
  - item_code
  - customer
  - transaction_date
  - status
  - qty
  - amount
  - grand_total
# Fields that accept only these values. Checked before the tool runs; anything else is
# reported as invalid instead of being sent to ERPNext.
field_values:
  query_type:
    - best_selling_item_qty
    - best_selling_item_revenue
    - worst_selling_item
    - top_customer_value
    - top_customer_count
    - total_sales_period
    - order_count_by_status
  status_filter:
    - submitted
    - draft
    - all
follow_up_eligible: true
follow_up_slots:
  - query_type
  - date_from
  - date_to
  - limit
  - status_filter
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
  - sales ranking
  - revenue report
  - most ordered
  - most units sold
  - which item sells most
  - which product sells
  - top products
  - order count by status
  - sales performance
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
Trigger this skill for ALL computed/aggregate sales queries: best sellers, top customers, revenue totals, slow movers, period summaries. Pick the `query_type` that matches the question; the system derives grouping, metric and sort from it. The variants are:

**`best_selling_item_qty`: best-selling item by QUANTITY** (default for "best seller"):
- Sum of qty per item_code, highest first
- Example: "What's our best-selling item?", "Most popular item last 4 months"

**`best_selling_item_revenue`: best-selling item by REVENUE**:
- Sum of amount per item_code, highest first
- Example: "Which item generates the most revenue?", "Top items by sales value"

**`worst_selling_item`: worst-selling / slow-moving items**:
- Sum of qty per item_code, lowest first
- Example: "Slow-moving items", "Worst sellers"

**`top_customer_value`: top customer by order VALUE**:
- Sum of grand_total per customer (on Sales Order), highest first
- Example: "Who's our top customer?", "Which customer buys the most from us?"

**`top_customer_count`: top customer by NUMBER of orders**:
- Count of orders per customer (on Sales Order), highest first
- Example: "Which customer places the most orders with us?"

**`total_sales_period`: total sales for a period**:
- Sum of grand_total per transaction_date, with the date range filter
- Example: "What's our total sales in August?", "Revenue last quarter"

**`order_count_by_status`: order count by status**:
- Count of orders per status on Sales Order, across drafts and submitted orders
- Example: "How many draft orders do we have?", "Order count by status"

## Default behavior
- Status filter: **Submitted orders only (docstatus=1)**, the ERP convention for "actual sales".
  - "including drafts" or "all orders" -> status_filter = "all" (drafts and submitted; cancelled orders are never counted)
  - "only drafts" -> status_filter = "draft"
  - order_count_by_status always counts across statuses (status_filter "submitted" is treated as "all")
- Default metric: quantity (qty sum)
- Default limit: top 10 results
- Date range: last 4 months if user says "last 4 months", last 30 days for "recent", etc.

## Required information
- **query_type**: Inferred from user's question. One of: best_selling_item_qty, best_selling_item_revenue, worst_selling_item, top_customer_value, top_customer_count, total_sales_period, order_count_by_status

## Parameter extraction
Extract from the user's message (do not extract metric, group_by or sort; query_type defines them):
- `query_type`: One of the values above (best seller by qty or revenue, worst seller, top customer by value or order count, total sales for a period, order count by status)
- `date_from`: Start date filter (e.g. "2026-05-11" for "last 4 months")
- `date_to`: End date filter (e.g. "2026-09-11" for current date)
- `limit`: Number of results (default 10)
- `status_filter`: "submitted" (default), "draft" or "all"

## ERPNext details
- Primary Doctype: `Sales Order Item` (for item-level aggregation)
- Also uses: `Sales Order` (for customer-level and period aggregation)
- Key fields: `item_code`, `qty`, `amount` (on Sales Order Item); `customer`, `grand_total`, `transaction_date` (on Sales Order)

## Tool
Tool name: `aggregate` on DocType `Sales Order Item` (or `Sales Order` for customer/period variants).

## Steps
1. Identify the query_type from the user's question.
2. The system sets group_by, metric, aggregation_fn and sort from the query_type.
3. Apply date range and status filters if specified.
4. Call `aggregate` tool with the assembled parameters.
5. Return ranked results formatted clearly.
