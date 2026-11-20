"""
Ask ERPNext whether the signed-in user may perform a write, before the user is asked to
confirm it.

The write registry says what the assistant is set up to do and which roles may do it. ERPNext
knows more: role permissions as customised on the site, User Permissions (for example "only
this company" or "only this territory"), and ownership rules. This check asks ERPNext itself,
as the user (the request carries the user's own session), so the answer reflects their real
access today, not the roles captured when their token was minted.

Both must allow. The registry can be stricter than ERPNext, never looser, and ERPNext still
enforces its own rules on the write itself. So this check only ever adds a refusal; it never
grants anything.

Outcomes:
  allowed  ERPNext answered with a literal true
  denied   ERPNext answered with a literal false
  unknown  anything else: no request context, the check is switched off, the call failed, or
           the answer was not a plain true or false. The write is then left to ERPNext's own
           refusal, which cannot be bypassed. An unknown answer is never treated as a denial.

Verified on Frappe 16.24: GET /api/method/frappe.client.has_permission with doctype, docname
and perm_type returns {"message": {"has_permission": <bool>}}; docname is required, and an
empty string asks at doctype level. As Administrator it answers true to everything, so the
false path (a restricted user) was not observed; it is covered by tests with a stand-in only.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from mcp_erpnext.auth import get_request_context
from mcp_erpnext.client import ERPNextClient

logger = logging.getLogger("mcp_erpnext.permissions")

# Registry operation -> ERPNext permission type.
PERMISSION_TYPE: Dict[str, str] = {
    "create": "create",
    "update": "write",
    "submit": "submit",
    "cancel": "cancel",
    "delete": "delete",
}

ALLOWED = "allowed"
DENIED = "denied"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionCheck:
    outcome: str
    detail: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome == ALLOWED

    @property
    def denied(self) -> bool:
        return self.outcome == DENIED


def preflight_enabled() -> bool:
    """ERPNEXT_PERMISSION_PREFLIGHT=false turns the check off without a code change."""
    return os.getenv("ERPNEXT_PERMISSION_PREFLIGHT", "true").strip().lower() not in ("false", "0", "no")


def check_erpnext_permission(
    operation: Any,
    doctype: Any,
    name: Optional[str] = None,
    client: Optional[ERPNextClient] = None,
) -> PermissionCheck:
    """
    Ask ERPNext whether the current user may `operation` the document `name` of `doctype`
    (or the doctype as a whole when `name` is empty, as for a create). Never raises.
    """
    perm_type = PERMISSION_TYPE.get(operation) if isinstance(operation, str) else None
    if perm_type is None:
        return PermissionCheck(UNKNOWN, f"unsupported operation {operation!r}")
    if not isinstance(doctype, str) or not doctype.strip():
        return PermissionCheck(UNKNOWN, "no doctype")
    if not preflight_enabled():
        return PermissionCheck(UNKNOWN, "permission preflight is switched off")
    # Without a request context there is no user session to ask about. The service account
    # would answer for itself, which says nothing about the person making the request.
    if client is None and get_request_context() is None:
        return PermissionCheck(UNKNOWN, "no request context")

    params = {
        "doctype": doctype.strip(),
        "docname": name.strip() if isinstance(name, str) else "",
        "perm_type": perm_type,
    }
    try:
        c = client or ERPNextClient()
        response = c.get("/api/method/frappe.client.has_permission", params=params, timeout=20.0)
    except Exception as exc:  # transport, configuration, anything: the write is still checked by ERPNext
        logger.warning("Permission preflight could not run for %s on %s: %s", operation, doctype, exc)
        return PermissionCheck(UNKNOWN, f"the check could not run: {exc}")

    if not getattr(response, "is_success", False):
        logger.warning(
            "Permission preflight got HTTP %s for %s on %s", getattr(response, "status_code", "?"), operation, doctype
        )
        return PermissionCheck(UNKNOWN, f"HTTP {getattr(response, 'status_code', '?')}")

    try:
        body = response.json()
    except Exception:
        return PermissionCheck(UNKNOWN, "the answer was not JSON")
    message = body.get("message") if isinstance(body, dict) else None
    answer = message.get("has_permission") if isinstance(message, dict) else None
    if answer is True:
        return PermissionCheck(ALLOWED)
    if answer is False:
        return PermissionCheck(DENIED, f"ERPNext does not allow {perm_type} on {doctype}")
    return PermissionCheck(UNKNOWN, "the answer was not a plain true or false")
