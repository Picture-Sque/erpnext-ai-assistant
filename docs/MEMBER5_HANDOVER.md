## 4e. Member 5 handover: Authentication, User Identity, and Per-User ERPNext Sessions

Last updated: 14 Nov 2026. Branch: `member5-stage2b` (local only, no push).
Base: `origin/mcp-integration @ 48ca6a9`.
Status: Parts A through L, Stage 1 hardening cleanup, and Part L live proofs are COMPLETE and VERIFIED.

---

### Commits on `member5-stage2b` (Oldest First, Rebased on `origin/mcp-integration` @ `48ca6a9`)

All 16 commits on this branch:

- `aa150be` feat(mcp-erpnext): add RequestContext and contextvar management for user identity (Part A)
- `aedecb3` feat(mcp-erpnext): integrate RequestContext credentials into ERPNextClient with fallback (Part B)
- `03f80d6` feat(agent): harden authentication, CORS, and bind RequestContext in main.py (Part C)
- `b133358` test(auth): isolate test_verify_token_missing_header and test_endpoints_require_a_token from env mock auth (Part D)
- `b5b5cfb` refactor(agent): clean up whitespace and redundant context indentations in main.py (Part 3A)
- `fa8894f` feat(agent): add fail-closed role gate on direct tools API (Part 3B)
- `9e59bf2` feat(mcp-erpnext): unify RequestContext into auth.py (Part E)
- `3740819` refactor(mcp-erpnext): make RequestContext credentials read-only after construction (Part E.1)
- `8baea0f` feat(mcp-erpnext): bind server identity to unified ContextVar (Part F)
- `8c1e235` feat(auth): mint and verify Fernet-encrypted session credentials in JWT (Part G)
- `9c1e1e0` fix(auth): fail closed on unset/malformed key and empty session sid (Part G.1)
- `1bf67fb` feat(auth): enforce read-only scope claim with transport backstop (Part H)
- `a313900` feat(auth): harden identity resolution, dev-mock guard, and session expiration (Part I+J)
- `35fd917` fix(auth): clean up test cookies, context flag thread-safety, and test coverage (Part I cleanup)
- `a9c66b5` docs: add Member 5 handover and test suite baseline documentation (Part K)
- `d9bd490` feat(mcp): propagate per-user session credentials and enforce read-only HTTP transport (Part L)

---

### Token Claims Contract

The JWT issued by ERPNext Desk (`ai_assistant/ai_assistant/api.py:get_auth_token`, `get_readonly_auth_token`) and validated by the agent service (`agent/main.py:verify_token`, `_build_request_context`) contains the following claims:

| Claim | Type | Required | Description |
|---|---|---|---|
| `sub` | `str` | Yes | Subject identifier; the authenticated ERPNext user email (e.g. `sandra@gmail.com`, `Administrator`). Derived directly from `frappe.session.user`. |
| `full_name` | `str` | Yes | User display name derived from `frappe.utils.get_fullname(frappe.session.user)`. |
| `roles` | `List[str]` | Yes | List of role profile names assigned to the user in ERPNext at token mint time, resolved via `frappe.get_roles(session_user)` (`api.py:27`). |
| `ctx` | `str` | Yes | Fernet-encrypted JSON payload (`{"sid": "...", "csrf_token": "..."}`) containing the active Frappe session ID and CSRF token. Encrypted with `credential_enc_key` so secret session identifiers are unreadable by the browser/client. |
| `scope` | `str` | Optional | Scope claim. Omitted for standard writable tokens (`_mint_auth_token(read_only=False)`). Present with value `"read"` for read-only tokens (`_mint_auth_token(read_only=True)`). Handled in `agent/main.py:_build_request_context`: if absent, `read_only = False`; if exactly `"read"`, `read_only = True`; for ANY other value/type/null, fails closed to `read_only = True`. Note: there is no `"scope": "all"` in the codebase. |
| `iat` | `datetime` | Yes | Token issue timestamp (`datetime.now(timezone.utc)`). |
| `exp` | `datetime` | Yes | Token expiration. For read-only tokens, governed by `frappe.conf.get("readonly_token_ttl_minutes")` / `READONLY_TOKEN_TTL_MINUTES` (defaults to 15 minutes). For standard tokens, set to `now + timedelta(hours=1)` (1 hour). |

---

### RequestContext Construction & Architecture

Every incoming agent turn enters `_build_request_context(payload: dict) -> RequestContext` in `agent/main.py`. The resulting immutable context is bound via `with set_request_context(ctx):` in `mcp_erpnext/auth.py`.

Fields of `RequestContext` (`@dataclass(frozen=True)` in `mcp_erpnext/auth.py:26-38`):
- `user_id: Optional[str] = None`: User email/sub (e.g., `sandra@gmail.com`).
- `roles: Tuple[str, ...] = ()`: Normalized immutable tuple of role strings. Empty tuple if omitted or malformed.
- `is_admin: bool = False`: True if `"System Manager"` or `"Administrator"` is present in `roles`.
- `is_guest: bool = False`: True if `user_id == "Guest"` or `roles` is empty.
- `credentials: Optional[Any] = field(default=None, repr=False)`: Instance of `mcp_erpnext.client.Credentials` carrying `base_url` and `auth_headers={"Cookie": "sid=...", "X-Frappe-CSRF-Token": "..."}`. Redacted in string/repr representation to prevent leakage in logs or tracebacks. (Accessible via read-only property alias `credential`).
- `correlation_id: Optional[str] = None`: Tracing UUID for request logs (`req-xxxxxxxxxxxx`). Synchronized with `request_id`.
- `read_only: bool = True`: Boolean enforcement flag. Defaults to `True`. Evaluated strictly from `scope` claim in `agent/main.py`.
- `request_id: Optional[str] = None`: Alias for `correlation_id`.
- `dev_mock: bool = False`: Boolean flag. `True` ONLY when explicitly generated via dev-mock authorization without token header; otherwise `False`.
- `_session_expired_flag: list = field(default_factory=lambda: [False], repr=False, init=False)`: Internal mutable single-element list `[False]`, created per-context to survive thread hops (AnyIO/LangGraph worker threads) without using global pointer tables.

---

### READ_ONLY_POST_ALLOWLIST Location & Status

- **Location**: Defined in `mcp_erpnext/client.py:11` as `READ_ONLY_POST_ALLOWLIST: Set[str] = set()`.
- **Value**: Currently an empty set `set()`. It does NOT live in `auth.py`.
- **Enforcement**: In `mcp_erpnext/client.py:136`, when `ctx.read_only` is `True`, any POST request whose normalized path is not in `READ_ONLY_POST_ALLOWLIST` raises `ReadOnlySessionError`.
- **Testing**: `tests/test_read_only_transport.py:82` monkeypatches `mcp_erpnext.client.READ_ONLY_POST_ALLOWLIST` with `{"/api/method/frappe.client.get_list"}` to verify that allowlisted RPC POSTs are permitted while unallowlisted POSTs and path traversal attempts are rejected.

---

### Behaviour Changes (Deliberate & Tested)

1. **Session Forwarding (Part G & G.1)**:
   - Tokens carry encrypted active session `sid` and `csrf_token`.
   - `ERPNextClient` communicates with Frappe REST endpoints using the user's active session cookie (`Cookie: sid=...`), causing ERPNext native document-level permissions, role permissions, and ownership audit fields (`owner`, `modified_by`) to apply directly to the authenticated user.
   - Missing or empty `sid` in `ctx` fails closed immediately with HTTP 401.

2. **Read-Only Scope Enforcement (Part H)**:
   - Signed `scope` claim in JWT controls `ctx.read_only`.
   - **Transport Backstop**: `ERPNextClient._send()` inspects `ctx.read_only`. If `True`, mutating HTTP methods (`POST`, `PUT`, `DELETE`) are rejected with `ReadOnlySessionError` before hitting the network, except for explicitly allowlisted read-only RPC POST endpoints in `READ_ONLY_POST_ALLOWLIST`.

3. **No Silent Service-Key Fallback (Part I-1)**:
   - When a request executes inside an active user context, if `ctx.credentials` is `None` and `dev_mock=False`, `ERPNextClient.get_credentials()` raises `ERPNextConfigError` rather than silently substituting the `.env` service keys.
   - When outside any request context, `ERPNEXT_STRICT_IDENTITY` controls behavior: if true, fails closed with `ERPNextConfigError`; if false, permits legacy background tools to fall back to service keys.

4. **Session Expiration & HTTP 401 Propagation (Part I-2 & Stage 1)**:
   - When ERPNext returns HTTP 401 or HTTP 403 with `{"session_expired": 1}`, `ERPNextClient._send()` calls `mark_session_expired()`.
   - The thread-safe context flag propagates across worker threads back to `agent/main.py:chat_endpoint()`.
   - The agent aborts the turn and returns HTTP 401 with header `WWW-Authenticate: Bearer`, prompting the Desk chat widget to refresh its token.

5. **Dev-Mock Guard (Part I-3)**:
   - `ALLOW_DEV_MOCK_AUTH=true` is strictly prohibited if `ERPNEXT_BASE_URL` hostname is anything other than `localhost`, `127.0.0.1`, or `::1`. If configured with an external host, dev-mock auth is automatically refused, disabled, and logged as an error on startup.

6. **MCP Server Per-User Identity (Part L)**:
   - `BearerAuthMiddleware` in `mcp_erpnext/server.py` now parses the JWT's encrypted `ctx` claim, decrypts `sid` and `csrf_token`, and attaches `Credentials` to `RequestContext`.
   - External clients over HTTP transport are unconditionally forced to `read_only=True`; write tools are blocked server-side by `mcp_erpnext.policy` before reaching `crud.py`.
   - `InMemoryMCPClient` in `mcp_erpnext/server.py` preserves `current_request_context` and fails closed if no context is active under strict identity.
   - Parity between direct backend and `ToolAdapter(backend='mcp', mcp_client=InMemoryMCPClient())` is verified without modifying Member 4's `adapter.py` or `mcp_client.py`.

---

### Verified Facts & Evidence

| Fact | Verification Method | Status |
|---|---|---|
| Per-user document ownership (`owner`, `modified_by`) matches the caller | Part J live check with real ERPNext: created Sales Orders audited in database (`owner='Administrator'` vs `owner='sandra@gmail.com'`). | VERIFIED |
| Permission isolation across users | Part J live check: Administrator allowed GET `/api/resource/Sales Invoice` (200 OK); Sandra forbidden (403 Forbidden). **Note: this was a direct ERPNext REST API check, NOT via `/chat`.** | VERIFIED |
| `/chat` behavior for Administrator vs Sandra | Part J live check: Customer lookup (read) and Sales Order creation (write) succeeded for both users, creating documents with caller's `owner`/`modified_by`. The cancel request returned a draft rejection ("Sales Order is in Draft status — only Submitted documents can be cancelled") for **both** users, proving business precondition handling rather than a permission divergence. | VERIFIED |
| Read-only tokens block write verbs | Unit tests `test_readonly_transport_blocks_writes_before_httpx_is_called` and `test_require_writable_behavior` in `tests/test_read_only_transport.py`. | VERIFIED |
| Read-only allowlist permits RPC reads | Unit test `test_readonly_transport_allows_allowlisted_post_and_denies_traversals` in `tests/test_read_only_transport.py:71`. | VERIFIED |
| Missing user credentials in context fails closed | Unit test `test_ctx_with_credentials_none_and_not_dev_mock_never_reaches_env` in `tests/test_part_i_hardening.py`. | VERIFIED |
| Session expiration triggers HTTP 401 with `WWW-Authenticate: Bearer` | Unit test `test_session_expired_sets_flag_and_chat_returns_401` in `tests/test_part_i_hardening.py` and Part J live session logout check. | VERIFIED |
| Session expired flag survives thread hops without memory address collisions | Unit test `test_session_expired_flag_survives_thread_hop_and_is_isolated_between_requests` in `tests/test_part_i_hardening.py`. | VERIFIED |
| Resolver cache isolation between sessions | Unit test `test_two_different_session_credentials_never_share_defaults_cache` in `tests/test_part_i_hardening.py`. | VERIFIED |
| Dev-mock refusal on non-localhost hosts | Unit test `test_dev_mock_refusal_on_non_local_base_url` in `tests/test_part_i_hardening.py`. | VERIFIED |
| Unified JWT verification and fail-closed base URL | Unit tests `test_verify_jwt_unified_helper` and `test_base_url_fails_closed_when_unset` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| MCP HTTP transport forces read-only and denies all 5 write tools | Unit test `test_http_forces_read_only_and_denies_write_tools_for_all_external_tokens` and Step 2 live MCP server check on port 8009. | VERIFIED |
| MCP HTTP rejects unauthenticated/invalid/expired before tool execution | Unit test `test_http_rejects_unauthenticated_requests_before_tool_runs` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| Multi-user concurrency preserves identity without leakage | Unit test `test_concurrent_requests_with_different_users_do_not_leak_identity` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| InMemoryMCPClient enforces active context under strict identity | Unit test `test_in_memory_mcp_client_requires_active_context_in_strict_mode` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| STDIO transport is never writable and refuses to start without credentials | Unit test `test_stdio_transport_guards_refuses_writable_and_unconfigured_credentials` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| Backend parity (direct vs MCP) preserves caller session | Unit test `test_direct_backend_and_in_memory_mcp_parity` in `tests/test_part_l_mcp_identity.py`. | VERIFIED |
| Live instance token minting & read-only write protection (0 mutation) | Step 2 live checks against `http://localhost:8080`: normal token creates/deletes customer; read-only token blocks writes in client backstop and policy; database confirmed 0 mutation (HTTP 404). | VERIFIED |
| All 384 existing agent tests pass | Verified via `$env:PYTHONPATH="agent;."; $env:ALLOW_DEV_MOCK_AUTH="false"; python -m pytest agent/tests -q`. | VERIFIED |

---

### Open Decision D1 Resolution

**Context:** The existing `generic_tools.py` functions are called by 22 YAML skills and LangGraph workflow nodes without any `client` argument, and their function signatures and return envelopes could not be modified without breaking skills.
**Resolution:** Member 5 introduced a request-scoped Python `contextvars.ContextVar` (`current_request_context` in `mcp_erpnext/auth.py`), set at the request boundary in `agent/main.py`. Both `mcp_erpnext.client.ERPNextClient` and `mcp_erpnext.server.InMemoryMCPClient` read this active context automatically.
**Result:** All 22 YAML skills and `generic_tools` function signatures remain 100% unchanged, while per-user credentials and read-only status transparently govern all underlying ERPNext REST transport calls.

---

### Desk Widget & `useDemoChat.ts` Compatibility

`frontend/src/hooks/useDemoChat.ts` required **zero code changes** for per-user identity:
1. **Contract Unchanged:** `useDemoChat.ts` requests a JWT by calling Frappe's whitelisted endpoint `ai_assistant.ai_assistant.api.get_chat_token`, expecting a response payload containing `{ "message": { "token": "<jwt>" } }`.
2. **Backwards Compatibility:** In `ai_assistant/ai_assistant/api.py:106`, `get_chat_token()` is an alias for `get_auth_token()`. The encrypted session credentials (`sid` and `csrf_token`) are packed inside the JWT's `ctx` claim, completely transparent to the React frontend.
3. **Automatic Refresh on 401:** `useDemoChat.ts` natively listens for HTTP 401 responses and triggers a one-time token refresh by re-calling `get_chat_token`, ensuring that when a session expires, the widget attempts to refresh gracefully.

---

### CORS Architecture & Decision

1. **Agent Service Gateway (`agent/main.py`):**
   - Configured via `CORS_ALLOWED_ORIGINS` (comma-separated, whitespace-trimmed).
   - **Fail-Closed by Default:** If `CORS_ALLOWED_ORIGINS` is unset, no cross-origin requests are allowed (empty origin allowlist), except localhost origins when `ALLOW_DEV_MOCK_AUTH` is explicitly enabled.
   - **Wildcard Refusal:** Wildcard `"*"` is strictly disallowed when `allow_credentials=True`. Any `"*"` entry in `CORS_ALLOWED_ORIGINS` is dropped with a logged security warning.
   - **Restricted Methods & Headers:** Methods are restricted to what the widget and agent API use (`["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]`). Headers are restricted to `["Authorization", "Content-Type"]`.
2. **Frappe Side CORS Configuration:**
   - The assistant app does not touch Frappe CORS.
   - *Note:* The assertion that Frappe reads `allow_cors` from `site_config.json` is a standard Frappe framework capability, but is marked **[UNVERIFIED]** for custom headers on this specific bench instance.

---

### Status of `require_writable()` and Write Routing

The five write functions in `mcp_erpnext/crud.py` are:
1. `create_document` (`crud.py:233`)
2. `update_document` (`crud.py:309`)
3. `delete_document` (`crud.py:370`)
4. `cancel_document` (`crud.py:753`)
5. `submit_document` (`crud.py:760`)

**Audit Status**:
- `require_writable()` is **NOT** called in `mcp_erpnext/crud.py` at all.
- Neither Member 3 nor Member 4 inserted `require_writable()` into `crud.py`.
- **Effect on Error Envelopes**:
  - When `read_only=True`, calling `crud.py` write operations triggers `ERPNextClient._send()` transport check, which raises `ReadOnlySessionError`.
  - Because `crud.py` catches generic exceptions, it maps `ReadOnlySessionError` to `{"status": "system_error", "message": "Operation failed: Session is read-only..."}` instead of `{"status": "error", "error": "permission_denied"}`.
  - However, the server policy layer (`mcp_erpnext.server.dispatch_tool` and `mcp_erpnext.policy`) still blocks writes over MCP with `permission_denied`.
  - **Action Required for Owners:** Member 3 (`crud.py`) and Member 4 (`adapter.py`) must be informed to add `require_writable()` to `crud.py` write functions or route writes through MCP policy. Member 5 leaves these files untouched.
- **Adapter Routing**: In `agent/tools/adapter.py:352-365`, all five write methods call `generic_tools.<func>` directly, completely bypassing MCP:
  ```python
  def create_document(self, doctype: str, fields: dict) -> dict:
      return generic_tools.create_document(doctype=doctype, fields=fields)
  def update_document(self, doctype: str, name: str, fields: dict) -> dict:
      return generic_tools.update_document(doctype=doctype, name=name, fields=fields)
  def delete_document(self, doctype: str, name: str) -> dict:
      return generic_tools.delete_document(doctype=doctype, name=name)
  def cancel_document(self, doctype: str, name: str) -> dict:
      return generic_tools.cancel_document(doctype=doctype, name=name)
  def submit_document(self, doctype: str, name: str) -> dict:
      return generic_tools.submit_document(doctype=doctype, name=name)
  ```
- Because writes currently bypass MCP and go through `generic_tools`, write enforcement depends entirely on `generic_tools` calling `ERPNextClient`, where Member 5's transport backstop blocks mutations when `read_only=True`. For MCP write tools to be protected in `TOOL_BACKEND=mcp`, Member 4 needs this handover to route writes through MCP with `require_writable` or context propagation.

---

### Deployment Configuration for Member 6

1. **`credential_enc_key`**:
   - Frappe site config key: `"credential_enc_key"` in `site_config.json` (or env var `CREDENTIAL_ENC_KEY`).
   - Must match `CREDENTIAL_ENC_KEY` in `agent/.env`.
   - Must be a 32-byte url-safe base64 Fernet key generated via:
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - If missing or invalid, both ERPNext token minting and agent token verification will fail closed with 401/ValidationError (`ai_assistant/api.py:20`, `agent/main.py:321`).

2. **`jwt_secret`**:
   - Frappe site config key: `"jwt_secret"` in `site_config.json` (or env var `JWT_SECRET`).
   - Must match `JWT_SECRET` in `agent/.env` (`ai_assistant/api.py:16`, `agent/main.py:29`).

3. **Required Environment Variables**:
   - `ALLOW_DEV_MOCK_AUTH="false"`: Must always be false in production environments.
   - `ERPNEXT_STRICT_IDENTITY="true"`: Recommended for production agent service to prevent unauthenticated fallback to service keys.
   - `READONLY_TOKEN_TTL_MINUTES="15"`: Lifetime in minutes for read-only scoped tokens (`ai_assistant/api.py:43`).

4. **Base URL Guard**:
   - If `ALLOW_DEV_MOCK_AUTH` is set to true while `ERPNEXT_BASE_URL` points to an external hostname or IP address, the agent service will refuse to start / refuse to enable dev-mock auth and log an error (`agent/main.py:53-73`).

---

### Stage 2B Design Note (UNIMPLEMENTED)

> [!NOTE]
> The following describes proposed architectural extensions for Stage 2B beyond the current single-host Frappe session forwarding. All items below are UNIMPLEMENTED and UNVERIFIED.

- **Per-User OAuth2 / Token Exchange [UNIMPLEMENTED]**: A future extension where Frappe acts as an OAuth2 Authorization Server issuing scoped user tokens directly to the agent without forwarding raw session cookies. (UNVERIFIED)
- **Granular Field-Level Permissions [UNIMPLEMENTED]**: Enforcing field-level DocPerm evaluation in `mcp_erpnext` before sending payload to ERPNext. (UNVERIFIED)
- **Key Rotation Scheme [UNIMPLEMENTED]**: Supporting `MultiFernet` with primary and fallback encryption keys for zero-downtime key rotation. (UNVERIFIED)

---

### Known Limits

1. **Fixed Token Roles**: User roles in the JWT are captured at token generation time. If an administrator modifies a user's roles in ERPNext, the user's active token retains the previous roles until the session is refreshed or expired.
2. **Resolver Cache Lifetime**: Default company and warehouse caches in `mcp_erpnext/defaults.py` are keyed per credential hash but have no wall-clock TTL; they persist for the lifetime of the process or until `reset_resolver_caches()` is called.
3. **No Key Rotation**: `CREDENTIAL_ENC_KEY` currently expects a single Fernet key; multiple simultaneous keys are unsupported.
4. **Session Lifetime Coupling**: Access validity is bound to the Frappe web session. Logging out of ERPNext Desk terminates the session and invalidates subsequent agent requests with HTTP 401.
5. **Library Defaults**: `ERPNEXT_STRICT_IDENTITY` defaults to `"false"` in the library code (`mcp_erpnext/client.py`) so that standalone non-request unit tests from other members do not break, and is set to `"true"` in `agent/.env.example`.
6. **In-Process ContextVar Boundary**: Identity and per-user credentials pass through Python's `contextvars.ContextVar` for the in-process MCP client (`InMemoryMCPClient`) only. A remote external HTTP MCP backend would require `ToolAdapter` to forward the user JWT token explicitly in the HTTP `Authorization: Bearer <token>` header.

---

### Test Suite Baseline and .env Investigation

Measured at commit `97cb74d` (HEAD):
- **`agent/tests`**: **387 passed**, 0 failed.
- **`tests` (Root Suite)**: **486 passed**, **1 pre-existing baseline failure in a clean environment** (no local `agent/.env` / `DOCTYPE_WHITELIST` unset).
- New failures introduced by Member 5: **0**. All 120 Member 5 tests across both suites pass.

#### Matrix Attribution Result (.env vs Pre-Existing Failures)

A matrix run across `merge-base` (`48ca6a9`), `origin tip` (`48ca6a9`), and `HEAD` (`97cb74d`) under both `env set` and `env cleared` verified:
- **1 test fails regardless of environment (pre-existing baseline failure)**:
  - `tests/test_generic_pipeline_genericity.py::TestGenericPipelineGenericity::test_static_scan_for_hardcoded_literals`:
    Fails with `AssertionError: <re.Match object; span=(4015, 4023), match='customer'> is not None : Found forbidden hardcoded literal 'Customer' in graph node 'collect_parameters_node'!`.
    **Owner**: Member 4 (`agent/workflow/graph.py`).
- **7 tests fail ONLY when `agent/.env` defines `DOCTYPE_WHITELIST`**:
  `agent/.env` defines `DOCTYPE_WHITELIST=Customer, Item, Sales Order, Bin, Quotation, Sales Invoice, Purchase Order, Supplier, Sales Order Item, Purchase Order Item, Company, Warehouse` (12 DocTypes, omitting `Stock Ledger Entry`, `Delivery Note`, `Purchase Receipt`).
  In contrast, code defaults in `mcp_erpnext/crud.py:DEFAULT_WHITELIST` include all 17 DocTypes (including `Stock Ledger Entry`).
  When `agent/.env` is loaded into the test process by `generic_tools.py:load_dotenv()`, the following 7 tests fail; **when `DOCTYPE_WHITELIST` is cleared, all 7 pass on merge-base, origin tip, and HEAD**:
  1. `tests/test_generic_tools.py::TestGenericTools::test_custom_whitelist_env`: Fails on `self.assertNotIn("DOCTYPE_WHITELIST", os.environ)` because `agent/.env` populated the variable.
  2. `tests/test_generic_tools.py::TestGenericTools::test_get_list_invalid_direction_and_malformed_clauses_rejected`: Calls `get_list("Stock Ledger Entry", ...)` which fails at the whitelist check (`permission_denied`) before testing SQL injection / order_by direction.
  3. `tests/test_generic_tools.py::TestGenericTools::test_get_list_sql_injection_rejected_before_http_call`: Calls `get_list("Stock Ledger Entry", ...)` which fails at the whitelist check before SQL injection validation.
  4. `tests/test_generic_tools.py::TestGenericTools::test_get_list_valid_multi_order_by`: Calls `get_list("Stock Ledger Entry", ...)` which fails with `permission_denied` at the whitelist check.
  5. `tests/test_generic_tools.py::TestGenericTools::test_get_list_valid_single_order_by`: Calls `get_list("Stock Ledger Entry", ...)` which fails with `permission_denied` at the whitelist check.
  6. `tests/test_generic_tools.py::TestGenericTools::test_workflow_call_generic_tool_node_order_by_passthrough`: Calls `call_generic_tool_node` for intent `stock-movement-analysis` with doctype `Stock Ledger Entry`. `graph.py:2182` rejects it with `DocType 'Stock Ledger Entry' is not permitted by whitelist`, preventing `get_list` from being called.
  7. `tests/test_mcp_erpnext_crud.py::TestMCPErpnextCrudDirect::test_search_unmapped_doctype_searches_name_only`: Calls `search_document("Stock Ledger Entry", "Entry")`. `crud.py:484` returns `permission_denied` before calling `client.get`, causing `_, kwargs = self.mock_client.get.call_args` to raise `TypeError: cannot unpack non-iterable NoneType object`.
  **Owner of the 7 tests**: Member 3 (`mcp_erpnext/crud.py`, `tests/test_generic_tools.py`, `tests/test_mcp_erpnext_crud.py`) and Member 1/4 (`graph.py`).

#### Line Number Shift in `test_generic_tools.py`
In `test_generic_tools.py`, `test_workflow_call_generic_tool_node_order_by_passthrough` was originally added in commit `3daf050`. It shifted down to line 518 (with failure at line 538) due to earlier additions:
- Commits `c6e5b01` (pagination metadata and database-side `get_count`) and `4b71c70` (ranked multi-field search) added tests and assertions.
- Fixtures `reset_globals_and_env` and `erpnext_env_defaults` were inserted at lines 38–60.
