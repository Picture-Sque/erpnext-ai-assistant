---
name: purchase-receipt-report
intent: purchase-receipt-report
description: List Purchase Receipts (goods received from suppliers such as printers and paper vendors), optionally by supplier, Purchase Order, date or status. Use when the user asks what was received, which receipts exist for a supplier or purchase order, or the receiving status of a purchase. NOT for the purchase orders themselves (use purchase-order-lookup). NOT for creating or submitting receipts.
allowed_roles:
  - Purchase User
  - Purchase Manager
  - Stock User
  - Stock Manager
  - Accounts User
  - Accounts Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Purchase Receipt
required_fields: []
optional_fields:
  - supplier
  - purchase_order
  - date_from
  - date_to
  - status
query_parameters:
  filters: []
  optional_filters:
    - field: supplier
      filter: ["supplier", "like", "%{supplier}%"]
    - field: purchase_order
      filter: ["Purchase Receipt Item", "purchase_order", "=", "{purchase_order}"]
    - field: date_from
      filter: ["posting_date", ">=", "{date_from}"]
    - field: date_to
      filter: ["posting_date", "<=", "{date_to}"]
    - field: status
      filter: ["status", "=", "{status}"]
  fields:
    - name
    - supplier
    - posting_date
    - status
    - docstatus
    - is_return
    - grand_total
    - currency
defaults: []
validation_rules: []
follow_up_eligible: true
follow_up_slots:
  - supplier
  - purchase_order
  - date_from
  - date_to
  - status
keywords:
  - purchase receipt
  - purchase receipts
  - incoming stock
  - stock received
  - what did we receive
  - received from supplier
  - goods received
  - incoming inventory
response_template: "**Purchase Receipts**"
error_template: "Failed to retrieve purchase receipts: {error}"
not_found_message: "No matching Purchase Receipt was found."
examples:
  - "Show incoming stock from ABC Printers"
  - "What purchase receipts were created this month?"
  - "Was purchase order PUR-ORD-2026-00013 received?"
---

# Purchase Receipt Report

## When to use
Trigger this skill to review receipts of goods from suppliers. Read-only.

**IMPORTANT intent boundaries:**
- Use `purchase-order-lookup` for the order itself and what is still pending
- Creating, submitting or cancelling receipts has no skill yet; say so instead of improvising
- Use THIS skill for the receipt records

## Optional information
- **supplier**, **purchase_order**, **date_from**, **date_to**, **status**; supplier and item names go through entity resolution

## ERPNext details
- Doctype: `Purchase Receipt`; the Purchase Order link lives on the child table `Purchase Receipt Item`, so it is a child-table filter
- `status`: Draft, To Bill, Completed, Return Issued, Cancelled, Closed; `is_return = 1` marks goods returned to the supplier
- This skill returns receipt headers only. It cannot say how many copies of a title arrived; do not estimate quantities. Item-level quantities need the stock ledger (`stock-movement-analysis`) or a future receipt-lines report
- ENGINE PREREQUISITES: whitelist `Purchase Receipt` and `Purchase Receipt Item`

## Tool
Tool name: `get_list` on DocType `Purchase Receipt`.

## Steps
1. Identify supplier, Purchase Order and period, if given.
2. Retrieve matching receipts.
3. Present them with status and date; say when the list is truncated.
