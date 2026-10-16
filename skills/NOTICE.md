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

---

## Skill File Format

Every skill lives in `skills/<name>/SKILL.md`: YAML frontmatter between two `---` lines, then a Markdown body that is passed to the LLM as the skill's instructions. `agent/tests/test_skill_files.py` checks every rule below; run it after adding or editing a skill.

### Frontmatter keys

| Key | Required | Meaning |
|---|---|---|
| `name`, `intent` | yes | Both equal the folder name |
| `description` | yes | What the skill does, when to use it, and `NOT for ...` boundaries naming the skill to use instead. The classifier and planner read this |
| `allowed_roles` | yes | ERPNext roles allowed to run it |
| `tool` | yes | `get_list`, `get_document`, `aggregate`, `create_document`, `update_document`, `submit_document` or `cancel_document` |
| `doctype` | yes | Target ERPNext doctype |
| `required_fields` / `optional_fields` | yes | Field names the extractor fills. A field cannot be in both. Every optional field must be used by a filter, the payload or the tool |
| `defaults` | yes | `[{field, value}]`; `value` may be `today` or `<field> + Nd` |
| `validation_rules` | yes | `[{field, must_be_after or must_be_before, on_fail}]` |
| `keywords` | yes | Phrases that route a message to this skill without an LLM call. Must be specific to this skill: a keyword shared with another skill, or generic enough to match another skill's request ("cancel order", "how much", "customer"), is rejected by the tests |
| `response_template`, `error_template` | yes | `{field}` placeholders; `error_template` may only use `{error}` |
| `examples` | yes | Realistic requests. Each must route to this skill or to the LLM classifier, never to another skill |
| `query_parameters` | read skills | `filters` (placeholders must be required or defaulted), `optional_filters` (`[{field, filter}]`; a 4-element filter `[child doctype, field, op, value]` filters on a child table) and `fields` (columns returned) |
| `payload_structure` | write skills | `fixed`, `field_mappings`, `child_defaults` for `create_document` |
| `not_found_message` | optional | Shown when a read returns nothing |
| `follow_up_eligible`, `follow_up_slots` | optional | Read skills only; slots a short follow-up may change |
| `field_values` | optional | `{field: [allowed values]}`; other values are asked for, never sent to ERPNext |
| `outputs` | optional | Result fields later chain steps can reference as `$stepN.<field>`; defaults to `query_parameters.fields` |
| `context_pronouns`, `chain_exports` | optional | Legacy keys kept for compatibility |

### Body sections
Start with `# Title`, then at least `## When to use` (with intent boundaries), `## ERPNext details` and `## Tool`. Add `## Required information`, `## Optional information` and `## Steps` where they apply.
