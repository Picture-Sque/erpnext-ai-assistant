---
name: check-inventory
intent: check-inventory
description: Check current stock levels for an item in ERPNext, optionally at a specific warehouse. Use when the user asks "how much X do we have", "is X in stock", "check inventory for X", or similar.
allowed_roles:
  - Stock User
  - Stock Manager
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: list_doctype
doctype: Bin
required_fields:
  - item_code
optional_fields:
  - warehouse
query_parameters:
  filters:
    - ["item_code", "=", "{item_code}"]
  optional_filters:
    - field: warehouse
      filter: ["warehouse", "like", "%{warehouse}%"]
  fields:
    - item_code
    - warehouse
    - actual_qty
defaults: []
validation_rules: []
keywords:
  - check stock
  - stock for
  - inventory
  - how much
  - do we have
  - available
  - in stock
  - is it in stock
  - check quantity
  - how many
context_pronouns:
  entity_field: item_code
  triggers: ["it", "available", "quantity", "stock", "how much", "how many"]
response_template: |
  **Inventory Check for {item_code}**
  - **Warehouse**: {warehouse} | **Actual Qty**: {actual_qty}
not_found_message: "**Inventory Check Result for {item_code}**\n\nThe stock check for {item_code} has been completed.\n\n**Result:** No stock found for the specified item."
examples:
  - "Check stock for SKU005"
  - "Do we have 40 Headphones (SKU009)?"
  - "How much Widget-A do we have in the Main Warehouse?"
---

# Check Inventory

## When to use
Trigger this skill when the user wants to know current stock quantity for one or more items, with or without a specific warehouse.

## Required information
- **Item code**
- **Warehouse** (optional)

## ERPNext details
- Source: `Bin` doctype (holds `item_code`, `warehouse`, `actual_qty`)

## Tool
Tool name: `list_doctype` with DocType `Bin`.
