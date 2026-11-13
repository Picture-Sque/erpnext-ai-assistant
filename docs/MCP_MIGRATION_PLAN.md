# ERPNext Assistant: MCP Migration Plan, Status and Handover

Last updated: 13 Nov 2026. Branch: `mcp-integration` (cut from `integration-fixed` at `d3d75bd`).
Status: Member 1 is COMPLETE. Members 2 to 6 have not started.

This document is the work allocation plus the handover notes from Member 1. It is a plan, not evidence that work 2 to 6 is done.

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
| 2 | Aggregation, reports, pagination | Not started |
| 3 | MCP server and shared security policy | Not started |
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

### Member 2: Aggregation, reports and pagination (new features)
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
