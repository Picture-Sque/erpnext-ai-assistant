---
name: low-stock-report
intent: low-stock-report
description: >
  Report all items that are below a stock quantity threshold across all warehouses. Use when the user
  asks "what's low on stock", "what items are running low", "what's out of stock", "what do we need to
  reorder", "show me items below X units", or similar. NOT for checking a single specific named item
  (use check-inventory for that). NOT for sales analytics. This is a list/report skill for stock alerts.
allowed_roles:
  # Stock reports are read-only — accessible to stock and purchase teams
  - Stock User
  - Stock Manager
  - Purchase User
  - Purchase Manager
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Bin
required_fields: []
optional_fields:
  - threshold
  - warehouse
query_parameters:
  filters:
    - ["actual_qty", "<=", "{threshold}"]
  optional_filters:
    - field: warehouse
      filter: ["warehouse", "like", "%{warehouse}%"]
  fields:
    - item_code
    - warehouse
    - actual_qty
    - ordered_qty
    - reserved_qty
defaults:
  # Default threshold: 10 units — flag when practical low-stock alert is triggered.
  # Special case: user says "out of stock" → threshold = 0 (handled in parameter extraction)
  - field: threshold
    value: "10"
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - threshold
  - warehouse
keywords:
  - low on stock
  - low stock
  - running low
  - out of stock
  - stock alert
  - reorder
  - need to reorder
  - which items are low
  - items below
  - stock below
  - what needs restocking
  - restocking
  - below threshold
  - zero stock
  - no stock left
  - empty stock
  - depleted
  - critical stock
  - low inventory
  - inventory alert
  - short on
response_template: "Low Stock Report"
error_template: "Failed to generate low stock report: {error}"
not_found_message: "No items found below the specified stock threshold. All items appear to be adequately stocked."
examples:
  - "What's low on stock?"
  - "Show me items running low"
  - "What items are completely out of stock?"
  - "List items below 5 units"
  - "What do we need to reorder?"
---

# Low Stock Report

## When to use
Trigger this skill for PORTFOLIO-LEVEL stock alerts — "what items are low?", "what's out of stock?", "show me everything below X units". This is a LIST query covering ALL items.

**IMPORTANT intent boundary:**
- Use `check-inventory` for a single, named item ("how much SKU001 do we have?")
- Use `low-stock-report` for a threshold-based list across all items ("what items are below 10 units?")

## Optional information
- **threshold**: The quantity cutoff (default: 10 units). Items with actual_qty <= threshold are returned.
  - If user says "out of stock" or "zero stock" → threshold = 0
  - If user says "below 5" → threshold = 5
  - If not specified → use default of 10
- **warehouse**: Optional warehouse filter. If not specified, shows all warehouses.

## ERPNext details
- Doctype: `Bin` (holds `item_code`, `warehouse`, `actual_qty`, `ordered_qty`, `reserved_qty`)
- Filter: `actual_qty <= threshold`

## Tool
Tool name: `get_list` with DocType `Bin`, filtering on `actual_qty`.

## Steps
1. Parse the threshold from the user's message (default 10, 0 for "out of stock").
2. Parse optional warehouse filter.
3. Call `get_list` on Bin with `actual_qty <= threshold`.
4. Return sorted list of low-stock items with their quantities.
