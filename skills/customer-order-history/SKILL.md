---
name: customer-order-history
intent: customer-order-history
description: >
  Show the full order history for a NAMED customer — all Sales Orders associated with a specific
  customer. Use when the user asks "show me all orders from [customer name]", "order history for
  Acme Corp", "what has [customer] ordered?", "list orders for [customer]". Requires a specific
  named customer to filter by. NOT for looking up customer details without orders (use
  customer-lookup). NOT for listing orders by date without a named customer (use sales-order-report).
  NOT for aggregated revenue/ranking metrics (use sales-analytics-report). The customer name goes
  through the existing disambiguation flow if ambiguous.
allowed_roles:
  # Customer-specific order history — same access as sales-order-report
  - Sales User
  - Sales Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Sales Order
required_fields:
  - customer
optional_fields:
  - date_from
  - date_to
  - status
query_parameters:
  filters:
    - ["customer", "like", "%{customer}%"]
  optional_filters:
    - field: date_from
      filter: ["transaction_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["transaction_date", "<=", "{date_to}"]
    - field: status
      filter: ["status", "=", "{status}"]
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
  - orders from customer
  - orders for customer
  - order history for
  - all orders from
  - show orders for
  - show me orders from
  - orders placed by
  - what has customer ordered
  - customer order history
  - customer history
  - purchase history
  - buying history
  - what did customer buy
  - what has customer bought
context_pronouns:
  entity_field: customer
  triggers: ["their", "they", "order history", "orders"]
response_template: "Order history for customer {customer}"
error_template: "Failed to retrieve order history: {error}"
not_found_message: "No Sales Orders found for customer '{customer}'."
examples:
  - "Show me all orders from Acme Corp"
  - "What has BlueStar Technologies ordered?"
  - "Order history for Apex Logistics Global"
  - "List all orders placed by Quantum Dynamics Ltd"
---

# Customer Order History

## When to use
Trigger this skill when the user wants to see ALL Sales Orders associated with a SPECIFIC, NAMED customer. This runs the existing entity disambiguation flow — if the customer name is ambiguous (e.g. "Acme" matches Acme Corp, Acme Corporation, Acme Industries), the system will ask the user to clarify.

**IMPORTANT intent boundaries:**
- Use `customer-lookup` when user wants customer *profile details* (group, territory, email) without asking about orders
- Use `sales-order-report` for date/status-based order lists WITHOUT a specific customer
- Use `sales-analytics-report` for "who is our top customer?" (ranking query, no specific named customer needed)
- Use THIS skill when a named customer is given and the user wants THEIR orders

## Required information
- **customer**: The customer name (full or partial). This feeds through entity resolution and disambiguation.

## Optional information
- **date_from**: Start date filter
- **date_to**: End date filter
- **status**: ERPNext order status filter

## ERPNext details
- Doctype: `Sales Order`
- Filter: `customer LIKE %{customer}%`

## Tool
Tool name: `get_list` on DocType `Sales Order` with `customer` filter.

## Steps
1. Extract customer name from user message.
2. Entity resolution runs via the existing disambiguation pipeline (Step 3/4).
3. Apply resolved customer name + optional date/status filters.
4. Call `get_list` on Sales Order.
5. Return all matching orders for that customer.
