# ERPNext AI Assistant — Skills Directory

## Deliberate Omission: `delete-*` Skills

**No `delete_document` skill has been created for any Doctype, and this is intentional.**

### Reasoning
- ERPNext's `delete_document` permanently removes records with no native undo path (unlike cancellation, which preserves the audit trail via `docstatus=2`).
- For the practical use cases in this system (Sales Orders, Purchase Orders), **cancellation already covers the operational need** — cancelled documents remain visible in reports and audit logs, which is almost always preferable to permanent deletion.
- The risk profile of deletion (irreversible data loss, potential referential integrity issues) significantly exceeds the risk of cancellation, making it out of scope for an AI-driven workflow where intent classification errors could have permanent consequences.
- If deletion is required in a future version, it must be:
  1. Added to the doctype whitelist explicitly
  2. Implemented with at minimum double-confirmation (name the exact record + explicit "delete" keyword in confirmation reply)
  3. Reviewed for cascading effects (linked child tables, ledger entries, etc.)

**Do not add a `delete-*` skill without a deliberate architectural review.**

---

## Skill Registry (17 Active Skills)

### Batch 0 — Original Skills (4)
| Skill | Tool | Description |
|---|---|---|
| `check-inventory` | `get_list` | Stock level for a SPECIFIC named item |
| `customer-lookup` | `get_list` | Customer profile details |
| `item-lookup` | `get_list` | Item details by code/name |
| `create-sales-order` | `create_document` | Create a new Sales Order |

### Batch 0b — Original Write Skills (3)
| Skill | Tool | Description |
|---|---|---|
| `cancel-sales-order` | `cancel_document` | Cancel an existing Sales Order (w/ confirmation) |
| `update-sales-order` | `update_document` | Update fields on a Sales Order |
| `submit-sales-order` | `submit_document` | Submit/finalize a Draft Sales Order (w/ confirmation) |

### Batch A — Lookup Skills (2)
| Skill | Tool | Description |
|---|---|---|
| `sales-order-lookup` | `get_document` | View details of a specific Sales Order by ID |
| `purchase-order-lookup` | `get_list` | View a PO by ID/supplier, or check if item has a PO |

### Batch B — Analytics & Reports (4)
| Skill | Tool | Description |
|---|---|---|
| `sales-analytics-report` | `aggregate` | Best seller, top customer, revenue totals (computed metrics) |
| `low-stock-report` | `get_list` | All items below a stock threshold (portfolio-level alert) |
| `sales-order-report` | `get_list` | Filtered list of Sales Orders by date/status |
| `customer-order-history` | `get_list` | All orders for a NAMED customer |

### Batch C — Purchase Order Write Skills (4)
| Skill | Tool | Description |
|---|---|---|
| `create-purchase-order` | `create_document` | Create a new Purchase Order from a supplier |
| `update-purchase-order` | `update_document` | Update fields on an existing Purchase Order |
| `cancel-purchase-order` | `cancel_document` | Cancel a submitted Purchase Order (w/ confirmation) |
| `submit-purchase-order` | `submit_document` | Submit/finalize a Draft Purchase Order (w/ confirmation) |
