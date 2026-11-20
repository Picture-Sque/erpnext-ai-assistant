---
name: create-item
intent: create-item
description: Create a new Item (product or material) record in ERPNext with an item code and an item group. The item name, unit of measure, description and brand can be given. Use when the user asks to create or register a new item, product or material in the item master. This always asks for confirmation first and mentions existing items with a similar code. NOT for adding an item to an order or quotation. NOT for looking up items (use item-lookup). NOT for changing an existing item (use update-item). NOT for prices or stock levels.
allowed_roles:
  - Stock Manager
  - Item Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Item
required_fields:
  - item_code
  - item_group
optional_fields:
  - item_name
  - stock_uom
  - description
  - brand
payload_structure:
  fixed: {}
  field_mappings:
    item_code: item_code
    item_name: item_name
    item_group: item_group
    stock_uom: stock_uom
    description: description
    brand: brand
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - create item
  - create an item
  - create a new item
  - register item
  - register a new item
  - create product
  - create a new product
response_template: "Successfully created Item: {name} ('{item_code}')!"
error_template: "Failed to create item: {error}"
examples:
  - "Create item WIDGET-100 in the Products item group"
  - "Create a new item BOLT-M10 called Hex Bolt M10 in the Raw Material group, unit Nos"
  - "Register a new item CABLE-5M, item group Consumable, with description Five metre power cable"
---

# Create Item

## When to use
Trigger this skill when the user wants to create a brand new Item record in the ERPNext item master.

**IMPORTANT intent boundaries:**
- Use `item-lookup` to FIND or VIEW items
- Use `update-item` to CHANGE an existing item
- Use `check-inventory` for stock levels
- Use `create-sales-order`, `create-purchase-order` or `create-quotation` to put an existing item on a document
- Prices, valuation and stock settings cannot be set here.

## Required information
- **item_code**: the code of the new item, exactly as the user gave it. It becomes the item's ID and cannot be renamed here. Never shorten, change case or "correct" it.
- **item_group**: the item group the item belongs to. Ask for it if the user did not say. Never guess one.

## Optional information
- **item_name**: a display name (ERPNext uses the code when it is left out)
- **stock_uom**: the unit of measure (ERPNext uses the default from Stock Settings when it is left out). It cannot be changed later.
- **description**: a description, only if the user gave one
- **brand**: the brand, only if the user named one

Only fill an optional field when the user stated it.

## What happens
- An existing item with exactly this code stops the request: nothing is created.
- The user is always asked to confirm. The question lists the details and any existing items with a similar code.
- Prices, valuation, stock, sales and purchase switches, barcodes and tax cannot be set here.

## ERPNext details
- Doctype: `Item`
- Items are named by their item code
- The item group, unit and brand must already exist in ERPNext; ERPNext reports it if they do not

## Tool
Tool name: `create_document` on DocType `Item`.

## Steps
1. Extract the item code, the item group and any details the user gave.
2. Stop if an item with exactly that code exists.
3. Show the details and any similar items, and ask for confirmation.
4. Create the item via `create_document`.
5. Write verification confirms it was created.
