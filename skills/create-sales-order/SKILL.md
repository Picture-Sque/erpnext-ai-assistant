---
name: create-sales-order
intent: create-sales-order
description: Create a new Sales Order in ERPNext for a customer. Use when the user asks to place an order, create a sales order, or sell items to a customer.
allowed_roles:
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: add_doctype
doctype: Sales Order
required_fields:
  - customer
  - items
optional_fields:
  - transaction_date
  - delivery_date
payload_structure:
  fixed:
    company: "LOREM Sample for internship"
  field_mappings:
    customer: customer
    transaction_date: transaction_date
    delivery_date: delivery_date
    items: items
  child_defaults:
    warehouse: "Stores - LS"
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
  - sales order
  - create order
  - place an order
  - sell
  - new sales order
response_template: "Successfully created Sales Order: {name} for customer '{customer}'!"
error_template: "Failed to create sales order: {error}"
examples:
  - "Create a sales order for Grant Plastics Ltd. with SKU001 qty 5"
  - "Create sales order for Acme Corp with 10 SKU001"
---

# Create Sales Order

## When to use
Trigger this skill when the user wants to create a new Sales Order document in ERPNext.

## Required information
- **Customer**
- **Items** (item code, quantity)
- **Delivery date** (optional — default to today + 7 days if not given)

## ERPNext details
- Doctype: `Sales Order`
- Child table: `items` (each row needs `item_code`, `qty`, `warehouse`)

## Tool
Tool name: `add_doctype` on DocType `Sales Order`.
