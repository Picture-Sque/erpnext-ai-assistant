---
name: update-item
intent: update-item
description: Update an existing Item in ERPNext, changing its item name, item group, description or brand. Use when the user asks to change, move, rename the display name of, or update an item's name, group, description or brand. This always asks for confirmation first and shows the old and new values. NOT for prices, valuation, stock levels or reorder settings. NOT for changing the item code or unit of measure. NOT for creating an item (use create-item). NOT for looking up an item (use item-lookup).
allowed_roles:
  - Stock Manager
  - Item Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Item
required_fields:
  - item_code
optional_fields:
  - item_name
  - item_group
  - description
  - brand
require_any_of:
  - item_name
  - item_group
  - description
  - brand
field_hints:
  item_name: "the new display name"
  item_group: "the item group to move the item to"
  description: "the new description"
  brand: "the brand"
payload_structure:
  fixed: {}
  field_mappings:
    item_code: item_code
    item_name: item_name
    item_group: item_group
    description: description
    brand: brand
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - update item group
  - change item group
  - update the item group
  - change the item group
  - update item description
  - change item description
  - update the item description
  - change the item description
  - update item brand
  - change item brand
  - update item name
  - change item name
response_template: "Successfully updated Item: {name}!"
error_template: "Failed to update item: {error}"
examples:
  - "Change the item group of ITEM-BOLT-009 to Consumable"
  - "Update the description of ITEM-BOX-010 to Heavy duty double wall shipping box"
  - "Set the brand of ITEM-CHAIR-002 to Ergo"
  - "Change the display name of item ITEM-DESK-001 to Standing Desk"
---

# Update Item

## When to use
Trigger this skill when the user wants to change an existing Item's display name, item group, description or brand.

**IMPORTANT intent boundaries:**
- Use `create-item` to ADD a new item
- Use `item-lookup` to FIND or VIEW items
- Use `check-inventory` for stock levels
- Prices, valuation, stock and reorder settings, the item code and the unit of measure cannot be changed here. Say so; do not try.

## Required information
- **item_code**: the item to change, as the user named it. It is matched against existing items, and the user is asked to pick when several match.
- At least one change from the list below

## What can be changed
- **item_name**: the display name (the item code stays the same)
- **item_group**: the group to move the item to
- **description**: the description
- **brand**: the brand

Only fill a field when the user stated it. Never guess a group or brand.

## What happens
- The user is always asked to confirm, and sees each field with its old and new value.
- The new group or brand must already exist in ERPNext; ERPNext reports it if it does not.

## ERPNext details
- Doctype: `Item`
- Items are not submitted documents, so a change takes effect on the live record

## Tool
Tool name: `update_document` on DocType `Item`.

## Steps
1. Extract the item and every change the user asked for.
2. Resolve the item against existing items.
3. Show the old and new values and ask for confirmation.
4. Update the document via `update_document`.
5. Write verification confirms each change is saved.
