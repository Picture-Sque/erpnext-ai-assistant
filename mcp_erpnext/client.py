import os
import logging
import httpx
import urllib.parse
from typing import Optional, Dict, Any, Set
from mcp_erpnext.errors import ERPNextConfigError, ERPNextTransportError
from mcp_erpnext.auth import get_request_context, ReadOnlySessionError, mark_session_expired

logger = logging.getLogger("mcp_erpnext")

READ_ONLY_POST_ALLOWLIST: Set[str] = set()


def is_strict_identity(default: bool = False) -> bool:
    """
    Unified helper to determine if ERPNEXT_STRICT_IDENTITY is enabled.
    Accepts explicit default (False for library client, True for chat runtime).
    """
    val = os.getenv("ERPNEXT_STRICT_IDENTITY")
    if val is None or not str(val).strip():
        return default
    return str(val).strip().lower() in ("true", "1", "yes")


def _normalize_request_path(path: str) -> str:
    """
    Normalizes request path and verifies there are no traversal, double-slashes,
    or percent-encoded traversal characters.
    """
    lower_path = path.lower()
    if "//" in path or ".." in path or "%2f" in lower_path or "%2e" in lower_path:
        raise ReadOnlySessionError("Operation not permitted: invalid path format in read-only session.")
    parsed = urllib.parse.urlparse(path)
    clean_path = parsed.path.rstrip("/")
    if not clean_path and path.startswith("/"):
        clean_path = "/"
    return clean_path


class Credentials:
    """
    Carries ERPNext connection details and credentials.
    Per-request credentials can be explicitly provided.
    Repr is fully redacted to prevent credential leakage in logs or exceptions.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
        is_service: bool = False,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._custom_headers = auth_headers
        self.is_service = is_service

    def get_headers(self) -> Dict[str, str]:
        if self._custom_headers is not None:
            headers = dict(self._custom_headers)
        elif self.api_key and self.api_secret:
            headers = {
                "Authorization": f"token {self.api_key}:{self.api_secret}",
            }
        else:
            raise ERPNextConfigError("Missing ERPNext API key or secret.")

        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        return headers

    def __repr__(self) -> str:
        key_repr = "'[REDACTED]'" if self.api_key else "None"
        sec_repr = "'[REDACTED]'" if self.api_secret else "None"
        headers_repr = "[CUSTOM_HEADERS]" if self._custom_headers else "None"
        return (
            f"Credentials(base_url='{self.base_url}', api_key={key_repr}, "
            f"api_secret={sec_repr}, custom_headers={headers_repr})"
        )


def get_env_credentials() -> Credentials:
    """
    Lazily read ERPNext credentials from environment at call time.
    Fails visibly if keys are missing.
    """
    base_url = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8081").rstrip("/")
    api_key = os.getenv("ERPNEXT_API_KEY", "").strip()
    api_secret = os.getenv("ERPNEXT_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise ERPNextConfigError(
            "ERPNext credentials not configured. Set ERPNEXT_API_KEY and ERPNEXT_API_SECRET or provide Credentials."
        )

    return Credentials(base_url=base_url, api_key=api_key, api_secret=api_secret, is_service=True)


class ERPNextClient:
    """
    Transport-only client for ERPNext REST API.
    Directly invokes httpx.Client methods (get, post, put, delete) matching existing call signatures,
    with zero retries and typed transport error wrapping.
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self._credentials = credentials

    def get_credentials(self) -> Credentials:
        if self._credentials is not None:
            return self._credentials

        ctx = get_request_context()
        strict = is_strict_identity(default=False)

        if ctx is not None:
            # Check if context or its credentials are explicitly marked as service credentials
            is_svc = getattr(ctx, "is_service", False) or getattr(getattr(ctx, "credentials", None), "is_service", False)
            if is_svc:
                if strict:
                    raise ERPNextConfigError(
                        "Strict identity enforcement enabled: tool execution requires an authenticated "
                        "per-user session or agent token. Shared service credentials are prohibited."
                    )
                return ctx.credentials

            if ctx.credentials is not None:
                return ctx.credentials
            if getattr(ctx, "dev_mock", False):
                return get_env_credentials()
            raise ERPNextConfigError(
                f"No per-user credentials available in request context for user '{ctx.user_id}'."
            )

        if strict:
            raise ERPNextConfigError(
                "No request context is active and ERPNEXT_STRICT_IDENTITY is enabled."
            )
        return get_env_credentials()

    def _send(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        ctx = get_request_context()
        method_upper = method.upper()

        if ctx is not None and ctx.read_only is not False:
            if method_upper == "GET":
                pass
            elif method_upper == "POST":
                normalized = _normalize_request_path(path)
                if normalized not in READ_ONLY_POST_ALLOWLIST:
                    raise ReadOnlySessionError()
            else:
                raise ReadOnlySessionError()

        # Last line of defence, before any credentials are used or any request is made and with no way to switch it off: never touch ERPNext's
        # scripts, settings or logic, and never call a server method the agent is not built to call.
        from mcp_erpnext.protected import check_request
        check_request(method_upper, path)

        creds = self.get_credentials()
        url = f"{creds.base_url}/{path.lstrip('/')}"
        req_headers = headers if headers is not None else creds.get_headers()
        try:
            with httpx.Client() as client:
                if method_upper == "GET":
                    if params is not None:
                        resp = client.get(url, headers=req_headers, params=params, timeout=timeout)
                    else:
                        resp = client.get(url, headers=req_headers, timeout=timeout)
                elif method_upper == "POST":
                    resp = client.post(url, headers=req_headers, json=json, timeout=timeout)
                elif method_upper == "PUT":
                    resp = client.put(url, headers=req_headers, json=json, timeout=timeout)
                elif method_upper == "DELETE":
                    resp = client.delete(url, headers=req_headers, timeout=timeout)
                else:
                    resp = client.request(method_upper, url, headers=req_headers, params=params, json=json, timeout=timeout)

                if resp.status_code == 401:
                    mark_session_expired()
                elif resp.status_code == 403:
                    try:
                        body_json = resp.json()
                        if isinstance(body_json, dict) and body_json.get("session_expired") == 1:
                            mark_session_expired()
                    except Exception:
                        pass

                return resp
        except httpx.HTTPError as e:
            logger.warning(f"HTTP transport error during {method_upper} {url}: {e}")
            raise ERPNextTransportError(str(e)) from e

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        return self._send("GET", path, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        path: str,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        return self._send("POST", path, json=json, headers=headers, timeout=timeout)

    def put(
        self,
        path: str,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        return self._send("PUT", path, json=json, headers=headers, timeout=timeout)

    def delete(
        self,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        return self._send("DELETE", path, headers=headers, timeout=timeout)
