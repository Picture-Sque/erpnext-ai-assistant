from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field, FrozenInstanceError
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from mcp_erpnext.client import Credentials


@dataclass
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

        req_id = request_id if request_id is not None else correlation_id
        corr_id = correlation_id if correlation_id is not None else request_id
        object.__setattr__(self, "correlation_id", corr_id)
        object.__setattr__(self, "request_id", req_id)

        if type(read_only) is not bool:
            object.__setattr__(self, "read_only", True)
        else:
            object.__setattr__(self, "read_only", read_only)

        object.__setattr__(self, "_initialized", True)

    @property
    def credential(self) -> Optional[Any]:
        """Backward-compatibility read/write alias for credentials."""
        return self.credentials

    @credential.setter
    def credential(self, value: Any) -> None:
        object.__setattr__(self, "credentials", value)

    def __setattr__(self, name: str, value: Any) -> None:
        if not getattr(self, "_initialized", False):
            super().__setattr__(name, value)
            return
        if name in ("credential", "credentials"):
            object.__setattr__(self, "credentials", value)
            return
        raise FrozenInstanceError(f"cannot assign to field {name!r}")


current_request_context: contextvars.ContextVar[Optional[RequestContext]] = (
    contextvars.ContextVar("current_request_context", default=None)
)


def get_request_context() -> Optional[RequestContext]:
    """Retrieve the active RequestContext, or None if outside a request scope."""
    return current_request_context.get()


@contextmanager
def set_request_context(ctx: Optional[RequestContext]):
    """
    Context manager to bind a RequestContext for the current execution context.
    Guarantees token reset in a finally block even on unhandled exceptions.
    """
    token = current_request_context.set(ctx)
    try:
        yield ctx
    finally:
        current_request_context.reset(token)
