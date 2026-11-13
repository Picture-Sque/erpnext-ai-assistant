from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from mcp_erpnext.client import Credentials


@dataclass(frozen=True)
class RequestContext:
    """
    Carries request-scoped identity and execution constraints.
    Default read_only=True ensures fail-closed behavior for all operations.
    Credentials are never exposed in __repr__.
    """

    user_id: str
    roles: Tuple[str, ...] = field(default_factory=tuple)
    credentials: Optional[Any] = None
    read_only: bool = True
    request_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.roles, tuple):
            normalized_roles = tuple(str(r) for r in self.roles)
            object.__setattr__(self, "roles", normalized_roles)
        else:
            normalized_roles = tuple(str(r) for r in self.roles)
            object.__setattr__(self, "roles", normalized_roles)

    def __repr__(self) -> str:
        creds_repr = "'[REDACTED]'" if self.credentials is not None else "None"
        return (
            f"RequestContext(user_id={self.user_id!r}, roles={self.roles!r}, "
            f"credentials={creds_repr}, read_only={self.read_only!r}, "
            f"request_id={self.request_id!r})"
        )


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
