from __future__ import annotations

import contextvars
import json
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, FrozenInstanceError
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Sequence, Tuple

import jwt

from mcp_erpnext.errors import ERPNextConfigError, ERPNextError

logger = logging.getLogger("mcp_erpnext.auth")

if TYPE_CHECKING:
    from mcp_erpnext.client import Credentials


@dataclass(frozen=True)
class RequestContext:
    """
    Canonical request context carrying caller identity and execution constraints.
    Superset supporting both agent and MCP server call patterns.
    Credentials are never exposed in string representations or tracebacks.
    """

    user_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    is_admin: bool = False
    is_guest: bool = False
    credentials: Optional[Any] = field(default=None, repr=False)
    correlation_id: Optional[str] = None
    read_only: bool = True
    request_id: Optional[str] = None
    dev_mock: bool = False
    is_service: bool = False
    _session_expired_flag: list = field(default_factory=lambda: [False], repr=False, init=False)

    def __init__(
        self,
        user_id: Optional[str] = None,
        roles: Any = (),
        is_admin: bool = False,
        is_guest: bool = False,
        credentials: Optional[Any] = None,
        correlation_id: Optional[str] = None,
        read_only: Any = True,
        request_id: Optional[str] = None,
        credential: Optional[Any] = None,
        dev_mock: bool = False,
        is_service: bool = False,
        _session_expired_flag: Optional[Any] = None,
    ) -> None:
        if credential is not None and credentials is None:
            credentials = credential
        object.__setattr__(self, "user_id", user_id)

        raw_roles = roles
        if isinstance(raw_roles, (list, tuple, set, frozenset)):
            if all(isinstance(r, str) and len(r) > 0 for r in raw_roles):
                object.__setattr__(self, "roles", tuple(raw_roles))
            else:
                object.__setattr__(self, "roles", ())
        else:
            object.__setattr__(self, "roles", ())

        object.__setattr__(self, "is_admin", bool(is_admin))
        object.__setattr__(self, "is_guest", bool(is_guest))
        object.__setattr__(self, "credentials", credentials)
        object.__setattr__(self, "dev_mock", bool(dev_mock))
        object.__setattr__(self, "is_service", bool(is_service))
        flag = _session_expired_flag if _session_expired_flag is not None else [False]
        object.__setattr__(self, "_session_expired_flag", flag)

        req_id = request_id if request_id is not None else correlation_id
        corr_id = correlation_id if correlation_id is not None else request_id
        object.__setattr__(self, "correlation_id", corr_id)
        object.__setattr__(self, "request_id", req_id)

        if type(read_only) is not bool:
            object.__setattr__(self, "read_only", True)
        else:
            object.__setattr__(self, "read_only", read_only)

    @property
    def credential(self) -> Optional[Any]:
        """Backward-compatibility read-only alias for credentials."""
        return self.credentials


current_request_context: contextvars.ContextVar[Optional[RequestContext]] = (
    contextvars.ContextVar("current_request_context", default=None)
)

_session_expired: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "session_expired", default=False
)


def mark_session_expired() -> None:
    """Flag the current request context as having encountered an expired ERPNext session."""
    _session_expired.set(True)
    ctx = get_request_context()
    if ctx is not None and hasattr(ctx, "_session_expired_flag"):
        ctx._session_expired_flag[0] = True


def is_session_expired() -> bool:
    """Check whether the active request has flagged an expired ERPNext session."""
    ctx = get_request_context()
    if ctx is not None and hasattr(ctx, "_session_expired_flag"):
        return bool(ctx._session_expired_flag[0])
    return _session_expired.get()


def reset_session_expired() -> None:
    """Reset the session expired flag for the current context."""
    _session_expired.set(False)
    ctx = get_request_context()
    if ctx is not None and hasattr(ctx, "_session_expired_flag"):
        ctx._session_expired_flag[0] = False


def get_request_context() -> Optional[RequestContext]:
    """Retrieve the active RequestContext, or None if outside a request scope."""
    return current_request_context.get()


get_current_request_context = get_request_context


@contextmanager
def set_request_context(ctx: Optional[RequestContext]):
    """
    Context manager to bind a RequestContext for the current execution context.
    Guarantees token reset in a finally block even on unhandled exceptions.
    """
    token = current_request_context.set(ctx)
    expired_token = _session_expired.set(False)
    try:
        yield ctx
    finally:
        current_request_context.reset(token)
        _session_expired.reset(expired_token)


class ReadOnlySessionError(ERPNextError):
    """Raised when a write or mutating operation is attempted in a read-only session."""

    def __init__(self, message: str = "Operation not permitted: this session is read-only."):
        super().__init__(message)


def require_writable(context: Optional[RequestContext] = None) -> None:
    """
    Ensure the current or provided RequestContext is permitted to perform mutating operations.
    Fails closed: raises ReadOnlySessionError if context exists and read_only is anything other than exactly False.
    """
    ctx = context if context is not None else get_request_context()
    if ctx is not None and ctx.read_only is not False:
        raise ReadOnlySessionError()


class AuthTokenError(ERPNextError):
    """Raised when token authentication fails (malformed, invalid, missing claims)."""
    pass


class TokenExpiredError(AuthTokenError):
    """Raised when token signature is expired."""
    pass


def verify_jwt(
    token: str,
    secrets: Sequence[str] | str,
    algorithms: Sequence[str] = ("HS256",),
    require_exp: bool = False,
    require_sub: bool = True,
) -> dict:
    """
    Unified JWT verification path used across agent and MCP server.
    Decodes token against candidate secrets. Fails closed with standard exceptions.
    """
    if isinstance(secrets, str):
        candidate_secrets = [secrets.strip()] if secrets.strip() else []
    else:
        candidate_secrets = [s.strip() for s in secrets if s and isinstance(s, str) and s.strip()]

    if not candidate_secrets:
        raise ERPNextConfigError("Server security error: JWT secret key is unconfigured.")

    require_claims = []
    if require_exp:
        require_claims.append("exp")
    if require_sub:
        require_claims.append("sub")

    last_err: Optional[Exception] = None
    for secret in candidate_secrets:
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=list(algorithms),
                options={
                    "require": require_claims,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                },
            )
            if require_sub:
                sub_val = payload.get("sub")
                if not isinstance(sub_val, str) or not sub_val.strip():
                    raise AuthTokenError("Token 'sub' claim must be a non-empty string.")
            return payload
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError("Token has expired. Please refresh your session.")
        except jwt.MissingRequiredClaimError as err:
            last_err = err
        except jwt.InvalidTokenError as err:
            last_err = err
        except AuthTokenError:
            raise
        except Exception as err:
            last_err = err

    raise AuthTokenError(f"Invalid token: {str(last_err)}")


def decrypt_session_credentials(
    ctx_claim: str,
    enc_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Any:
    """
    Decrypt Fernet-encrypted session credentials ('ctx' claim).
    Fails closed if encryption key or base URL is missing, or payload is corrupt/tampered.
    """
    key = enc_key if enc_key is not None else os.getenv("CREDENTIAL_ENC_KEY")
    if not key or not str(key).strip():
        raise ERPNextConfigError("Server security error: CREDENTIAL_ENC_KEY is unconfigured.")

    try:
        from cryptography.fernet import Fernet
        try:
            fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception:
            raise ERPNextConfigError("Server configuration error: Invalid CREDENTIAL_ENC_KEY.")

        decrypted_bytes = fernet.decrypt(ctx_claim.encode("utf-8") if isinstance(ctx_claim, str) else ctx_claim)
        data = json.loads(decrypted_bytes.decode("utf-8"))
        sid = data.get("sid")
        csrf_token = data.get("csrf_token")
        if not sid or not isinstance(sid, str) or not sid.strip():
            raise ValueError("Missing 'sid' in session credentials")
    except ERPNextConfigError:
        raise
    except Exception:
        raise AuthTokenError("Invalid, tampered, or undecryptable session credentials.")

    resolved_base_url = (base_url or os.getenv("ERPNEXT_BASE_URL") or os.getenv("ERPNEXT_URL") or "").strip()
    if not resolved_base_url:
        raise ERPNextConfigError("Server configuration error: ERPNEXT_BASE_URL is unconfigured.")
    resolved_base_url = resolved_base_url.rstrip("/")

    auth_headers = {"Cookie": f"sid={sid.strip()}"}
    if csrf_token:
        auth_headers["X-Frappe-CSRF-Token"] = str(csrf_token).strip()

    from mcp_erpnext.client import Credentials
    return Credentials(base_url=resolved_base_url, auth_headers=auth_headers)


def build_request_context_from_payload(
    payload: dict,
    enc_key: Optional[str] = None,
    base_url: Optional[str] = None,
    force_read_only: bool = False,
    require_ctx: bool = True,
    request_id: Optional[str] = None,
) -> RequestContext:
    """
    Construct canonical RequestContext from verified JWT payload.
    Supports force_read_only for external HTTP MCP clients.
    """
    user_sub = payload.get("sub", "")
    roles = tuple(payload.get("roles", []))
    req_id = request_id or payload.get("request_id") or f"req-{uuid.uuid4().hex[:12]}"

    is_dev_mock = bool(payload.get("is_dev_mock", False))
    if is_dev_mock:
        return RequestContext(
            user_id=user_sub,
            roles=roles,
            credentials=None,
            read_only=True if force_read_only else False,
            request_id=req_id,
            dev_mock=True,
        )

    ctx_claim = payload.get("ctx")
    if not ctx_claim:
        if require_ctx:
            key = enc_key if enc_key is not None else os.getenv("CREDENTIAL_ENC_KEY")
            if not key or not str(key).strip():
                raise ERPNextConfigError("Server security error: CREDENTIAL_ENC_KEY is unconfigured.")
            raise AuthTokenError("Missing encrypted session credentials in token ('ctx' claim required).")
        user_credentials = None
    else:
        user_credentials = decrypt_session_credentials(ctx_claim, enc_key=enc_key, base_url=base_url)

    if force_read_only:
        read_only = True
    elif "scope" not in payload:
        read_only = False
    elif payload.get("scope") == "read":
        read_only = True
    else:
        read_only = True

    return RequestContext(
        user_id=user_sub,
        roles=roles,
        credentials=user_credentials,
        read_only=read_only,
        request_id=req_id,
        dev_mock=False,
    )


