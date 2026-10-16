---
name: item-lookup
intent: item-lookup
description: Look up an ERPNext item master record (item name, item group, stock UOM, whether it is a stock item) by item code or partial code. Use when the user asks to find, look up, or get details of an item. NOT for stock quantities (use check-inventory).
allowed_roles:
  - Stock User
  - Stock Manager
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Item
required_fields:
  - item_code
optional_fields: []
query_parameters:
  filters:
    - ["item_code", "like", "%{item_code}%"]
  fields:
    - item_code
    - item_name
    - item_group
    - stock_uom
    - is_stock_item
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - item_code
keywords:
  - look up item
  - lookup item
  - find item
  - item details
  - search item
  - search for item
context_pronouns:
  entity_field: item_code
  triggers: ["item", "code", "uom", "group"]
response_template: |
  **Item Details: {item_code}**
  - **Item Name**: {item_name}
  - **Item Group**: {item_group}
  - **UOM**: {stock_uom}
not_found_message: "Item Lookup Result:\n\nUnfortunately, we were unable to find any item matching \"{item_code}\" in our database."
error_template: "Failed to look up item: {error}"
examples:
  - "Look up item SKU001"
  - "Find item details for SKU005"
  - "Search item Headphones"
---

# Item Lookup

## When to use
Trigger this skill when the user wants to look up or verify details about an item in ERPNext.

**IMPORTANT intent boundaries:**
- Use `check-inventory` for how much of an item is in stock
- Use THIS skill for item master details

## Required information
- **item_code**: Item code or part of it (matched with LIKE). Entity resolution maps an item name to its code.

## ERPNext details
- Doctype: `Item`
- Fields: `item_code`, `item_name`, `item_group`, `stock_uom`, `is_stock_item`

## Tool
Tool name: `get_list` with DocType `Item`.
