---
name: item-lookup
intent: item-lookup
description: Look up an ERPNext item record by item code, name, or item group.
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
examples:
  - "Look up item SKU001"
  - "Find item details for SKU005"
  - "Search item Headphones"
---

# Item Lookup

## When to use
Trigger this skill when the user wants to look up or verify details about an item in ERPNext.

## Required information
- **Item Code** or partial name

## ERPNext details
- Doctype: `Item`
- Fields: `item_code`, `item_name`, `item_group`, `stock_uom`, `is_stock_item`

## Tool
Tool name: `get_list` with DocType `Item`.
