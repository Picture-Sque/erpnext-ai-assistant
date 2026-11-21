---
name: convert-purchase-order-to-purchase-receipt
intent: convert-purchase-order-to-purchase-receipt
description: Create a Purchase Receipt from an existing submitted Purchase Order in ERPNext, carrying over the supplier and the items still to be received. Use when the user asks to create, make or prepare a purchase receipt from a purchase order, or to convert a purchase order into a purchase receipt. The purchase receipt is saved as a Draft, nothing is posted to stock, and this always asks for confirmation first. NOT for submitting the purchase receipt. NOT for listing or reporting on purchase receipts (use purchase-receipt-report). NOT for creating a purchase order (use create-purchase-order).
allowed_roles:
  # Creating the receipt needs the rights ERPNext gives these roles on a Purchase Receipt.
  - Stock User
  - Stock Manager
  - Purchase User
  - System Manager
  - Administrator
tool: convert_document
doctype: Purchase Receipt
source_doctype: Purchase Order
required_fields:
  - purchase_order
optional_fields: []
payload_structure:
  fixed: {}
  field_mappings:
    purchase_order: purchase_order
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - create purchase receipt from purchase order
  - create a purchase receipt from purchase order
  - make purchase receipt from purchase order
  - make a purchase receipt from purchase order
  - prepare purchase receipt from purchase order
  - purchase receipt from purchase order
  - create purchase receipt for purchase order
  - create a purchase receipt for purchase order
  - make purchase receipt for purchase order
  - make a purchase receipt for purchase order
  - convert purchase order to purchase receipt
  - convert purchase order to a purchase receipt
  - convert purchase order into purchase receipt
  - convert purchase order into a purchase receipt
  - purchase order to purchase receipt
  - convert purchase order
  - create purchase receipt
  - create a purchase receipt
  - make purchase receipt
  - make a purchase receipt
response_template: "Successfully created Purchase Receipt: {name} from Purchase Order '{purchase_order}'!"
error_template: "Failed to create a purchase receipt from the purchase order: {error}"
examples:
  - "Create a purchase receipt from purchase order PUR-ORD-2026-00028"
  - "Convert purchase order PUR-ORD-2026-00027 to a purchase receipt"
  - "Make a purchase receipt for purchase order PUR-ORD-2026-00023"
---

# Convert Purchase Order to Purchase Receipt

## When to use
Trigger this skill when the user wants to record the receipt of goods against an existing Purchase Order by creating a Purchase Receipt from it.

**IMPORTANT intent boundaries:**
- Use `purchase-receipt-report` to LIST or REPORT on purchase receipts
- Use `purchase-order-lookup` to look at a purchase order
- Use `create-purchase-order` to create the order itself
- Submitting a purchase receipt is not done here. Say so if asked: the draft has to be submitted in ERPNext.

## Required information
- **purchase_order**: the ID of the Purchase Order the goods arrived against, as the user gave it (for example PUR-ORD-2026-00028). Never guess one.

## What happens
- The Purchase Order must be submitted, and must still have something left to receive.
- ERPNext itself builds the receipt from the order: the supplier, the items, and the quantities still pending. Nothing is taken from the conversation except which order to use.
- The user is always asked to confirm. The question shows the supplier and the pending items, and mentions any purchase receipts that already exist for the same order.
- The purchase receipt is saved as a Draft. Nothing is posted to stock until it is submitted.

## ERPNext details
- Creates: `Purchase Receipt`, from the doctype `Purchase Order`
- The link back to the order is kept on each purchase receipt row

## Tool
Tool name: `convert_document`, creating a `Purchase Receipt` from a `Purchase Order`.

## Steps
1. Extract the purchase order ID.
2. Check that the order exists, is submitted and has something left to receive.
3. Show what will be created and any purchase receipts that already exist, and ask for confirmation.
4. Create the Draft Purchase Receipt through ERPNext's own conversion.
