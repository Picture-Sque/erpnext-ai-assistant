from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field, FrozenInstanceError
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence, Tuple

from mcp_erpnext.errors import ERPNextError

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

