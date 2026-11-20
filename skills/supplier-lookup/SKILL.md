---
name: supplier-lookup
intent: supplier-lookup
description: Look up an ERPNext supplier record (supplier group, type, country, email, mobile) by name or partial name, or list all suppliers when no specific supplier is requested. Use when the user asks "show all suppliers", "list suppliers", "find supplier X", or needs supplier profile details. NOT for what was bought from a supplier (use supplier-purchase-analysis). NOT for purchase orders (use purchase-order-lookup).
allowed_roles:
  - Purchase User
  - Purchase Manager
  - Accounts User
  - Accounts Manager
  - Stock Manager
  - System Manager
  - Administrator
tool: get_list
doctype: Supplier
required_fields: []
optional_fields:
  - supplier_name
query_parameters:
  filters: []
  optional_filters:
    - field: supplier_name
      filter: ["supplier_name", "like", "%{supplier_name}%"]
  fields:
    - supplier_name
    - supplier_type
    - supplier_group
    - country
    - email_id
    - mobile_no
defaults: []
validation_rules: []
keywords:
  - look up supplier
  - lookup supplier
  - find supplier
  - search supplier
  - supplier details
  - supplier info
  - show all suppliers
  - show me all suppliers
  - list suppliers
  - list all suppliers
response_template: |
  **Supplier Details: {supplier_name}**
  - **Type**: {supplier_type}
  - **Supplier Group**: {supplier_group}
  - **Country**: {country}
  - **Email**: {email_id}
  - **Mobile**: {mobile_no}
not_found_message: "Supplier Lookup Result:\n\nUnfortunately, we were unable to find any information on the supplier \"{supplier_name}\" in our database."
error_template: "Failed to look up supplier: {error}"
examples:
  - "Show me all suppliers"
  - "List all suppliers"
  - "Look up supplier Summit Traders Ltd."
  - "Find supplier Zuckerman"
---

# Supplier Lookup

## When to use
Trigger this skill when the user wants to list all suppliers, or find and confirm details about a specific supplier.

**IMPORTANT intent boundaries:**
- Use `supplier-purchase-analysis` for what was bought from a named supplier
- Use `purchase-order-lookup` for a purchase order
- Use `create-supplier` or `update-supplier` to add or change a supplier
- Use THIS skill for supplier profile details or the supplier list

## Optional information
- **supplier_name**: Supplier name (full or partial) for a specific supplier. Leave it out to list all suppliers.

## ERPNext details
- Doctype: `Supplier`
- Useful fields: `supplier_name`, `supplier_type`, `supplier_group`, `country`, `mobile_no`, `email_id`, `disabled`

## Tool
Tool name: `get_list` with DocType `Supplier`.

## Steps
1. Call `get_list` on DocType `Supplier` (with a `supplier_name` filter if specified, or without filters to list all).
2. If records are found, format and return supplier details or a table.
3. If looking up a specific supplier and no match is found, state that no supplier record was found.
