---
name: check-inventory
intent: check-inventory
description: Check current stock levels for ONE named item in ERPNext, optionally at a specific warehouse. Use when the user asks "how much X do we have", "is X in stock", "check inventory for X", or similar. NOT for listing every item that is low or out of stock (use low-stock-report). NOT for item master details such as item group or UOM (use item-lookup).
allowed_roles:
  - Stock User
  - Stock Manager
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: get_list
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
follow_up_eligible: true
follow_up_slots:
  - item_code
  - warehouse
keywords:
  - check stock
  - stock for
  - stock of
  - stock level
  - stock levels
  - check inventory
  - inventory for
  - how much stock
  - in stock
  - is it in stock
  - check quantity
context_pronouns:
  entity_field: item_code
  triggers: ["it", "available", "quantity", "stock", "how much", "how many"]
response_template: |
  **Inventory Check for {item_code}**
  - **Warehouse**: {warehouse} | **Actual Qty**: {actual_qty}
not_found_message: "**Inventory Check Result for {item_code}**\n\nThe stock check for {item_code} has been completed.\n\n**Result:** No stock found for the specified item."
error_template: "Failed to check stock: {error}"
examples:
  - "Check stock for SKU005"
  - "Do we have 40 Headphones (SKU009)?"
  - "How much Widget-A do we have in the Main Warehouse?"
---

# Check Inventory

## When to use
Trigger this skill when the user wants to know the current stock quantity of a specific item, with or without a specific warehouse.

**IMPORTANT intent boundaries:**
- Use `low-stock-report` for a list of ALL items below a threshold ("what is low on stock?")
- Use `item-lookup` for item master details (name, group, UOM)
- Use THIS skill for the stock of one named item

## Required information
- **item_code**: The item code (e.g. ITEM-DESK-001). Entity resolution maps an item name to its code.

## Optional information
- **warehouse**: Partial or full warehouse name. Without it, every warehouse holding the item is listed.

## ERPNext details
- Source: `Bin` doctype (one row per item and warehouse: `item_code`, `warehouse`, `actual_qty`)
- No Bin rows means the item has no stock anywhere

## Tool
Tool name: `get_list` with DocType `Bin`.
