---
name: stock-movement-analysis
intent: stock-movement-analysis
description: Show the stock ledger history (receipts, deliveries, returns, transfers) of ONE item over a period, optionally at one warehouse. Use when the user asks how the stock of a title changed, what movements occurred, or wants incoming and outgoing history. NOT for current stock quantity (use check-inventory). NOT for items that are low on stock (use low-stock-report).
allowed_roles:
  - Stock User
  - Stock Manager
  - Sales Manager
  - Purchase Manager
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Stock Ledger Entry
required_fields:
  - item_code
optional_fields:
  - date_from
  - date_to
  - warehouse
query_parameters:
  filters:
    - ["item_code", "=", "{item_code}"]
  optional_filters:
    - field: date_from
      filter: ["posting_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["posting_date", "<=", "{date_to}"]
    - field: warehouse
      filter: ["warehouse", "like", "%{warehouse}%"]
  fields:
    - item_code
    - warehouse
    - posting_date
    - posting_time
    - actual_qty
    - qty_after_transaction
    - voucher_type
    - voucher_no
  order_by: "posting_date desc, posting_time desc, name desc"
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - item_code
  - date_from
  - date_to
  - warehouse
keywords:
  - stock movement
  - stock history
  - inventory movement
  - stock transactions
  - how did stock change
  - how did the stock of
  - changes in stock of
  - stock ledger
  - movement history
response_template: "**Stock Movement: {item_code}**"
error_template: "Failed to retrieve stock movements: {error}"
not_found_message: "No stock movement records were found for '{item_code}' for the requested period."
examples:
  - "Show me the stock movement for Atomic Habits this month"
  - "Show stock history for item 1001"
  - "How did the inventory of SKU005 change since July?"
---

# Stock Movement Analysis

## When to use
Trigger this skill when the user wants the HISTORY of stock changes for one item.

**IMPORTANT intent boundaries:**
- Use `check-inventory` for the current quantity of an item
- Use `low-stock-report` for a list of items below a threshold
- Use THIS skill for what moved in and out, and when

## Required information
- **item_code**: ERPNext Item code; entity resolution maps a title to its code. If the title matches several editions or bindings, ask which one.

## Optional information
- **date_from**, **date_to**: ISO dates; ask when the period is ambiguous
- **warehouse**: partial or full warehouse name

## ERPNext details
- Doctype: `Stock Ledger Entry`; positive `actual_qty` = stock in, negative = stock out
- `voucher_type` tells the cause (Purchase Receipt, Delivery Note, Stock Entry, Sales Invoice, return documents)
- Keep the ledger rows exactly as returned; never add quantities by hand. If `has_more` is true, say only part of the history is shown
- ENGINE PREREQUISITES: whitelist `Stock Ledger Entry`; `get_list` needs `order_by` (posting_date desc) before "most recent first" can be promised

## Tool
Tool name: `get_list` on DocType `Stock Ledger Entry`.

## Steps
1. Resolve the item (and warehouse if given).
2. Apply date and warehouse filters.
3. Retrieve the ledger entries.
4. Present them with date, warehouse, quantity change, balance after, and the voucher that caused it.
5. State clearly when the list is truncated.
