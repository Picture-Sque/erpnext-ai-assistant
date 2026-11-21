---
name: convert-sales-order-to-sales-invoice
intent: convert-sales-order-to-sales-invoice
description: Create a Sales Invoice from an existing submitted Sales Order in ERPNext, carrying over the customer and the items still to be billed. Use when the user asks to create, make or prepare a sales invoice from a sales order, to bill a sales order, or to convert a sales order into a sales invoice. The sales invoice is saved as a Draft, nothing is posted to accounts, and this always asks for confirmation first. NOT for submitting the sales invoice. NOT for checking billing or outstanding amounts (use customer-outstanding-balance). NOT for creating a sales order (use create-sales-order).
allowed_roles:
  # Creating the invoice needs the rights ERPNext gives these roles on a Sales Invoice.
  - Accounts User
  - Accounts Manager
  - Sales User
  - System Manager
  - Administrator
tool: convert_document
doctype: Sales Invoice
source_doctype: Sales Order
required_fields:
  - sales_order
optional_fields:
  - items
field_hints:
  items: "only if the user wants some of the items or fewer units: a list of the item code and the quantity to bill of each. Leave it out to bill everything that is left"
payload_structure:
  fixed: {}
  field_mappings:
    sales_order: sales_order
    items: items
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - create sales invoice from sales order
  - create a sales invoice from sales order
  - make sales invoice from sales order
  - make a sales invoice from sales order
  - prepare sales invoice from sales order
  - sales invoice from sales order
  - create sales invoice for sales order
  - create a sales invoice for sales order
  - bill sales order
  - invoice sales order
  - convert sales order to sales invoice
  - convert sales order to a sales invoice
  - convert sales order into sales invoice
  - sales order to sales invoice
  - create sales invoice
  - create a sales invoice
  - make sales invoice
  - make a sales invoice
  - to a sales invoice
  - into a sales invoice
  - to sales invoice
  - into sales invoice
response_template: "Successfully created Sales Invoice: {name} from Sales Order '{sales_order}'!"
error_template: "Failed to create a sales invoice from the sales order: {error}"
examples:
  - "Bill only 3 of ITEM-A on sales order SAL-ORD-2026-00088"
  - "Create a sales invoice from sales order SAL-ORD-2026-00088"
  - "Convert sales order SAL-ORD-2026-00088 to a sales invoice"
---

# Convert Sales Order to Sales Invoice

## When to use
Trigger this skill when the user wants to bill an existing Sales Order by creating a Sales Invoice from it.

**IMPORTANT intent boundaries:**
- Use `create-sales-order` to create the order itself
- Submitting an invoice is not done here. Say so if asked: the draft has to be submitted in ERPNext, because submitting posts to accounts.

## Required information
- **sales_order**: the ID of the Sales Order to bill, as the user gave it (for example SAL-ORD-2026-00088). Never guess one.

## Optional: only some of the items
- **items**: when the user wants only some of the items, or fewer units than are left, collect a list of the item code and the quantity wanted of each. It can only reduce: a quantity above what is left, or an item that is not on the sales order, is refused. Leave it out to bill everything that is left.

## What happens
- The Sales Order must be submitted, and must still have something left to bill.
- ERPNext itself builds the sales invoice from the order: the customer, the items, the quantities and the prices. Nothing is taken from the conversation except which order to use and, if asked, which items and how many.
- The user is always asked to confirm. The question shows the customer and the items, and mentions any sales invoices that already exist for the same order.
- The sales invoice is saved as a Draft. Nothing is posted to accounts until it is submitted.

## ERPNext details
- Creates: `Sales Invoice`, from the doctype `Sales Order`
- The link back to the order is kept on each invoice row
- Submitting is not offered: it posts to accounts, so who may do it from chat is left to the owners of that control

## Tool
Tool name: `convert_document`, creating a `Sales Invoice` from a `Sales Order`.
