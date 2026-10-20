---
name: delivery-status-tracking
intent: delivery-status-tracking
description: Track delivery status of customer orders by listing Delivery Notes for a customer or Sales Order. Use when the user asks whether an order has been delivered, what the delivery status is, or wants the deliveries made to a customer. NOT for creating or editing orders (use create-sales-order or update-sales-order). NOT for the details of an order itself (use sales-order-lookup).
allowed_roles:
  - Sales User
  - Sales Manager
  - Stock User
  - Stock Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Delivery Note
required_fields: []
optional_fields:
  - customer
  - sales_order
  - date_from
  - date_to
  - status
query_parameters:
  filters: []
  optional_filters:
    - field: customer
      filter: ["customer", "like", "%{customer}%"]
    - field: sales_order
      filter: ["Delivery Note Item", "against_sales_order", "=", "{sales_order}"]
    - field: date_from
      filter: ["posting_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["posting_date", "<=", "{date_to}"]
    - field: status
      filter: ["status", "=", "{status}"]
  fields:
    - name
    - customer
    - posting_date
    - status
    - docstatus
    - is_return
    - per_billed
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - customer
  - sales_order
  - date_from
  - date_to
  - status
keywords:
  - delivery tracking
  - delivery status
  - order delivered
  - has the order been delivered
  - shipment status
  - fulfillment status
  - show delivery notes
  - deliveries for
response_template: "**Delivery Tracking**"
error_template: "Failed to retrieve delivery information: {error}"
not_found_message: "No matching Delivery Note was found for this request."
examples:
  - "Has the order for ABC Bookstore been delivered?"
  - "Has SAL-ORD-2026-00037 been delivered yet?"
  - "Show deliveries for XYZ Bookstore"
---

# Delivery Status Tracking

## When to use
Trigger this skill when the user wants to know what has been delivered against a customer or a Sales Order.

**IMPORTANT intent boundaries:**
- Use `sales-order-lookup` for the order's own details
- Use `create-sales-order` / `update-sales-order` / `cancel-sales-order` to change orders
- Use THIS skill for Delivery Note records

## Optional information
- **customer**: customer name (full or partial); goes through entity resolution
- **sales_order**: Sales Order ID; matched through the Delivery Note's item rows
- **date_from**, **date_to**, **status**

With none of these the result is the most recent Delivery Notes; say so, and offer to narrow by customer or Sales Order.

## ERPNext details
- Doctype: `Delivery Note`; the link to the Sales Order (`against_sales_order`) lives on the child table `Delivery Note Item`, not on the header, so it is filtered with a child-table filter
- `is_return = 1` marks a returned delivery (customer returns); describe it as a return
- `status`: Draft, To Bill, Completed, Return Issued, Cancelled, Closed
- ENGINE PREREQUISITES: whitelist `Delivery Note` and `Delivery Note Item`. Quick win: also add `per_delivered` and `delivery_status` to the fields of `sales-order-lookup`, which answers "has it been delivered?" without this skill

## Tool
Tool name: `get_list` on DocType `Delivery Note`.

## Steps
1. Identify the customer and/or Sales Order; resolve them.
2. Apply the filters and retrieve matching Delivery Notes.
3. Report each note's status, date and billing progress.
4. If nothing is found, say no delivery exists yet. Never describe that as a failed delivery.
