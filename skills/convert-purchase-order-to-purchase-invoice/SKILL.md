---
name: convert-purchase-order-to-purchase-invoice
intent: convert-purchase-order-to-purchase-invoice
description: Create a Purchase Invoice from an existing submitted Purchase Order in ERPNext, carrying over the supplier and the items still to be billed. Use when the user asks to create, make or prepare a purchase invoice from a purchase order, to bill a purchase order, or to convert a purchase order into a purchase invoice. The purchase invoice is saved as a Draft, nothing is posted to accounts, and this always asks for confirmation first. NOT for submitting the purchase invoice. NOT for listing or reporting on purchase invoices. NOT for creating a purchase order (use create-purchase-order).
allowed_roles:
  # Creating the invoice needs the rights ERPNext gives these roles on a Purchase Invoice.
  - Accounts User
  - Accounts Manager
  - Purchase User
  - System Manager
  - Administrator
tool: convert_document
doctype: Purchase Invoice
source_doctype: Purchase Order
required_fields:
  - purchase_order
optional_fields:
  - items
field_hints:
  items: "only if the user wants some of the items or fewer units: a list of the item code and the quantity to bill of each. Leave it out to bill everything that is left"
payload_structure:
  fixed: {}
  field_mappings:
    purchase_order: purchase_order
    items: items
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - create purchase invoice from purchase order
  - create a purchase invoice from purchase order
  - make purchase invoice from purchase order
  - make a purchase invoice from purchase order
  - prepare purchase invoice from purchase order
  - purchase invoice from purchase order
  - create purchase invoice for purchase order
  - create a purchase invoice for purchase order
  - bill purchase order
  - convert purchase order to purchase invoice
  - convert purchase order to a purchase invoice
  - convert purchase order into purchase invoice
  - purchase order to purchase invoice
  - create purchase invoice
  - create a purchase invoice
  - make purchase invoice
  - make a purchase invoice
  - to a purchase invoice
  - into a purchase invoice
  - to purchase invoice
  - into purchase invoice
response_template: "Successfully created Purchase Invoice: {name} from Purchase Order '{purchase_order}'!"
error_template: "Failed to create a purchase invoice from the purchase order: {error}"
examples:
  - "Bill only 4 of ITEM-A on purchase order PUR-ORD-2026-00045"
  - "Create a purchase invoice from purchase order PUR-ORD-2026-00045"
  - "Convert purchase order PUR-ORD-2026-00045 to a purchase invoice"
---

# Convert Purchase Order to Purchase Invoice

## When to use
Trigger this skill when the user wants to bill an existing Purchase Order by creating a Purchase Invoice from it.

**IMPORTANT intent boundaries:**
- Use `create-purchase-order` to create the order itself
- Submitting an invoice is not done here. Say so if asked: the draft has to be submitted in ERPNext, because submitting posts to accounts.

## Required information
- **purchase_order**: the ID of the Purchase Order to bill, as the user gave it (for example PUR-ORD-2026-00045). Never guess one.

## Optional: only some of the items
- **items**: when the user wants only some of the items, or fewer units than are left, collect a list of the item code and the quantity wanted of each. It can only reduce: a quantity above what is left, or an item that is not on the purchase order, is refused. Leave it out to bill everything that is left.

## What happens
- The Purchase Order must be submitted, and must still have something left to bill.
- ERPNext itself builds the purchase invoice from the order: the supplier, the items, the quantities and the prices. Nothing is taken from the conversation except which order to use and, if asked, which items and how many.
- The user is always asked to confirm. The question shows the supplier and the items, and mentions any purchase invoices that already exist for the same order.
- The purchase invoice is saved as a Draft. Nothing is posted to accounts until it is submitted.

## ERPNext details
- Creates: `Purchase Invoice`, from the doctype `Purchase Order`
- The link back to the order is kept on each invoice row
- Submitting is not offered: it posts to accounts, so who may do it from chat is left to the owners of that control

## Tool
Tool name: `convert_document`, creating a `Purchase Invoice` from a `Purchase Order`.
