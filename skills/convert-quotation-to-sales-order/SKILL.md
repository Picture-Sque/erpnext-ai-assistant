---
name: convert-quotation-to-sales-order
intent: convert-quotation-to-sales-order
description: Create a Sales Order from an existing submitted Quotation in ERPNext, carrying over the customer and the quoted items and prices. Use when the user asks to convert a quotation into a sales order, to turn a quotation into an order, or to create a sales order from a quotation. The order is saved as a Draft and this always asks for confirmation first. NOT for creating a sales order from scratch (use create-sales-order). NOT for creating a quotation (use create-quotation). NOT for submitting the new order (use submit-sales-order).
allowed_roles:
  # Creating the order needs the same rights as creating a sales order directly.
  - Sales User
  - Sales Manager
  - System Manager
  - Administrator
tool: convert_document
doctype: Sales Order
source_doctype: Quotation
required_fields:
  - quotation
optional_fields:
  - items
field_hints:
  items: "only if the user wants some of the items or fewer units: a list of the item code and the quantity wanted of each. Leave it out to convert everything that is left"
payload_structure:
  fixed: {}
  field_mappings:
    quotation: quotation
    items: items
  child_defaults: {}
defaults: []
validation_rules: []
keywords:
  - convert quotation
  - convert a quotation
  - convert the quotation
  - convert quotation to sales order
  - quotation to sales order
  - sales order from quotation
  - create sales order from quotation
  - create a sales order from quotation
  - make sales order from quotation
  - make a sales order from quotation
  - turn quotation into sales order
response_template: "Successfully created Sales Order: {name} from Quotation '{quotation}'!"
error_template: "Failed to create a sales order from the quotation: {error}"
examples:
  - "Create a sales order from quotation QTN-2026-00012 with only 2 units of ITEM-A"
  - "Convert quotation SAL-QTN-2026-00007 to a sales order"
  - "Create a sales order from quotation SAL-QTN-2026-00008"
  - "Turn quotation SAL-QTN-2026-00009 into a sales order"
---

# Convert Quotation to Sales Order

## When to use
Trigger this skill when the user wants to turn an existing Quotation into a Sales Order, for example because the customer accepted the quote.

**IMPORTANT intent boundaries:**
- Use `create-sales-order` to create an order from scratch, without a quotation
- Use `create-quotation` to create a new quotation
- Use `submit-sales-order` to submit the order this creates
- Use `quotation-lookup` to look at a quotation first

## Required information
- **quotation**: the ID of the Quotation to convert, as the user gave it (for example SAL-QTN-2026-00007). Never guess one.

## Optional: only some of the items
- **items**: when the user wants only some of the items, or fewer units than are left, collect a list of the item code and the quantity wanted of each. It can only reduce: a quantity above what is left, or an item that is not on the quotation, is refused. Leave it out to convert everything that is left.

## What happens
- The quotation must be submitted. A draft or cancelled quotation cannot be converted.
- ERPNext itself builds the order from the quotation: the customer, the items, quantities and prices. Nothing is taken from the conversation except which quotation to use.
- The user is always asked to confirm. The question shows the customer, the items and the total, and mentions any sales orders that were already made from the same quotation.
- The new order is saved as a Draft. If the user wants it submitted, that is a separate step and it asks again.
- A delivery date a week out is set on the order, because ERPNext needs one and a quotation has none.

## ERPNext details
- Creates: `Sales Order`, from the doctype `Quotation`
- The link back to the quotation is kept on each order row

## Tool
Tool name: `convert_document`, creating a `Sales Order` from a `Quotation`.

## Steps
1. Extract the quotation ID.
2. Check that the quotation exists and is submitted.
3. Show what will be created and any orders already made from it, and ask for confirmation.
4. Create the Draft Sales Order through ERPNext's own conversion.
5. Offer to submit it.
