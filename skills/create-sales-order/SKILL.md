---
name: create-sales-order
intent: create-sales-order
description: Create a new Sales Order in ERPNext for a customer. Use when the user asks to create, place, or raise a sales order, or to sell items to a customer. NOT for Purchase Orders (use create-purchase-order for buying from a supplier). NOT for viewing or listing orders (use sales-order-lookup or sales-order-report).
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: create_document
doctype: Sales Order
required_fields:
  - customer
  - items
optional_fields:
  - transaction_date
  - delivery_date
payload_structure:
  fixed: {}
  field_mappings:
    customer: customer
    transaction_date: transaction_date
    delivery_date: delivery_date
    items: items
  child_defaults:
    delivery_date: "{delivery_date}"
defaults:
  - field: transaction_date
    value: today
  - field: delivery_date
    value: transaction_date + 7d
validation_rules:
  - field: delivery_date
    must_be_after: transaction_date
    on_fail: must be strictly after transaction_date
keywords:
  - create sales order
  - create a sales order
  - new sales order
  - place sales order
  - place a sales order
  - raise sales order
  - make sales order
  - sales order for customer
  - sell to customer
response_template: "Successfully created Sales Order: {name} for customer '{customer}'!"
error_template: "Failed to create sales order: {error}"
examples:
  - "Create a sales order for Grant Plastics Ltd. with SKU001 qty 5"
  - "Create sales order for Apex Logistics Global with 10 ITEM-DESK-001"
  - "Place a sales order for Beacon Healthcare Group: 4 ITEM-CHAIR-002 at 7500 each"
---

# Create Sales Order

## When to use
Trigger this skill when the user wants to create a new Sales Order document in ERPNext.

**IMPORTANT intent boundaries:**
- Use `create-purchase-order` when buying FROM a Supplier
- Use THIS skill when selling TO a Customer
- Use `submit-sales-order` / `cancel-sales-order` for state changes on EXISTING orders
- Use `sales-order-lookup` or `sales-order-report` to VIEW orders

## Required information
- **customer**: The customer name (entity resolution runs to confirm the exact customer)
- **items**: List of items to sell
  - Each item needs: `item_code`, `qty`
  - Optional: `rate`

## Optional information
- **transaction_date**: Order date (default: today)
- **delivery_date**: Delivery date (default: transaction_date + 7 days)

## ERPNext details
- Doctype: `Sales Order`
- Child table: `items` (each row needs `item_code`, `qty`, `delivery_date`, `warehouse`)
- Field: `company` (resolved to the default company if not specified)

## Tool
Tool name: `create_document` on DocType `Sales Order`.

## Steps
1. Resolve the customer via entity resolution.
2. Validate items (item_code and qty required per line).
3. Apply defaults (transaction_date = today, delivery_date = transaction_date + 7 days).
4. Validate delivery_date > transaction_date.
5. Create the Sales Order via `create_document`. It is saved as a Draft.
6. Write verification confirms the order was created with the expected customer and items.
