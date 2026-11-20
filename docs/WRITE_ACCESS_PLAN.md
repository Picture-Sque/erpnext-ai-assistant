# Chat Agent Write Access: Analysis and Implementation Plan

Date: 21 Nov 2026. Branch: `mcp-integration` at `7a68062`. Draft for team review, not an approved design.

Scope of the analysis: static reading of the code and docs on this branch. I did not run the test suite and did not call a live ERPNext instance, so anything marked UNVERIFIED below needs a check on the local instance before it is relied on.

## 1. Summary

The chat agent is not read only. It already has a full write pipeline in LangGraph: write RBAC gate, precondition checks, confirmation for submit/cancel/delete, execution as the caller's own ERPNext session, and post-write verification. Eight skills use it, covering create, update, submit and cancel for Sales Order and Purchase Order. What is read only is the MCP side: the MCP server, its HTTP transport and the in-process MCP path used for chat reads. Writes were deliberately kept on the direct path until per-user identity existed (plan section 1), and identity now exists.

So the real task is not "turn writes on". It is three things:

1. Widen what the agent can write (more doctypes, more fields, more operations), because today anything outside the 8 skills falls through to the fallback response.
2. Make role-based control real for writes. Today it is a static role list in each skill's YAML, enforced only inside `graph.py`. The MCP policy layer has no role check for writes at all, and `crud.py` has no field allowlist.
3. Close several gaps in the existing write path before it is widened, because widening multiplies them (section 3).

ERPNext itself already enforces DocPerm and User Permissions on every write, because writes go out with the user's session cookie. That is the authoritative control and the plan keeps it as the final backstop. The agent-side gates exist to fail early, give clear messages, and limit what an LLM can be talked into asking for.

## 2. Current state

### 2.1 How a write flows today

A message is classified to a skill (`classify_intent_node`), slots are extracted by the LLM (`collect_parameters_node`), validated, entities are resolved, then `write_rbac_gate_node` (`graph.py:1483`) checks the JWT roles against the skill's `allowed_roles`. `precondition_validation_node` (`graph.py:1564`) fetches the target fresh and checks existence, docstatus compatibility, required fields, staleness and a recent-duplicate check for creates. `confirmation_node` (`graph.py:1879`) asks for Yes/No on submit, cancel, delete, on updates to submitted documents, and on planner-derived chain steps. `pending_action_gate_node` (`graph.py:3600`) handles the reply: only the requester can answer, pending actions expire after 10 minutes, and RBAC and preconditions re-run on Yes. `call_generic_tool_node` (`graph.py:2155`) executes through `adapter`, and `result_validation_node` (`graph.py:2512`) re-reads the document directly to verify the write landed.

### 2.2 Where each control lives

| Control | Where | Applies to |
|---|---|---|
| Role gate per skill | `graph.py:212`, role lists in `skills/*/SKILL.md` | Chat only |
| Doctype whitelist | `mcp_erpnext/crud.py:29` (one list for reads and writes) | Chat, MCP, tools API |
| Read-only session flag | `adapter.py:360`, `client.py` transport backstop, `policy.py:122` | All paths |
| Tool allowlist and arg validation | `policy.py`, `server.py` | MCP path only, and chat writes never use it |
| Per-user ERPNext session | `RequestContext` credentials | All paths |
| Confirmation and expiry | `graph.py:1879`, `graph.py:3600` | Chat only |
| Audit log | `agent/audit_logger.py` (local SQLite) | Chat only |

### 2.3 What the 8 write skills cover

| Skill | Doctype | Roles allowed |
|---|---|---|
| create-sales-order | Sales Order | Sales User, Sales Manager, System Manager, Administrator |
| update-sales-order (delivery_date only) | Sales Order | same as create |
| submit-sales-order | Sales Order | Sales Manager, System Manager, Administrator |
| cancel-sales-order | Sales Order | Sales Manager, System Manager, Administrator |
| create-purchase-order | Purchase Order | Purchase User, Purchase Manager, System Manager, Administrator |
| update-purchase-order | Purchase Order | same as create |
| submit-purchase-order | Purchase Order | Purchase Manager, System Manager, Administrator |
| cancel-purchase-order | Purchase Order | Purchase Manager, System Manager, Administrator |

There is no delete skill. Delete exists only through the bulk path and the `/api/tools/delete` endpoint (System Manager by default).

## 3. Findings to fix before widening

These are in the existing write path. Line numbers are from this branch.

1. Update sends every collected key. `call_generic_tool_node` builds the update payload as `dict(collected)` minus `id` and `name` (`graph.py:2260`). `collect_parameters_node` merges every key the LLM returns into `collected_fields` (`graph.py:672`), not only the skill's declared fields. Creates are safe because they use `field_mappings`. Updates are not: a user message, or text inside a record the agent read, could cause fields such as `docstatus`, `status` or `grand_total` to be sent. ERPNext's own rules limit the damage but this should not depend on that. The verification step even skips `filters` and `_auto_resolved_note` keys, which suggests they do end up in `collected`. Fix: build the update payload from the skill's declared fields only.
2. No write allowlist in the tool layer. `crud.create_document`, `update_document` and `delete_document` accept any whitelisted doctype and any fields. The whitelist is a read list that includes Bin, Stock Ledger Entry, Company and Warehouse. Nothing in `mcp_erpnext` stops a write to those if a caller reaches it. Today only skills and the System Manager tools API do.
3. `require_writable()` is not called in `crud.py` (open item 2 in plan section 10). Read-only sessions are stopped by the transport backstop and adapter check, which return `system_error` or `permission_denied` depending on the layer. It works, but the error shape is inconsistent.
4. Audit events lose the user. `write_rbac_gate_node` (`graph.py:1522`) and `result_validation_node` (`graph.py:2656`, `graph.py:2672`) call `log_audit_event` without `user_id`, so denied, failed and executed events are stored as `system`. Only the confirmation events carry the user. The audit table is also a local SQLite file, so it is not tamper resistant. ERPNext's own Version log, written under the user's session, is the stronger record.
5. Updates and cancels send no `modified` value (`crud.py:352`), so concurrent edits are last write wins. The staleness check in `graph.py:1761` only compares against a snapshot from earlier in the same conversation. Plan section 6 already lists "send `modified`" for Member 6.
6. The duplicate check hard-codes the `customer` field (`graph.py:1800`). It will not protect creates for any doctype without a customer, and the same file already fails the genericity scan.
7. Bulk path caps nothing. `get_list(limit=999999)` at `graph.py:2369`, and the bulk path supports delete, cancel and submit with only a count check. For wider write access, bulk needs a hard cap and its own role rule.
8. Role names in skills are static. Roles in the JWT are fixed at mint time (1 hour). A site with customised role permissions will disagree with the YAML lists. Only ERPNext knows the truth.
9. Unconfirmed creates and draft updates. Create and draft-update run without a Yes/No unless they are chain steps (`graph.py:1929`). That is acceptable for 2 doctypes. Whether it stays acceptable for more is a product decision (section 8).

## 4. Target design

Four layers, outermost first. Each layer fails closed.

Layer 1, write registry (new, data, in `mcp_erpnext`). One structure that says, per doctype, which operations the chat may perform, which roles may perform each, which fields are writable (with child-table fields), which are required, whether the doctype is submittable, whether confirmation is required, and what identifies a duplicate. A doctype not in the registry cannot be written through chat, regardless of the read whitelist. This follows the report allowlist pattern Member 2 already built (`reports.py`), so it can reuse its validation style.

Layer 2, policy enforcement in the shared tool layer. Add `check_write_allowed(ctx, tool, doctype, fields)` to `policy.py` and call it from `crud.py` write functions, so the direct backend and any MCP route get the same checks. It rejects: unregistered doctype or operation, roles without a match (using the existing fail-closed `check_role_access`), fields outside the allowlist, reserved fields (`docstatus`, `owner`, `creation`, `modified`, `modified_by`, `parent`, `parenttype`, `parentfield`, `name` on create), unknown child-table fields, and read-only sessions (this is where `require_writable()` goes). It returns the standard `permission_denied` envelope.

Layer 3, ERPNext permission preflight with the user's own session. Before showing a confirmation, ask ERPNext whether this user may perform this operation on this doctype or document. This replaces most of the duplicated role matrix with the real one and picks up customised DocPerm, User Permissions (company, territory and similar) and field-level permissions. The call to verify on the local instance is `frappe.client.has_permission` (UNVERIFIED for Frappe 16; I am not certain of its exact name or parameters). If it is not usable, the fallback is to rely on the registry roles plus ERPNext's refusal at write time, and to make that refusal message clear.

Layer 4, ERPNext enforcement at write time. Already live. Keep it unchanged.

Routing: chat writes keep running in-process with the caller's `RequestContext`. They are not exposed over HTTP MCP, which stays read-only for external clients (plan section 1). `TOOL_BACKEND=direct` stays as the rollback. Because Layer 2 sits in `crud.py`, both backends are covered without moving the write calls.

Skills: keep one YAML skill per intent, since routing and slot extraction depend on them. Change the skill loader to check at startup that every write skill's doctype, operation, roles and fields are a subset of the registry, and refuse to load a write skill that is not. Roles and field lists then have one source of truth.

## 5. Proposed doctype and role matrix

This is a starting proposal for business sign-off, not a verified configuration. Role names are the standard ERPNext ones used elsewhere in this repo. They were not checked against this site's role setup.

| Doctype | Create | Update | Submit | Cancel | Delete | Tier |
|---|---|---|---|---|---|---|
| Sales Order | Sales User+ | Sales User+ (more fields) | Sales Manager+ | Sales Manager+ | off | exists |
| Purchase Order | Purchase User+ | Purchase User+ (more fields) | Purchase Manager+ | Purchase Manager+ | off | exists |
| Quotation | Sales User+ | Sales User+ | Sales Manager+ | Sales Manager+ | off | 1 |
| Customer | Sales User+ | Sales User+ (not credit limit) | n/a | n/a | off | 1 |
| Supplier | Purchase User+ | Purchase User+ | n/a | n/a | off | 1 |
| Item | Stock Manager, Item Manager | same | n/a | n/a | off | 1 |
| Delivery Note | Stock User+ | Stock User+ | Stock Manager+ | Stock Manager+ | off | 2 |
| Purchase Receipt | Stock User+ | Stock User+ | Stock Manager+ | Stock Manager+ | off | 2 |
| Sales Invoice | Accounts User+ | Accounts User+ | Accounts Manager | Accounts Manager | off | 2 |
| Purchase Invoice | Accounts User+ | Accounts User+ | Accounts Manager | Accounts Manager | off | 2 |
| Stock Entry | Stock User+ | Stock User+ | Stock Manager | Stock Manager | off | 2 |
| Payment Entry, Journal Entry, Stock Reconciliation | not in first release | | | | | 3 |

Tier 1 is draft-level master and pre-sales data. Tier 2 changes stock or ledgers on submit, so submit and cancel need a named manager role and a confirmation. Tier 3 has direct financial or inventory-valuation impact and is excluded until Tiers 1 and 2 are stable, and until the business decides on approval rules. "+" means the doctype's manager and System Manager and Administrator roles are included.

Delete is off everywhere in chat. If the business wants it, it should be draft only, System Manager only, and need the user to type the document name.

Segregation of duties (for example, the same person creating and submitting an invoice) and any statutory audit requirement are compliance questions. I have not assessed them. They need review by the people who own those rules before Tier 2 financial doctypes go live.

Two things need ERPNext-side facts first: documents created from other documents (Delivery Note from a Sales Order, Invoice from a Delivery Note) use ERPNext's mapped-document methods, not a plain create, so they need their own tool path; and doctypes with a Workflow attached may not accept a direct `docstatus` change. Neither was checked here (UNVERIFIED).

## 6. Phased plan

Phase 0, foundation (no new user-visible capability). Fixes findings 1 to 8.
- Add the write registry and `check_write_allowed`, wire it into the five `crud.py` write functions, add `require_writable()`.
- Build update payloads from declared skill fields only. Drop unknown LLM keys at extraction time as well.
- Pass the user id on every audit call. Add the request id from `RequestContext`.
- Send `modified` on update, submit and cancel and surface a clear conflict message.
- Replace the hard-coded duplicate check with registry-declared duplicate keys.
- Cap bulk size and make bulk delete unavailable.
- Skill loader cross-check against the registry.
- Exit criteria: existing 8 skills behave as before, all current tests still pass (baseline: 1 known failure), new tests for each rejection case pass, two-user run passes on both backends.

Phase 1, permission preflight. Add the Layer 3 check at confirmation time and at the start of write skills, using the caller's session. Cache nothing across users. Exit: a user whose ERPNext permissions differ from the YAML roles is allowed or refused according to ERPNext, in both directions.

Phase 2, widen Sales Order and Purchase Order. More updatable fields (item quantities and rates, customer reference, schedule dates), child-row edits, and a diff preview ("delivery_date: 2026-12-01 to 2026-12-10") in the confirmation. This validates the registry design on doctypes that are already proven live.

Phase 3, Tier 1 doctypes (Quotation, Customer, Supplier, Item), one doctype per commit, each with its skills, registry entry, role tests and a live check.

Phase 4, Tier 2 doctypes with mapped-document creation, starting with Delivery Note and Purchase Receipt, then invoices and Stock Entry. Each needs a decision on confirmation and approver roles first.

Phase 5, hardening. Intent-collision tests for the new skills (the keyword router has had collisions before, see commit `2bd85e9` and `agent/test_intent_collision.py`), per-role read-only tokens for users with no writer role, audit export, documentation, and a go/no-go review.

Working agreements stay as in plan section 2: small commits, tests green each time, stage by explicit path, `git pull --rebase` before push, user pushes through GitHub Desktop.

## 6a. Phase 0 status (21 Nov 2026)

Phase 0 is implemented on `mcp-integration` in five local commits (`34041cc`, `777ee9d`, `5702669`, `a0e8f49`, `5c69e10`), not pushed. Test result with the local `.env` overrides neutralised: `tests` 600 passed and 1 failed, `agent/tests` 437 passed; run together, 1037 passed and 1 failed. The one failure is the known genericity scan, unchanged.

| Finding | State |
|---|---|
| 1 Update sends every collected key | Fixed. Update and create payloads come from the skill's declared fields intersected with the registry. Slot extraction for a write skill ignores undeclared model output. Verification checks what was sent. |
| 2 No write allowlist in the tool layer | Fixed. `mcp_erpnext/write_registry.py`, enforced in `crud.py` and `policy.py`. Only Sales Order and Purchase Order are registered. Delete is registered nowhere. |
| 3 `require_writable()` not in `crud.py` | Fixed. Read-only sessions get `permission_denied` from the registry check before any HTTP call. |
| 4 Audit events lose the user | Fixed. User and request id come from the request context. `request_id` column added with an in-place migration. |
| 5 No `modified` on writes | Implemented, not verified live (see below). |
| 6 Hard-coded `customer` duplicate check | Fixed. Keys come from the registry. Purchase Orders were not duplicate-checked at all before; now they are, by supplier. |
| 7 Unbounded bulk | Fixed. Cap of `WRITE_BULK_MAX` (default 25), bulk delete refused, the 999,999 read is gone. |
| 8 Static role names | Partly. The registry is now the single source of truth, a write skill that exceeds it is not loaded, and the write gate checks both. The ERPNext permission preflight (Layer 3) is Phase 1 and is not done. |
| 9 Unconfirmed create and draft update | Unchanged. Product decision (section 8). |

Found during the work and also fixed:

- A failed write could be retried by the graph. A retry jumps straight back to the tool step, skipping the precondition and confirmation steps. A transient error reading the target, or a duplicate check that failed with "Nothing was created", led to the write running anyway, and a create that timed out but had landed could be created twice. Writes are no longer retried automatically. Reads still are. `test_tool_system_failure_for_true_faults` used an update as its example of a retried fault and now uses a read.
- The audit table was never created at startup, so on a fresh deployment every audit write failed silently. Startup now creates it, and an event that finds no table creates it and retries.
- One genericity test deletes the file at `AUDIT_DB_PATH`, which was the developer's real `agent/audit_log.db`. Both test suites now use a temporary audit database.

Behaviour changes to know about:

- The System Manager only `/api/tools/add`, `/update` and `/delete` endpoints now go through the same registry. They can no longer write Customer, Item or any doctype that is not registered, and delete is unavailable there too. Say if the admin endpoint should keep a wider reach.
- A write skill with no `allowed_roles` is denied. Before, an empty list meant open to everyone.
- New settings, all optional: `ERPNEXT_WRITES_DISABLED`, `WRITE_BULK_MAX`, `ERPNEXT_SEND_MODIFIED`, `SQLITE_AUDIT_DB_PATH` (documented in `agent/.env.example`).
- To add a doctype or operation, add it to `WRITE_REGISTRY` first, then the skill. The loader refuses a skill that goes beyond the registry.

Not verified against a live ERPNext, and the first thing to check before this goes anywhere near users:

1. That a PUT carrying a stale `modified` is refused, and what it returns. The code treats a response with exception type `TimestampMismatchError`, or the standard "has been modified after you have opened it" wording, as a conflict. If ERPNext words it differently the write still fails safely as an ordinary error, but the message will be less clear.
2. That a PUT carrying the `modified` just read is accepted for update, submit and cancel on both doctypes. If it is wrongly refused, set `ERPNEXT_SEND_MODIFIED=false` and report it. The confirmation-staleness check does not depend on this and keeps working.
3. The two-user check from section 7, under both `TOOL_BACKEND=direct` and `TOOL_BACKEND=mcp`.

## 7. Verification plan

Unit: for every registry entry, a test per operation for allowed role, denied role, empty roles, bare-string roles, unregistered field, reserved field, child-row field, read-only session. Tests that a doctype on the read whitelist but not in the registry cannot be written. A test that an LLM-injected extra key never reaches the update payload.

Integration: LangGraph runs with a fake ERP for each write skill, including confirm, decline, expiry, wrong user answering, and ERPNext returning 403 and 417.

Two-user live check, required by plan section 10 and run under `TOOL_BACKEND=direct` and `TOOL_BACKEND=mcp`: for each registered doctype and operation, run it as a user who holds the role, one who does not, and System Manager. Confirm the result, the `owner` and `modified_by` values in ERPNext, the ERPNext Version log entry, and the local audit row with the right user id. Confirm zero mutation in the database for every denied case.

Regression: run both suites with `PYTHONPATH=agent;.` and `ALLOW_DEV_MOCK_AUTH=false` as in plan section 5. Do not add to the genericity failure; keep new doctype names out of `graph.py`.

## 8. Risks, open decisions and unverified items

Decisions needed from the team:
1. Which doctypes go in the first release (the matrix above is a proposal).
2. Whether every create and update should require Yes/No once the doctype count grows, or only submit, cancel and delete as now. My recommendation is to confirm all writes at first and relax per doctype later.
3. Whether the ERPNext permission preflight (Layer 3) is acceptable as the source of truth for roles, or whether the business wants an explicit agent-side matrix that can be stricter than ERPNext.
4. Approver rules for Tier 2 and Tier 3 (segregation of duties). Compliance owner to review.
5. Whether delete is wanted at all.

UNVERIFIED, to check on the local instance before building on them: the exact permission-check endpoint and its Frappe 16 behaviour; whether `PUT` with `docstatus` behaves correctly for each target doctype and with Workflows; how ERPNext reports a stale `modified` value over REST; the real role setup on this site; and whether the symptom you saw ("only read") came from the chat itself or from a deployment setting such as a read-only token. The widget requests `get_chat_token`, which mints a writable token (`useDemoChat.ts:119`, `api.py:93`), so the code path allows writes.

Risks: more write skills raise the chance of intent collisions and wrong-intent writes, which is why confirmation and the collision tests are in the plan; a longer-lived JWT role snapshot means a role removed in ERPNext still passes the agent gate for up to an hour, which Layer 3 and Layer 4 cover; the resolver caches have no TTL; and the audit log is local SQLite without tamper protection.

Rollback: `TOOL_BACKEND=direct` still works. Registry enforcement should have a documented emergency switch that makes all chat writes read only (a registry with no entries, or an environment flag), tested before release.
