---
name: update-quotation
intent: update-quotation
description: Update an existing Quotation in ERPNext. It can change the date it is valid till, and the quantity or rate of items already on a Draft quotation. Use when the user asks to change, modify, extend, or update a quotation's validity or an item's quantity or price. NOT for adding or removing items. NOT for Sales Orders (use update-sales-order). NOT for submitting (use submit-quotation) or cancelling (use cancel-quotation). Changing item quantities or rates is confirmed first, with the old and new values.
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: update_document
doctype: Quotation
required_fields:
  - name
optional_fields:
  - valid_till
  - item_updates
require_any_of:
  - valid_till
  - item_updates
field_hints:
  item_updates: "which item code, and its new quantity and/or rate"
  valid_till: "the last day the quotation is valid, as YYYY-MM-DD"
payload_structure:
  fixed: {}
  field_mappings:
    name: name
    valid_till: valid_till
    item_updates: item_updates
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - update quotation
  - change quotation
  - modify quotation
  - extend quotation
  - update quote
  - change quote
  - update quotation validity
  - change quotation validity
  - update quotation quantity
  - change quotation quantity
  - update quotation rate
  - change quotation rate
response_template: "Successfully updated Quotation: {name}!"
error_template: "Failed to update quotation: {error}"
examples:
  - "Extend quotation SAL-QTN-2026-00007 until 2026-12-31"
  - "Update quotation SAL-QTN-2026-00008 valid till 2027-01-15"
  - "Change the quantity of ITEM-DESK-001 on quotation SAL-QTN-2026-00009 to 20"
  - "Set the rate of ITEM-CHAIR-002 on quotation SAL-QTN-2026-00010 to 7100"
---

# Update Quotation

## When to use
Trigger this skill when the user wants to change an existing Quotation in ERPNext: how long it is valid, or the quantity or rate of items that are already on a Draft quotation.

**IMPORTANT intent boundaries:**
- Use `update-sales-order` when changing a Sales Order
- Use `submit-quotation` when the user wants to SUBMIT/FINALIZE a quotation
- Use `cancel-quotation` when the user wants to CANCEL a quotation
- Use `quotation-lookup` to VIEW a quotation without changing it
- Adding items to a quotation or removing items from it is not supported here. Say so; do not try to do it.

## Required information
- **name**: The Quotation ID to update (e.g. SAL-QTN-2026-00007)
- At least one change from the list below

## What can be changed
- **valid_till**: the new last valid date as YYYY-MM-DD
- **item_updates**: changes to items that are already on the quotation. A list where each entry has:
  - `item_code`: the item code exactly as it appears on the quotation
  - `qty`: the new quantity, a number above zero (only if the user asked to change the quantity)
  - `rate`: the new rate per unit, zero or more (only if the user asked to change the price)

  Example: "change the quantity of ITEM-DESK-001 to 20 and the rate of ITEM-CHAIR-002 to 7100" gives
  `[{"item_code": "ITEM-DESK-001", "qty": 20}, {"item_code": "ITEM-CHAIR-002", "rate": 7100}]`.
  Only include `qty` or `rate` when the user stated it. Never guess a number.

## ERPNext details
- Doctype: `Quotation`
- Item quantity and rate changes are only accepted while the quotation is a Draft
- Every item row of the quotation is kept; only the named rows are changed

## Tool
Tool name: `update_document` on DocType `Quotation`.

## Steps
1. Extract the Quotation ID and every change the user asked for.
2. If the change is to item quantities or rates, show the old and new values and ask for confirmation.
3. Update the document via `update_document`.
4. Write verification confirms each change is saved.
