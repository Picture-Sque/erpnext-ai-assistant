# Design Memo: Per-User ERPNext Credentials & Identity Propagation (Part 3)

**Author:** Member 5 (Authentication & User Identity)  
**Date:** 2026-11-14  
**Status:** Implemented: Option C (session forwarding); pending Antony's confirmation  
**Branch:** `member5-stage2b` (HEAD: `8baea0f` -> rebased on `48ca6a9`)

---

## 1. PART 3.0: Diagnostic Analysis of Current Implementation

### 1.1 Full Credential Path (Hop-by-Hop Trace)

```
[Frappe Desk Browser] 
       │ 1. Cookie: sid=<user_session>
       ▼
[Frappe Backend: ai_assistant/ai_assistant/api.py:get_auth_token()]
       │ 2. Mints JWT with claims: {sub, full_name, roles, iat, exp}
       ▼ (LOST: Frappe session cookie, user API key/secret, or ERPNext bearer token)
[FastAPI Gateway: agent/main.py:verify_token()]
       │ 3. Decodes JWT; verifies exp and sub
       ▼
[agent/main.py:_build_request_context()]
       │ 4. RequestContext(user_id=sub, roles=roles, credentials=None, read_only=False)
       ▼ (LOST: credentials set explicitly to None)
[agent/tools/adapter.py -> agent/tools/mcp_client.py:call_tool()]
       │ 5. Dispatches tool call via StdioServerParameters into subprocess queue
       ▼ (LOST: No caller identity or credentials piped across STDIO boundary)
[MCP Server: mcp_erpnext/server.py:handle_call_tool()]
       │ 6. ctx = current_request_context.get() or default_context or get_default_context()
       ▼ (Identity present in HTTP mode via BearerAuthMiddleware; missing in STDIO mode)
[ERPNext Client: mcp_erpnext/client.py:get_credentials()]
       │ 7. Evaluates ctx.credentials (is None) -> falls back to get_env_credentials()
       ▼
[HTTP REST Call: mcp_erpnext/client.py:get/post/put/delete]
       │ 8. Headers: Authorization: token <ERPNEXT_API_KEY>:<ERPNEXT_API_SECRET>
       ▼
[ERPNext Server] (Acts as Administrator / Service Key Owner; knows nothing of user_alice)
```

#### Detailed Breakdown per Hop:

1. **Hop 1: Frappe Authentication & JWT Generation (`ai_assistant/ai_assistant/api.py:7-47`)**
   ```python
   # ai_assistant/ai_assistant/api.py:13-32
   session_user = frappe.session.user if hasattr(frappe, "session") and frappe.session else None
   ...
   payload = {
       "sub": session_user,
       "full_name": full_name,
       "roles": roles,
       "iat": now,
       "exp": now + timedelta(hours=24)
   }
   token = jwt.encode(payload, jwt_secret, algorithm="HS256")
   ```
   - **What Exists:** The user is logged in to Frappe Desk with an active session (`frappe.session.user`, `frappe.session.data.sid`). Identity claims (`sub`, `roles`, `full_name`) are encoded into the JWT.
   - **What is Lost:** No ERPNext credentials (no API key/secret, no Frappe session cookie `sid`, no delegation token) are placed into the payload.

2. **Hop 2: FastAPI JWT Verification (`agent/main.py:170-174`)**
   ```python
   # agent/main.py:172-174
   payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
   logger.info(f"Successfully authenticated JWT for user: {payload.get('sub')}")
   return payload
   ```
   - **What Exists:** Token signature and expiry verified. Caller identity (`sub`, `roles`) is verified in FastAPI.

3. **Hop 3: Agent Request Context Construction (`agent/main.py:263-273`)**
   ```python
   # agent/main.py:263-273
   def _build_request_context(payload: dict) -> RequestContext:
       user_sub = payload.get("sub", "")
       roles = tuple(payload.get("roles", []))
       req_id = f"req-{uuid.uuid4().hex[:12]}"
       return RequestContext(
           user_id=user_sub,
           roles=roles,
           credentials=None,
           read_only=False,
           request_id=req_id,
       )
   ```
   - **What is Lost:** `credentials` is hardcoded to `None`.

4. **Hop 4: Agent to MCP Adapter Boundary (`agent/tools/mcp_client.py:212-214`)**
   ```python
   # agent/tools/mcp_client.py:212-214
   tool_name, args, req_fut = item
   try:
       res = await session.call_tool(tool_name, args)
   ```
   - **What is Lost:** The MCP client connects to an isolated, long-running STDIO subprocess launched at startup. Neither `user_id`, `roles`, nor any authorization token is passed into `session.call_tool`.

5. **Hop 5: MCP Server Transport (`mcp_erpnext/server.py:498-506`)**
   - In HTTP transport mode (`BearerAuthMiddleware`): Extracts `sub` and `roles` from the HTTP Bearer token and binds them to `current_request_context`. However, it does not receive or unpack an ERPNext user credential.
   - In STDIO transport mode: Defaults to static environment variables (`MCP_USER_ID="anonymous"`, `MCP_USER_ROLES=()`, `MCP_READ_ONLY=True`).

6. **Hop 6: Client Credential Resolution (`mcp_erpnext/client.py:81-87`)**
   ```python
   # mcp_erpnext/client.py:81-87
   def get_credentials(self) -> Credentials:
       if self._credentials is not None:
           return self._credentials
       ctx = get_request_context()
       if ctx is not None and ctx.credentials is not None:
           return ctx.credentials
       return get_env_credentials()
   ```
   - **What Happens:** `ctx.credentials` is `None`. The client silently falls back to `get_env_credentials()`.

7. **Hop 7: Outbound REST Call to ERPNext (`mcp_erpnext/client.py:33-36` & `agent/tools/erpnext_client.py:25-29`)**
   ```python
   # mcp_erpnext/client.py:33-36
   headers = {
       "Authorization": f"token {self.api_key}:{self.api_secret}",
   }
   ```
   - **What Happens:** Outbound HTTP request presents the shared service account keys from `.env`.

---

### 1.2 Locations Reading `.env` Service Credentials

Only two production modules read `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET`:
1. `mcp_erpnext/client.py:60-66` (in `get_env_credentials()`):
   ```python
   api_key = os.getenv("ERPNEXT_API_KEY", "").strip()
   api_secret = os.getenv("ERPNEXT_API_SECRET", "").strip()
   if not api_key or not api_secret:
       raise ERPNextConfigError(
           "ERPNext credentials not configured. Set ERPNEXT_API_KEY and ERPNEXT_API_SECRET or provide Credentials."
       )
   return Credentials(base_url=base_url, api_key=api_key, api_secret=api_secret)
   ```
2. `agent/tools/erpnext_client.py:16-26`:
   ```python
   ERPNEXT_API_KEY = os.getenv("ERPNEXT_API_KEY", "")
   ERPNEXT_API_SECRET = os.getenv("ERPNEXT_API_SECRET", "")
   def get_auth_headers() -> dict:
       return {
           "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
           "Content-Type": "application/json",
           "Accept": "application/json"
       }
   ```

---

### 1.3 What ERPNext Sees Today for a Request by “user_alice”

When `user_alice` submits a query (e.g., creating a Quotation or querying confidential Salary Slips or Customer balances):
1. **Acting User in ERPNext:** ERPNext identifies the request solely by the `Authorization: token <key>:<secret>` header. Since this belongs to the service account (typically `Administrator`), ERPNext sets `frappe.session.user = "Administrator"`.
2. **Permission Bypass:** Frappe's native Document Permissions (`has_permission`), User Permissions (restricting access by Company, Branch, or Customer), and Role Permission overrides are evaluated against `Administrator` (which has unrestricted system bypass). If `user_alice` has restricted read access, ERPNext does not block the query.
3. **Audit Trail Destruction:** Any document created or modified has its `owner`, `creation`, `modified_by`, and version history (`tabVersion`, `tabActivity Log`) stamped as `Administrator`. The identity of `user_alice` is completely erased from ERPNext's database audit log.

---

### 1.4 Does `ai_assistant/api.py` Currently Support Per-User Credentials?

**No.** Inspection of `ai_assistant/ai_assistant/api.py:1-55` confirms:
- The endpoint `get_auth_token()` only inspects `frappe.session.user`, queries `frappe.get_roles()`, and mints a JWT containing `{"sub", "full_name", "roles", "iat", "exp"}`.
- It contains **zero logic** to look up or generate API keys (`tabUser.api_key`), retrieve user secrets, or export Frappe session cookies (`sid`).

---

## 2. PART 3.1: Options Analysis

> [!NOTE]
> **Mapping Note:** "Frappe-Side Signed Assertion" maps to **Option C**, where Frappe signs user claims (`sub`, `roles`) into a short-lived JWT assertion while embedding Fernet-encrypted session credentials (`sid`, `csrf_token`) in the `ctx` claim.

| Criteria | Option A: Per-User API Keys | Option B: Frappe-Side Signed Assertion / Session Cookie (`sid`) Forwarding [SELECTED] | Option C: Per-User OAuth2 / Token Exchange [UNVERIFIED] | Option D: Service Account Impersonation [UNVERIFIED] | Option E: Service Key + Policy-Only Gating |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Mechanism** | `ai_assistant/api.py` fetches or generates user API key/secret (`frappe.generate_keys`) and returns them in the JWT or session store. | `ai_assistant/api.py` mints a signed JWT assertion with encrypted Frappe Desk session credentials (`sid`, `csrf_token`) in `ctx`; client sends `Cookie: sid=<sid>`. | Frappe acts as an OAuth2 Authorization Server; agent exchanges auth code or user grant for scoped access tokens. | Agent sends service credentials with custom header (e.g. `X-Frappe-Acting-User: user_alice`) to ERPNext. | Retain existing single service key in `.env`; rely entirely on `policy.py` to enforce role constraints. |
| **Frappe Native Support** | **Yes.** Frappe REST natively supports `Authorization: token <user_key>:<user_secret>`. | **Yes.** Frappe REST natively supports authentication via `Cookie: sid=...` across all `/api/resource/*` and `/api/method/*` endpoints. | **Yes [UNVERIFIED].** Frappe has built-in OAuth Provider app (`OAuth Client`, `OAuth Bearer Token`), but configuration for Desk widget is untested on this instance. | **NO.** Vanilla Frappe does **NOT** support impersonation headers on `/api/resource/*`. Requires custom Frappe middleware. | **Yes.** Already active today. |
| **ERPNext Permissions & Audit** | Full native Frappe permissions, User Permissions, and `modified_by` audit logging. | Full native Frappe permissions, User Permissions, and `modified_by` audit logging. | Full native Frappe permissions and audit logging [UNVERIFIED]. | Full native permissions, provided custom Frappe middleware calls `frappe.set_user()`. | **Zero ERPNext-side permissions.** Audit log records `Administrator` for all users. |
| **Secret Storage** | Stored in Frappe DB (`tabUser`). Secret hash must be retrieved or generated; secrets must be encrypted in transit. | Stored in Frappe DB (`tabSessions`). Ephemeral, managed by Frappe session manager. Fernet key in site config. | Tokens in `tabOAuth Bearer Token`. Refresh tokens stored in DB [UNVERIFIED]. | Service key in `.env`. No user secret storage. | Service key in `.env`. |
| **Expiration & Rotation** | User keys are static until manually regenerated in Frappe Desk. | Tied to active Frappe Desk browser session. Automatically expires when user logs out. | Token TTL configured on OAuth client (e.g. 1 hour) with refresh flow [UNVERIFIED]. | Service key rotation requires `.env` reload. | Service key rotation requires `.env` reload. |
| **Leakage in Repr/Logs** | Protected by `Credentials` and `RequestContext` (`repr=False`). | Protected by `Credentials._custom_headers` (`[CUSTOM_HEADERS]` in repr). | Protected by `Credentials` (`repr=False`). | Protected by `Credentials` (`repr=False`). | Protected by `Credentials` (`repr=False`). |
| **STDIO Path Impact** | Requires STDIO tool invocations to accept an ephemeral credential argument or per-request process. | Requires in-process ContextVar propagation or ephemeral credential arguments. | Requires token passing or per-turn exchange. | Same as Option A. | Works statically via `.env` without changes. |
| **Files Touched & Owner** | `ai_assistant/api.py` (M5/Frappe), `agent/main.py` (M5), `client.py` (M5), `mcp_client.py` (M4). | `ai_assistant/api.py` (M5/Frappe), `agent/main.py` (M5), `client.py` (M5), `mcp_erpnext/auth.py` (M5). | `ai_assistant` OAuth setup (M5/Frappe), `agent/main.py` (M5), `client.py` (M5). | `ai_assistant/hooks.py` (Frappe), `ai_assistant/auth.py` (Frappe), `client.py` (M5). | None. |
| **Security Risk** | High risk of user API secret exposure if stored or transmitted in plain JWT. | Low risk: session cookie is already held in the user's browser; limited to browser session lifetime; encrypted with Fernet in transit. | Low risk: industry standard OAuth2 scoped tokens [UNVERIFIED on this bench]. | Critical security risk: any breach of service account allows impersonating all users. | High security risk: privilege escalation if policy layer has a gap or bug. |
| **Implementation Effort** | High (Key generation, secret retrieval, schema changes). | **Low to Medium** (Leverages existing Frappe session infrastructure and Fernet encryption). | High (Requires full OAuth2 client setup, consent flows, token refreshes). | High (Requires custom Frappe monkeypatch/middleware). | Zero (status quo). |

---

## 3. PART 3.2: Fail-Closed Behavior Specification

To prevent silent privilege escalation, the service-credential fallback in `mcp_erpnext/client.py` must be strictly regulated:

```python
# Proposed logic for mcp_erpnext/client.py:get_credentials()
def get_credentials(self) -> Credentials:
    if self._credentials is not None:
        return self._credentials

    ctx = get_request_context()
    if ctx is not None:
        # User is authenticated
        if ctx.user_id and ctx.user_id != "anonymous":
            if ctx.credentials is not None:
                return ctx.credentials
            # Authenticated user without credentials: FAIL CLOSED
            raise ERPNextConfigError(
                f"Missing ERPNext user credentials for authenticated caller '{ctx.user_id}'. "
                "Silent service credential fallback is forbidden for authenticated users."
            )

    # Allow service key fallback ONLY for unauthenticated STDIO/CLI or explicit system admin tasks
    if os.environ.get("ALLOW_SERVICE_FALLBACK", "false").strip().lower() in ("true", "1") or is_cli_context():
        return get_env_credentials()

    raise ERPNextConfigError("No active request context or credentials available.")
```

### Fallback Matrix:
1. **HTTP Path with Verified User (`ctx.user_id = 'alice'`):** If `ctx.credentials` is `None` $\rightarrow$ **FAIL CLOSED (Raise `ERPNextConfigError`)**. Never fall back to `.env`.
2. **STDIO Standalone CLI / Development Tool:** If `ALLOW_SERVICE_FALLBACK=true` or unauthenticated CLI context $\rightarrow$ Allow `.env` fallback.
3. **HTTP Path with Unauthenticated Request:** Blocked at FastAPI / Starlette middleware with `401 Unauthorized`.

---

## 4. PART 3.3: Test Plan & Rollout Slicing

### Proposed Test Cases:
1. **Concurrent User Isolation:**
   - Run two concurrent requests (`user_alice` and `user_bob`) via `asyncio.gather`.
   - Assert `client.get_credentials()` inside request 1 yields Alice's credentials, and request 2 yields Bob's credentials.
2. **Redaction & Log Secrecy:**
   - Assert that `str(ctx)`, `repr(ctx)`, and logging output never expose API secrets, session cookies, or tokens.
3. **Fail-Closed on Missing Credentials:**
   - Execute request where `user_id = "user_alice"` and `credentials = None`.
   - Assert `client.get_credentials()` raises `ERPNextConfigError` rather than returning `.env` keys.
4. **STDIO Standalone Fallback:**
   - Verify CLI runs without user context continue to operate via `.env` when `ALLOW_SERVICE_FALLBACK=true`.

### Commit Slicing Plan:
- **Part G: Frappe Session / Token Minting (`ai_assistant/api.py` & `agent/main.py`)**
  - Update `get_auth_token` in Frappe to include encrypted/delegated session identifier (`sid`).
  - Update `agent/main.py:_build_request_context` to parse session token and instantiate `Credentials(auth_headers={"Cookie": f"sid={sid}"})`.
- **Part H: Fail-Closed Enforcement in `mcp_erpnext/client.py`**
  - Implement strict fail-closed check preventing authenticated users from falling back to service keys.
- **Part I: MCP Transport Credential Forwarding**
  - Forward credentials across the agent-to-MCP boundary (HTTP transport headers or STDIO argument injection).

---

## 5. PART 3.4: Recommendation & Team Decisions

### Primary Recommendation: **Option C (Frappe Session Cookie Forwarding)**
**Rationale:**
1. **Zero New Secret Management:** The user is already logged in to Frappe Desk when using the assistant. Frappe's session cookie (`sid`) already exists in `tabSessions`.
2. **Native Frappe Permission Engine:** Sending `Cookie: sid=<sid>` causes Frappe's REST API to natively execute as `frappe.session.user = "user_alice"`. Document permissions, role permissions, and user permissions apply automatically.
3. **Native Audit Logging:** Frappe automatically records `modified_by = "user_alice"` on all created or edited documents.
4. **Automatic Expiry:** When the user logs out of Frappe, the session expires in `tabSessions`, automatically revoking the assistant's access.

---

### Decisions Required from Team Lead & Members:

Please select from the following architectural decisions:

- **[Decision A] Credential Mechanism:**
  - `(A.1)` **Option C (Recommended):** Forward user Frappe session cookie (`sid`) in the JWT as `auth_headers={"Cookie": f"sid={sid}"}`.
  - `(A.2)` **Option A:** Generate and manage per-user API key/secret in Frappe `tabUser`.
  - `(A.3)` **Option D:** Retain shared `.env` service keys and rely exclusively on `policy.py` role filtering (accepting loss of Frappe-side permissions and audit trails).

- **[Decision B] Agent-to-MCP Transport:**
  - `(B.1)` **Switch to Streamable HTTP transport:** Agent calls MCP over HTTP with `Authorization: Bearer <user_jwt>`, allowing clean per-request identity and credential propagation.
  - `(B.2)` **Retain STDIO transport:** Pass credentials as an internal invocation context parameter or per-call argument to MCP tools.

- **[Decision C] Fail-Closed Enforcement:**
  - `(C.1)` **Strict:** Immediate raise of `ERPNextConfigError` if an authenticated caller lacks credentials.
  - `(C.2)` **Permissive Migration:** Log a warning and allow `.env` service fallback temporarily during transition.
