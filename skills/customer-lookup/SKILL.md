---
name: customer-lookup
description: Look up an ERPNext customer record by name, ID, or partial match. Use when the user asks "find customer X", "who is X", needs customer details, or when another skill (like Create Sales Order) needs to confirm a customer exists.
---

# Customer Lookup

## When to use
Trigger this skill when the user wants to find or confirm details about a customer, or when another skill needs a customer confirmed before proceeding.

## Required information
- **Search term**: customer name (full or partial), or customer ID

## ERPNext details
- Doctype: `Customer`
- Useful fields: `customer_name`, `customer_group`, `territory`, `mobile_no`, `email_id`, `disabled`
- Related doctype for contact details: `Contact` (linked via Dynamic Link)

## Tool
Tool name: `get_customer` (schema and handler defined separately in the tools registry — this skill does not define its schema)

## Steps
1. Call `get_customer` with the search term (partial name match, case-insensitive).
2. If exactly one match, return it with key details (name, group, territory, contact info).
3. If multiple matches, list them briefly and ask the user which one they mean.
4. If no matches, say so clearly — don't guess or fabricate a customer.
5. If called from another skill (e.g. Create Sales Order), return just the resolved customer ID/name so the calling skill can proceed.

## Example
User: "Find customer Acme"
Response: Call get_customer with "Acme" → if "Acme Corp" and "Acme Industries" both match, list both and ask which one.

## Edge cases
- Disabled customers (disabled=1) should still show up in search but flag that they're disabled.
- Don't expose sensitive fields (e.g. internal credit limits) unless explicitly asked.
