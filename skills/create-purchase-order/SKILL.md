---
name: create-purchase-order
intent: create-purchase-order
description: Create a new Purchase Order in ERPNext for a supplier. Use when the user asks to create, place, or make a purchase order, or to order/procure items from a supplier. NOT for Sales Orders (use create-sales-order for that). Must go through entity resolution (supplier + items), Write RBAC, precondition validation, execution, write verification, and audit logging.
allowed_roles:
  # Creating a PO commits purchasing budget; restricted to purchasing roles
  - Purchase User
  - Purchase Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Purchase Order
required_fields:
  - supplier
  - items
optional_fields:
  - transaction_date
  - schedule_date
payload_structure:
  fixed:
    company: "LOREM Sample for internship"
  field_mappings:
    supplier: supplier
    transaction_date: transaction_date
    schedule_date: schedule_date
    items: items
  child_defaults:
    warehouse: "Stores - LS"
    schedule_date: "{schedule_date}"
defaults:
  - field: transaction_date
    value: today
  - field: schedule_date
    value: transaction_date + 7d
validation_rules:
  - field: schedule_date
    must_be_after: transaction_date
    on_fail: schedule_date must be strictly after transaction_date
keywords:
  - create purchase order
  - place purchase order
  - make purchase order
  - new purchase order
  - raise purchase order
  - purchase order for supplier
  - procure
  - reorder from supplier
  - order from supplier
  - buy from supplier
response_template: "Successfully created Purchase Order: {name} for supplier '{supplier}'!"
error_template: "Failed to create purchase order: {error}"
examples:
  - "Create a purchase order for Zuckerman Security Ltd. for ITEM-DESK-001, qty 50"
  - "Place a PO with MA Inc. for 20 UltraWide Monitors at 18500 each"
  - "Make a purchase order for Summit Traders for ITEM-ALUM-008 qty 100"
---

# Create Purchase Order

## When to use
Trigger this skill when the user wants to create a new Purchase Order document in ERPNext.

**IMPORTANT intent boundaries:**
- Use `create-sales-order` when selling TO a Customer
- Use THIS skill when buying FROM a Supplier
- Use `cancel-purchase-order` / `submit-purchase-order` for state changes on EXISTING POs
- Use `purchase-order-lookup` to CHECK if a PO exists

## Required information
- **Supplier**: The supplier name (entity resolution runs to confirm the exact supplier)
- **Items**: List of items to order (item_code, qty, and optionally rate per item)
  - Each item needs: `item_code`, `qty`
  - Optional: `rate`, `uom` (defaults to "Nos")

## Optional information
- **transaction_date**: Order date (default: today)
- **schedule_date**: Required delivery/delivery schedule date (default: today + 7 days)

## ERPNext details
- Doctype: `Purchase Order`
- Child table: `items` (each row needs `item_code`, `qty`, `schedule_date`, `warehouse`)
- Fixed field: `company` = "LOREM Sample for internship"

## Tool
Tool name: `create_document` on DocType `Purchase Order`.

## Steps
1. Resolve supplier via entity resolution.
2. Validate items (item_code and qty required per line).
3. Apply defaults (transaction_date = today, schedule_date = today + 7d).
4. Validate schedule_date > transaction_date.
5. Create the Purchase Order via `create_document`.
6. Write verification confirms the PO was created with the expected supplier and items.
