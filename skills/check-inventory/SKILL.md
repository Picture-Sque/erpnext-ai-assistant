---
name: check-inventory
description: Check current stock levels for an item in ERPNext, optionally at a specific warehouse. Use when the user asks "how much X do we have", "is X in stock", "check inventory for X", or similar.
---

# Check Inventory

## When to use
Trigger this skill when the user wants to know current stock quantity for one or more items, with or without a specific warehouse.

## Required information
- **Item** (item code or name — if the name is ambiguous, search for close matches and ask the user to confirm)
- **Warehouse** (optional — if not given, return stock across all warehouses, or the default warehouse if the org only uses one)

## ERPNext details
- Source: `Bin` doctype (holds `item_code`, `warehouse`, `actual_qty`, `reserved_qty`, `projected_qty`)
- Alternative: the `Stock Balance` report for a point-in-time snapshot
- Key fields to report: `actual_qty` (physically in stock), `reserved_qty` (already committed to orders), `projected_qty` (actual - reserved + incoming)

## Tool
Tool name: `check_stock` (schema and handler defined separately in the tools registry — this skill does not define its schema)

## Steps
1. Resolve the item code (fuzzy match on name if needed).
2. Call `check_stock`, filtered by warehouse if one was given.
3. If multiple warehouses have stock, list them individually rather than only a total.
4. Report actual quantity, and mention reserved/projected quantity if it's meaningfully different from actual.

## Example
User: "How much Widget-A do we have in the Main Warehouse?"
Response: Call check_stock for Widget-A at Main Warehouse → report actual_qty, and reserved_qty if non-trivial.

## Edge cases
- If the item doesn't exist, say so clearly rather than returning zero silently.
- If stock is negative (oversold), flag it — don't just report the raw number without context.
