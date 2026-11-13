# ERPNext Assistant: MCP Migration Plan, Status and Handover

Last updated: 14 Nov 2026. Branch: `mcp-integration` (cut from `integration-fixed` at `d3d75bd`).
Status: Members 1, 2 and 3 are COMPLETE. Members 4, 5 and 6 are pending.

This document is the work allocation plus the handover notes from Members 1 and 2. It is a plan, not evidence that work 3 to 6 is done.

## 1. Direction and security boundary

Build a separately maintainable Python MCP component while keeping the existing ERPNext Assistant, LangGraph workflow, widget, prompts, LLM and the 22 YAML skills. Extract ERPNext operations into a reusable tool layer (the `mcp_erpnext` package), then expose approved tools through MCP.

- Migrate READ operations first.
- Keep WRITE operations in the existing LangGraph/direct path until per-user ERPNext identity and authorization are implemented and verified.
- External MCP clients get read-only access only, and only after per-user identity works.

## 2. Branching and workflow

- `integration-fixed` is the known-good rollback branch. Do not modify it. It is merged into only after the first milestone is verified end to end.
- All members commit directly to `mcp-integration` (no per-member branches). Antony coordinates merges.
- Before starting and before every push: `git pull --rebase origin mcp-integration`.
- Run the tests (section 5) before every push. Do not push code that does not run. Do not force-push.
- Commit small and often, stage files by explicit path, and keep the shared files (`generic_tools.py`, `graph.py`, `main.py`, `ai_assistant/api.py`) changes small.
- Each stream starts with a read-only investigation and a checkpoint report (actual diffs and per-file test output) before any edit. Fixes are reviewed in labelled parts.
- `TOOL_BACKEND=direct` must stay available as an in-code rollback.
- Local safety tag on Antony's machine: `pre-default-company-fix` (`2bd85e9`).

## 3. Status

| Member | Stream | Status |
|---|---|---|
| 1 | ERPNext tool layer extraction | DONE (section 4) |
| 2 | Aggregation, reports, pagination | DONE (section 4b) |
| 3 | MCP server and shared security policy | DONE ( section 4c) |
| 4 | LangGraph adapter and MCP integration | Not started |
| 5 | Authentication and user identity | Not started |
| 6 | Testing, security, deployment | Not started (baseline numbers in section 5) |

## 4. Member 1 handover: what was delivered

### Commits on `mcp-integration` (oldest first, after `d3d75bd`)
- `704f987`, `ef68382` characterization tests for every tool (read, write, workflow, `delivery_date` sync, `get_count` limit)
- `93143d1` `mcp_erpnext` transport client, typed errors, credentials
- `fc73e44` `mcp_erpnext` crud and default-resolver modules
- `106fee1` `generic_tools` delegates to `mcp_erpnext`
- `4969ac6` do not send a partial Sales Order update when the current document cannot be read
- `9ecb84f` direct tests for default company/warehouse resolvers
- `47782d1` removed unused legacy functions from `erpnext_client.py`
- `c77ee99` the caller's client now reaches the default resolvers and document reads
- `529f7d9` direct crud tests, isolation guard, cache-key tests
Earlier on `integration-fixed` (also in this branch): `54143ac` default company/warehouse without silent fallbacks, `d3d75bd` company and warehouse shown in create confirmation and submit offer.

### Package layout (repo root, no imports from `agent/`)
- `mcp_erpnext/errors.py`: `ERPNextError` (subclasses `RuntimeError`), `ERPNextTransportError`, `ERPNextConfigError`, and the single `extract_erpnext_error`.
- `mcp_erpnext/client.py`: `Credentials` (base_url plus api_key/api_secret OR custom auth headers such as a Bearer token; `repr` is fully redacted), `get_env_credentials()` (reads `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` lazily at call time and fails visibly when missing), and `ERPNextClient(credentials=None)` with `get/post/put/delete`. It uses `with httpx.Client() as client:` per call, wraps `httpx.HTTPError` in `ERPNextTransportError` (original message kept), and never retries (so no write retries).
- `mcp_erpnext/defaults.py`: `get_default_company`, `get_default_warehouse`, `reset_resolver_caches`. Caches are keyed by (base_url, HMAC-SHA256 of the credential with a per-process random key), are guarded by a lock, and are shared by `tools.generic_tools` and `agent.tools.generic_tools`.
- `mcp_erpnext/crud.py`: `_format_result` (the single home of the envelope), whitelist helpers, and `get_list`, `create_document`, `update_document`, `delete_document`, `get_document`, `search_document`, `get_count`, `aggregate`, `cancel_document`, `submit_document`. Every function takes `client=` and uses it for ALL its calls; when no resolver or getter is injected, the resolvers and the document getter use that same client.
- `agent/tools/generic_tools.py`: thin wrappers with the SAME names, signatures and result envelopes. They refer to `get_default_company`, `get_default_warehouse` and `get_document` by plain name so existing test patches still apply. They have NO `client` argument (see open decision D1). It still has `import httpx` on purpose: tests patch `agent.tools.generic_tools.httpx.Client`.
- `agent/tools/erpnext_client.py`: reduced to `get_auth_headers`, `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` and `load_dotenv`. The six unused legacy symbols were deleted (no references anywhere).

### Behaviour changes (deliberate, all covered by tests)
1. Missing ERPNext credentials now return a `system_error` envelope with a configuration message. Before, an empty token was sent and the 401 came back as `permission_denied`.
2. Default company/warehouse: no hard-coded fallbacks. Failures raise `RuntimeError`; `create_document` turns them into a `system_error` envelope and does not POST. The warehouse default is resolved only when an item row lacks a warehouse.
3. Sales Order `delivery_date` update with no `items` in the payload: if the current document cannot be read, the update is aborted and the failed read's status and error are returned (before, a PUT without item rows was sent). Why ERPNext needs the item rows synchronised was NOT verified here; do not state it as fact.
4. Failures while reading the Company or Stock Settings default warehouse are logged as warnings, then fall through to the next preference.

### Preserved
Tool names, signatures, the envelope `{status, data, error, has_more, success}` and the statuses `success`, `empty`, `permission_denied`, `not_found`, `system_error`. Member 2 adds `total_count`, `offset`, `limit` and `truncated`.

### Manual smoke test
Antony ran the chatbot against his real ERPNext (company Metal Forms): a Sales Order was created, and the draft showed the Company and Warehouse lines and the submit offer.

## 4b. Member 2 handover: what was delivered

### Commits on `mcp-integration` (oldest first, after `db0eef9`)
- `c6e5b01` pagination metadata and a database-side `get_count`
- `0d76eaf` database-side `aggregate` with input validation and a Python fallback
- `675e3ce` Frappe 16 dict aggregates and `parent_doctype` child-table reads
- `450b453` item-level analytics in `graph.py` as one grouped call (the N+1 is gone)
- `4b71c70` ranked multi-field search with exact-match priority
- `6ff8add` row aggregation moved into `mcp_erpnext/aggregation.py`
- `0195d52` `run_report` with a fail-closed report allowlist (`mcp_erpnext/reports.py`)

### Contract changes (Members 3 and 4 read this)
- The read tools `get_list`, `get_count`, `search_document`, `aggregate` and `run_report` add `total_count`, `offset`, `limit` and `truncated` to successful and empty results. Errors and the write tools keep the original five-key envelope. `total_count` is `None` when it is not known (for example a list that was cut off and not asked for a total).
- `get_list` gained optional `offset` and `include_total` (the total costs one extra call and is only made on request). `group_by`, `parent_doctype` and `or_filters` are for internal use by the tool layer.
- `get_count` asks ERPNext to count (`frappe.client.get_count`). No rows are transferred and the old 999,999 ceiling is gone.
- `aggregate` gained `parent_doctype`. Inputs are validated before anything is sent: `group_by` and `metric` must be single field names, `aggregation_fn` must be one of sum, avg, count, max, min (an unknown function used to behave like max), `sort` must name the group or the metric, `limit` must be a non-negative integer. A null group value is reported as "Unknown" (it was "None") and a null metric counts as 0 (it used to raise). Ties are ordered alphabetically by group so results are repeatable. It asks ERPNext to group and sum, and falls back to the old fetch-and-sum path if ERPNext ignores the query or returns a system error. Permission and not-found results never trigger the fallback. At most 5,000 groups are requested per call; reaching that sets `truncated`.
- `search_document` searches every field mapped for the doctype (ID and display name), matches `%` and `_` literally, ranks starts-with before contains, and returns only the exact matches when any exist. A blank query is a `system_error`. It gained an optional `limit` (default 10).
- The wrappers in `agent/tools/generic_tools.py` only gained optional trailing arguments. Tool names and existing signatures are unchanged.

### run_report contract (Member 3 and Member 5 read this)
`run_report(report_name, filters=None, *, user_roles, row_filters=None, group_by=None, metric=None, aggregation_fn=None, sort=None, limit=None, client=None)`.
- `user_roles` has no default. It must be filled from trusted, authenticated context. `None`, an empty list, a bare string or an entry that is not a string all deny. An allowlist entry with an empty or missing `allowed_roles` denies everyone.
- Unknown reports, unlisted reports and role-denied reports all return the same `permission_denied` text, so the answer does not reveal which reports exist.
- The allowlist is data (`REPORT_ALLOWLIST`, read through `get_report_allowlist()`). Member 3 can replace that function when the shared policy exists. Each entry has `allowed_roles`, `max_rows` and a `filters` declaration (type, required, choices). Unknown filters are rejected, not forwarded.
- Current entries are operational only: Stock Projected Qty, Stock Ledger, Sales Analytics. Financial reports are not listed, and a test fails if one is added by accident. Adding them waits for per-user identity (Member 5). The role names in the allowlist are the standard ERPNext ones and were not checked against this site's role setup.
- Row filters allow only =, !=, >, >=, <, <=, contains. Aggregation allows only the five functions above. Rows are capped at the report's `max_rows` and never above 1,000.
- The report's total row (a list lined up with the columns, present when `add_total_row` is set) is returned as `data["totals"]` and is never counted as data.
- Reports that ERPNext runs in the background (Prepared Reports, for example Stock Balance on this site) return a `system_error` saying so. Stock Balance is one on this site, so it is not on the allowlist. Stock Ageing is also left off: it failed with a server error for the filters tried, and its required filters were not worked out.
- `run_report` is not wired into the chatbot or any skill.

### Behaviour verified against a live instance (Frappe 16.24, ERPNext 16.25, read only)
- Frappe 16 rejects SQL functions written as strings in `fields` ("sum(x) as y"). The dict form `{"SUM": "x", "as": "agg_value"}` works for SUM, COUNT, AVG, MAX and MIN, and `order_by` on the alias works.
- A child doctype such as Sales Order Item only returns its fields when the `parent` parameter names the parent doctype. Filters on parent fields use `[parent, field, operator, value]`. The grouped child-table result equalled a per-document ground truth for every item-level query type and filter variant.
- A GET with about 600 names in a filter returns 414 from nginx. Do not build long `in` lists into a GET; use the POST form of `frappe.client.get_list`.
- Aggregating report rows reproduced the report's own total row exactly.

### Open items and known limits
- Everything above was verified against one local instance. The aggregate fallback exists for other ERPNext versions. Its computation was checked live, but the switch to it (when ERPNext rejects or ignores the aggregate query) was only exercised with mocks.
- `search_document` ranks within the 50 rows it fetches. An exact match is always found (it gets its own lookup), but with more than 50 partial matches the best partial match is not guaranteed to be shown.
- The bulk-operation path in `graph.py` still reads targets with `get_list(limit=999999)` after taking an exact count. A bulk action over more than 999,999 documents would silently leave some out. The owner of `graph.py` should decide whether to cap bulk size.
- Search fields for Delivery Note and Purchase Receipt were only checked on empty tables (ERPNext accepted the fields).
- The genericity scan still does not cover `mcp_erpnext/crud.py` or `reports.py`.
- Test baseline after this work (`tests` and `agent/tests` run together): 655 passed, 1 failed. The one failure is the genericity scan described in section 5.

## 4c. Member 3 handover: MCP server and shared security policy

### Commit

- `5436b02` — Implement secure ERPNext MCP server
- Six new files created.
- 2,942 lines added.
- Working tree was clean after the commit.

### Files delivered

| File | Description |
|---|---|
| `mcp_erpnext/policy.py` | Centralized security policy covering RBAC, DocType allowlisting, report authorization, tool allowlisting, read-only restrictions and query validation. |
| `mcp_erpnext/schemas.py` | Pydantic v2 input/output schemas for all 11 MCP tools, with strict validation and rejection of unexpected arguments. |
| `mcp_erpnext/server.py` | MCP server using the official Python MCP SDK, supporting STDIO and Streamable HTTP transports. |
| `tests/test_policy.py` | 51 tests covering authorization, RBAC, allowlists and validation. |
| `tests/test_schemas.py` | 37 tests covering input schemas and validation. |
| `tests/test_server.py` | 26 tests covering tool registration, execution, authentication and transport security. |

### Security implementation

- Centralized authorization through `policy.py`.
- Separate allowlists for read and write tools.
- Read-only mode enabled by default.
- Empty or missing `allowed_roles` denies access.
- DocType access checked against the existing CRUD whitelist.
- Report access checked against the existing report allowlist.
- Strict validation of `order_by`, sorting and identifiers to prevent unsafe query expressions.
- Reserved identity fields such as `user_id`, `roles`, `read_only` and `credential` cannot be supplied through tool arguments.
- Fail-closed authorization: denied requests never reach the underlying CRUD or report functions.

### MCP server implementation

The server exposes all 11 existing ERPNext operations through MCP.

Each tool request passes through four stages:

1. Tool allowlist verification.
2. Pydantic schema validation.
3. Shared policy authorization.
4. Execution through existing `mcp_erpnext.crud` or `mcp_erpnext.reports` functions.

Supported transports:

- **STDIO:** Recommended for initial integration. Runs without requiring another network port and directs logs to `stderr`.
- **Streamable HTTP:** Supports Bearer JWT authentication, DNS rebinding protection and allowed-host validation.

### Authentication and security protections

- JWT verification uses HS256.
- JWT requires valid `sub` and `exp` claims.
- No hardcoded JWT secret fallback.
- JWT secret is resolved from `JWT_SECRET`, `JWT_SECRET_KEY` or `MCP_JWT_SECRET`.
- Development mock authentication is disabled in production.
- Sensitive credentials and tokens are masked in error output.
- HTTP transport uses explicit host validation rather than wildcard hosts.

### Test results

**Member 3 dedicated test results:**

| Test | Passed |
|---|---:|
| `test_policy.py` | 51 |
| `test_schemas.py` | 37 |
| `test_server.py` | 26 |
| `test_mcp_erpnext_isolation.py` | 1 |
| **Total** | **115** |

**Full regression results:**

- `tests`: 410 passed, 1 failed.
- `agent/tests`: 359 passed, 0 failed.
- Overall: 769 passed, 1 known baseline failure.

The remaining failure is `tests/test_generic_pipeline_genericity.py::test_static_scan_for_hardcoded_literals`, caused by pre-existing hardcoded literals in `agent/workflow/graph.py`. Member 3 did not modify that file.

### Handover to Member 4

- The MCP server and shared policy are ready for LangGraph integration.
- STDIO is recommended initially to avoid introducing another Docker container or network port.
- User identity and roles must come from trusted, verified server-side authentication context.
- Identity and roles must never be accepted from the LLM or user prompt.
- Keep `TOOL_BACKEND=direct|mcp` so the existing direct backend remains available for rollback.
- Keep MCP read-only until per-user identity and authorization are implemented and verified.

### Deployment requirements

- Configure a strong `JWT_SECRET` with at least 32 random bytes.
- Set `ENVIRONMENT=production` in production.
- If using HTTP alongside FastAPI on port 8000, configure MCP to use another port, such as `8001`.
- Configure `MCP_ALLOWED_HOSTS` explicitly when exposing HTTP.
- Keep STDIO read-only until the authentication integration is complete.

**Member 3 status: COMPLETE.** MCP server, shared security policy, schemas and dedicated tests have been implemented and committed as `5436b02`.

## 5. What every member must know

### Run the tests (PowerShell, from the repo root)
```
$env:PYTHONPATH="agent;."; $env:ALLOW_DEV_MOCK_AUTH="false"
python -m pytest tests -q
python -m pytest agent/tests -q
```
Baseline at `529f7d9`: `tests` gives 1 failed, 190 passed; `agent/tests` gives 358 passed. On bash use `PYTHONPATH=agent:.`.

### Known failing or fragile tests (not caused by the migration)
- `tests/test_generic_pipeline_genericity.py::test_static_scan_for_hardcoded_literals` fails on a hard-coded literal ('Customer') in `graph.py` `collect_parameters_node`, and the scan also matches other literals in `call_generic_tool_node`. It never scans `mcp_erpnext/crud.py`, so literals such as 'Sales Order' that used to sit in `generic_tools.create_document` are now invisible to it. Do not edit the test without agreeing it with Antony.
- `tests/test_auth.py::test_verify_token_missing_header` and `agent/tests/test_conversations_api.py::test_endpoints_require_a_token` fail only when a local `.env` sets `ALLOW_DEV_MOCK_AUTH=true`. They pass with `ALLOW_DEV_MOCK_AUTH=false`. Members 5 and 6: make them independent of the local `.env`.

### Conventions
- Do not rename tools. The 22 YAML skills depend on the names.
- Keep the result envelope. Do not add retries on writes.
- In new tests patch `mcp_erpnext.client.httpx.Client` (same module object as `agent.tools.generic_tools.httpx.Client`). Tests must set their own ERPNext environment variables and must not depend on a developer `.env`; `tests/test_generic_tools.py` has an autouse fixture that does this.
- `mcp_erpnext` must stay free of imports from `agent`, `tools`, `workflow`, `ai_assistant`, `main` and `dotenv` (a test enforces this). It never loads `.env`.
- `graph.py` imports `tools.generic_tools` and the tests import `agent.tools.generic_tools`. These are two module objects. State that used to be module-level now lives in `mcp_erpnext`, so it is shared.

## 6. Work allocation

### Member 1: ERPNext tool layer extraction: DONE
See section 4.

### Member 2: Aggregation, reports and pagination (new features): DONE
See section 4b for what was delivered. The text below is the original plan and Member 1's notes from before that work, so some of it (for example the `get_count` limit) no longer describes the code.
Responsibility: improve data retrieval and add missing ERPNext capabilities.
- Replace Python-side counting and summing with database-side aggregation where supported.
- Reduce repeated document retrievals (N+1 calls).
- Add pagination metadata `total_count`, `offset`, `limit`, `truncated`.
- Improve search.
- Implement `run_report` with a report-to-role allowlist, fixed aggregation functions, fixed filter operators and maximum result limits.
Files: `mcp_erpnext/aggregation.py`, `reports.py`, tool-layer integration files, and skill documentation if arguments change.
Deliverable: efficient aggregation, pagination and reports, tested against live ERPNext. Financial reports stay unavailable through the chatbot until per-user identity is verified.
Notes from Member 1:
- `get_count` requests `limit_page_length=1000000` and counts the returned rows in Python, so a count is silently capped at 999,999 and every row is loaded. `aggregate` also fetches rows (limit 999999) and sums in Python. `search_document` uses `get_list` with limit 10.
- Tests pin the current behaviour (`test_get_count_pins_limit_and_page_length_param` and the aggregate tests). Changing it means updating those tests on purpose.
- Work in `mcp_erpnext/crud.py` first; `generic_tools.py` is only a wrapper. Coordinate argument changes with the YAML skills.

### Member 3: MCP server and shared security policy (core MCP development)
See section 4c for what was delivered.
Responsibility: build the Python MCP server and centralise security rules.
- MCP server with STDIO and HTTP transports.
- Register the existing ERPNext operations as MCP tools.
- Shared policy for RBAC, doctype allowlists, report allowlists and order-by validation.
- Read-only mode for external clients; deny tools outside the allowlist.
- Role checks fail closed; empty `allowed_roles` must deny access.
- Caller identity, roles and read-only status come from trusted authenticated context, not request arguments.
Files: `mcp_erpnext/server.py`, `policy.py`, `schemas.py`; `agent/workflow/graph.py` for importing the shared policy.
Deliverable: a working MCP server exposing approved read tools with shared authorization.
Notes from Member 1:
- Build on `mcp_erpnext.crud` functions (they take `client=`). The doctype whitelist is `get_allowed_doctypes` / `is_doctype_allowed` (`DOCTYPE_WHITELIST` or `ALLOWED_DOCTYPES`). `order_by` validation is `ORDER_BY_PATTERN`. RBAC role checks currently live in `graph.py` (`_check_role_access`).
- The package is not in `pyproject.toml` and never loads `.env`. A standalone server process relies on the repo root being on `sys.path` and must load its own environment (section 8).
- `ERPNextError` subclasses `RuntimeError`.

### Member 4: LangGraph adapter and MCP integration (agent integration)
Responsibility: connect the existing LangGraph agent to the MCP server without breaking the 22 skills.
- `ToolAdapter` and `TOOL_BACKEND=direct|mcp`; an MCP client.
- Preserve tool names and result envelopes; keep direct mode as a rollback.
- Migrate reads first; keep writes in the LangGraph/direct path until per-user identity is verified.
- Handle MCP connection errors and timeouts without silently bypassing security.
- Keep the multi-turn confirmation flow in LangGraph.
Files: `agent/tools/adapter.py`, `mcp_client.py`, `__init__.py`, `agent/workflow/graph.py`, `agent/main.py`, `.env.example`.
Deliverable: direct and MCP backends working with the existing skills, with reads migrated.
Notes from Member 1:
- `graph.py` imports `get_default_company` and `get_default_warehouse` from `tools.generic_tools` and calls them directly (confirmation text and submit offer). These bypass any adapter; route them through it, and keep catching only `RuntimeError` there.

### Member 5: Authentication and user identity (critical security)
Responsibility: make ERPNext recognise the actual user behind each request.
- Spike comparing per-user Frappe API credentials, a Frappe-side signed assertion endpoint and OAuth; pick and implement one.
- Put user identity into the MCP request context; verify identity, roles and read-only status from trusted authentication data.
- Replace the shared service key with per-user identity where supported; scoped read-only credentials for external MCP clients.
- Harden `verify_token`, CORS and the development mock authentication.
Files: `ai_assistant/ai_assistant/api.py`, `agent/main.py`, `mcp_erpnext/auth.py`, client credential integration, `useDemoChat.ts` if token exchange changes.
Deliverable: per-user authentication demonstrated with two users who have different permissions and receive their own ERPNext permissions.
Notes from Member 1:
- `Credentials` already supports api_key/secret and custom headers (for example `Authorization: Bearer ...`), and `ERPNextClient(credentials=...)` uses them for every call. Resolver caches are keyed by an HMAC of the credential, so two users never share a cached company or warehouse. Today every user still shares the one service key from `.env`, so the cache is effectively global until your work lands.
- Open decision D1 is yours to settle with Member 4.
- Dev mock auth is controlled by `ALLOW_DEV_MOCK_AUTH`.

### Member 6: Testing, security and deployment (quality and deployment)
Responsibility: the migration works correctly, securely and reliably.
- Clean test baseline (section 5) and the existing failing tests.
- Tests for aggregation, reports, pagination and direct-versus-MCP parity; MCP tool registration and execution.
- Read-only tokens blocked from writes, restricted reports, disallowed tools, empty `allowed_roles`; unauthenticated access denied.
- Write safety: no automatic retries on writes; send `modified` on update and cancel.
- Dockerfiles and Docker Compose with persistent volumes for SQLite files; README and deployment documentation.
Files: existing tests; `tests/test_mcp_server.py`, `test_security_isolation.py`, `test_pagination_and_reports.py`, `Dockerfile`, `docker-compose.yml`, README.
Deliverable: passing functional and security tests, working deployment configuration, updated documentation.
Notes from Member 1:
- `mcp_erpnext` is not packaged by `pyproject.toml` and only works when the repo root is on `sys.path` (in dev an editable install provides that). Docker images and a plain `uvicorn` start in a clean environment need `PYTHONPATH` or an install step.
- `mcp_erpnext` never loads `.env`; set the ERPNext variables in the container environment.
- Two auth tests depend on the local `.env` (section 5).

## 7. Coordination

### Recommended sequence
| Order | Members | Work |
|---|---|---|
| 1 | All | Phase 0 security fixes and agreement on shared contracts |
| 2 | 1 (done), 3, 5, 6 | Tool extraction (done), MCP design, identity spike, test preparation in parallel |
| 3 | 2 | Aggregation and reports against the extracted tool layer |
| 4 | 4 | Integrate LangGraph after Member 3 settles the MCP schemas; reads first |
| 5 | 3, 4, 5 | Verified identity and authorization before enabling more operations or external access |
| 6 | All | Integration tests, security review, deployment hardening |

### Shared agreements
- Request context: `user_id`, `roles`, opaque credential, `read_only`, `request_id`.
- Result format: preserve `{status, data, error, has_more, success}`; add `total_count`, `offset`, `limit`, `truncated`.
- Tool names: do not rename existing tools.
- Security: the MCP server verifies the caller from trusted authentication context, never from request arguments.
- Writes stay in LangGraph until per-user identity works.
- No merge without relevant tests and end-to-end verification.

### Shared-file ownership
| File | Ownership |
|---|---|
| `generic_tools.py` | Member 1 (done), then Member 2 |
| `graph.py` | Member 3 prepares shared policy; Member 4 integrates |
| `main.py` | Member 5 authentication; Member 4 tool integration |
| `ai_assistant/api.py` | Member 5 identity; Member 2 only if custom aggregation methods are needed |
| `mcp_erpnext/crud.py` | Member 2 for aggregation and pagination; others coordinate with Member 2 |

## 8. Open decisions and known issues

### Decisions needed
- D1 (Members 4 and 5): the `generic_tools` wrappers cannot receive per-request credentials (no `client` argument, and their signatures must stay unchanged for the 22 skills). Proposed: a request-scoped context variable that `mcp_erpnext` reads, with the environment service key as the fallback. Settle this before building identity or the adapter.

### Known issues and open items
- The resolver caches have no TTL or size limit.
- `ERPNextConfigError` is re-raised as a plain `RuntimeError` inside the default resolvers (message kept) for compatibility with `graph.py`.
- The nine broad `except Exception` blocks in `crud.py` were kept on purpose: they preserve the "Invalid JSON response" `system_error` envelope for non-object responses. Narrowing them needs a decision.
- `mcp_erpnext` is not packaged and never loads `.env` (Members 3 and 6).
- The genericity scan does not cover `mcp_erpnext/crud.py` (section 5).
- The Sales Order `delivery_date` behaviour of ERPNext was not verified; the abort-on-failed-read change is a safety rule, not a documented ERPNext fact.

## 9. First milestone

A secure, read-only MCP integration working with the existing chatbot: the chatbot keeps working; LangGraph retrieves ERPNext data through MCP; existing skills are unchanged; unauthorized tools are rejected; external agents cannot write; direct mode remains available for rollback; per-user identity is verified before external exposure.
