---
name: convert-sales-order-to-delivery-note
intent: convert-sales-order-to-delivery-note
description: Create a Delivery Note from an existing submitted Sales Order in ERPNext, carrying over the customer and the items still to be delivered. Use when the user asks to create, make or prepare a delivery note from a sales order, to deliver a sales order, or to convert a sales order into a delivery note. The delivery note is saved as a Draft, nothing is posted to stock, and this always asks for confirmation first. NOT for submitting the delivery note. NOT for checking delivery status (use delivery-status-tracking). NOT for creating a sales order (use create-sales-order).
allowed_roles:
  # Creating the delivery note needs the rights ERPNext gives these roles on a Delivery Note.
  - Stock User
  - Stock Manager
  - Sales User
  - System Manager
  - Administrator
tool: convert_document
doctype: Delivery Note
source_doctype: Sales Order
required_fields:
  - sales_order
optional_fields: []
payload_structure:
  fixed: {}
  field_mappings:
    sales_order: sales_order
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - create delivery note from sales order
  - create a delivery note from sales order
  - make delivery note from sales order
  - make a delivery note from sales order
  - prepare delivery note from sales order
  - delivery note from sales order
  - create delivery note for sales order
  - create a delivery note for sales order
  - make delivery note for sales order
  - make a delivery note for sales order
  - convert sales order to delivery note
  - convert sales order to a delivery note
  - convert sales order into delivery note
  - convert sales order into a delivery note
  - sales order to delivery note
  - create delivery note
  - create a delivery note
  - make delivery note
  - make a delivery note
response_template: "Successfully created Delivery Note: {name} from Sales Order '{sales_order}'!"
error_template: "Failed to create a delivery note from the sales order: {error}"
examples:
  - "Create a delivery note from sales order SAL-ORD-2026-00088"
  - "Convert sales order SAL-ORD-2026-00086 to a delivery note"
  - "Make a delivery note for sales order SAL-ORD-2026-00084"
---

# Convert Sales Order to Delivery Note

## When to use
Trigger this skill when the user wants to prepare the delivery of an existing Sales Order by creating a Delivery Note from it.

**IMPORTANT intent boundaries:**
- Use `delivery-status-tracking` to CHECK whether something was delivered
- Use `create-sales-order` or `convert-quotation-to-sales-order` to create the order itself
- Submitting a delivery note is not done here. Say so if asked: the draft has to be submitted in ERPNext.

## Required information
- **sales_order**: the ID of the Sales Order to deliver, as the user gave it (for example SAL-ORD-2026-00088). Never guess one.

## What happens
- The Sales Order must be submitted, and must still have something left to deliver.
- ERPNext itself builds the delivery note from the order: the customer, the items, and the quantities still pending. Nothing is taken from the conversation except which order to use.
- The user is always asked to confirm. The question shows the customer and the pending items, and mentions any delivery notes that already exist for the same order.
- The delivery note is saved as a Draft. Nothing is posted to stock until it is submitted.

## ERPNext details
- Creates: `Delivery Note`, from the doctype `Sales Order`
- The link back to the order is kept on each delivery note row

## Tool
Tool name: `convert_document`, creating a `Delivery Note` from a `Sales Order`.

## Steps
1. Extract the sales order ID.
2. Check that the order exists, is submitted and has something left to deliver.
3. Show what will be created and any delivery notes that already exist, and ask for confirmation.
4. Create the Draft Delivery Note through ERPNext's own conversion.
