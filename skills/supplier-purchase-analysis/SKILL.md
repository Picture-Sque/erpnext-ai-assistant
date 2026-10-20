---
name: supplier-purchase-analysis
intent: supplier-purchase-analysis
description: List the submitted Purchase Orders placed with ONE supplier over a period, with order values, to show purchase history with a printer, paper vendor or other supplier. Use when the user asks what was bought from a supplier, purchase history with a supplier, or order values for a supplier. NOT for finding one purchase order by ID or by item (use purchase-order-lookup). NOT for supplier master details.
allowed_roles:
  - Purchase User
  - Purchase Manager
  - Accounts User
  - Accounts Manager
  - Stock Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Purchase Order
required_fields:
  - supplier
optional_fields:
  - date_from
  - date_to
  - status
query_parameters:
  filters:
    - ["supplier", "=", "{supplier}"]
    - ["docstatus", "=", "1"]
  optional_filters:
    - field: date_from
      filter: ["transaction_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["transaction_date", "<=", "{date_to}"]
    - field: status
      filter: ["status", "=", "{status}"]
  fields:
    - name
    - supplier
    - transaction_date
    - status
    - grand_total
    - currency
    - per_received
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - supplier
  - date_from
  - date_to
  - status
keywords:
  - supplier purchase
  - supplier purchases
  - supplier purchase analysis
  - supplier purchase history
  - supplier history
  - how much did we purchase
  - purchases from
  - purchases for supplier
  - purchase history for supplier
  - purchase history with
response_template: "**Purchase History: {supplier}**"
error_template: "Failed to retrieve supplier purchases: {error}"
not_found_message: "No submitted purchase orders were found for '{supplier}' for the requested period."
examples:
  - "How much did we purchase from ABC Printers last year?"
  - "Show our purchase history with Summit Traders"
  - "What have we purchased from ABC Printers this quarter?"
---

# Supplier Purchase Analysis

## When to use
Trigger this skill for the purchase history with one supplier.

**IMPORTANT intent boundaries:**
- Use `purchase-order-lookup` for one PO by ID, or POs containing an item
- Use THIS skill for a supplier's submitted orders over a period

## Required information
- **supplier**: supplier name; entity resolution confirms the exact supplier

## Optional information
- **date_from**, **date_to**: `transaction_date` range; ask when the period is ambiguous
- **status**: ERPNext status such as "To Receive and Bill" or "Completed"

## ERPNext details
- Doctype: `Purchase Order`; only submitted orders (`docstatus = 1`) are returned, so drafts and cancelled orders are not counted as purchases
- `per_received` shows how much of each order has arrived
- Amounts are in each order's own `currency`; never add different currencies. Do not total by hand: if asked "how much", show the orders, and when `has_more` is true say the total cannot be given
- UPGRADE (phase 2): a grouped query for exact spend per supplier per currency and period

## Tool
Tool name: `get_list` on DocType `Purchase Order`.

## Steps
1. Resolve the supplier.
2. Apply period and status filters.
3. List orders with date, status, value and receipt progress.
4. State when the list is truncated.
