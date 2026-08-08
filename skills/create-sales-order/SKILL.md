---
name: create-sales-order
description: Create a new Sales Order in ERPNext for a customer. Use when the user asks to place an order, create a sales order, or sell items to a customer. Triggers on phrases like "create an order for", "sell X to customer Y", "new sales order".
---

# Create Sales Order

## When to use
Trigger this skill when the user wants to create a new Sales Order document in ERPNext — i.e. record that a customer has ordered specific items.

## Required information
Before calling the tool, make sure you have:
- **Customer** (name or customer ID — if ambiguous, use the Customer Lookup skill first to confirm)
- **Items** (item code or name, quantity for each)
- **Delivery date** (optional — default to today + 7 days if not given)
- **Rate/price** (optional — ERPNext will pull from the item's default price list if not specified)

If the customer name is ambiguous or not found, use the Customer Lookup skill first instead of guessing.

## ERPNext details
- Doctype: `Sales Order`
- Child table: `items` (each row needs `item_code`, `qty`, and optionally `rate`)
- Required fields: `customer`, `delivery_date`, `items` (at least one row)
- Sales Orders are created in "Draft" status by default — do not auto-submit unless the user explicitly asks to submit/confirm it.

## Tool
Tool name: `create_sales_order` (schema and handler defined separately in the tools registry — this skill does not define its schema)

## Steps
1. Confirm the customer exists (look up if unsure — use the `get_customer` tool).
2. Confirm each item code exists and is sellable.
3. Build the Sales Order payload with customer, items, and delivery date.
4. Call `create_sales_order`.
5. Report back the new Sales Order ID (e.g. SAL-ORD-2026-00042) and a short summary of what was ordered.

## Example
User: "Create a sales order for Acme Corp — 10 units of Widget-A and 5 units of Widget-B"
Response: Confirm Acme Corp exists → confirm Widget-A and Widget-B exist → create Sales Order with those two line items → return the new Sales Order number.

## Edge cases
- If an item is out of stock, still create the order (ERPNext allows this) but mention the stock shortfall in your reply — don't block creation.
- If quantity or item is missing/unclear, ask the user rather than guessing values.
