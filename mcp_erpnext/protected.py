"""
Hard block on ERPNext's own logic and configuration.

The agent must never read, create, change or delete ERPNext scripts and the other records that define how
ERPNext behaves (Server Scripts, Client Scripts, custom fields, workflows, permissions and so on), and it
must never call a server method it was not built to call. This is enforced where every request leaves the
process (`ERPNextClient._send`), so it holds whatever the doctype whitelist, the write registry, a skill or
an environment variable says, and whatever a model asks for. There is deliberately NO switch to turn it off.

Two rules:
  1. Record access (`/api/resource/<doctype>`): refused for every doctype in PROTECTED_DOCTYPES.
  2. Method calls (`/api/method/<name>`): refused unless the method is one the agent is built to call: the
     fixed read helpers below, and the conversion methods named in the write registry. Any other path
     (`/api/v2`, `/app`, `/files`, ...) is refused.

`validate_configuration()` runs at startup and refuses to start the service if the registry or the doctype
whitelist names a protected doctype, so a bad setting is loud instead of silently ignored.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import FrozenSet, Iterable, List, Optional

from mcp_erpnext.errors import ERPNextError

logger = logging.getLogger("mcp_erpnext.protected")


class ProtectedResourceError(ERPNextError):
    """The request would touch ERPNext's scripts, configuration or an unapproved method. It was not sent."""


# Records that define or change how ERPNext behaves. Compared case-insensitively, whitespace-trimmed.
PROTECTED_DOCTYPES: FrozenSet[str] = frozenset(
    name.lower()
    for name in (
        # scripts and code
        "Server Script", "Client Script", "Custom Script", "Custom HTML Block", "Website Script", "Scheduled Job Type",
        "Webhook", "Notification", "Assignment Rule", "Auto Email Report", "Report", "Print Format", "Print Style", "Page",
        # schema and customisation
        "DocType", "DocField", "DocType Action", "DocType Link", "DocType State", "Custom Field", "Property Setter",
        "Customize Form", "Module Def", "Patch Log",
        # workflow
        "Workflow", "Workflow State", "Workflow Action", "Workflow Action Master", "Workflow Transition", "Workflow Document State",
        # who may do what
        "DocPerm", "Custom DocPerm", "Role", "Has Role", "Role Permission for Page and Report", "User Permission",
        # site wide settings
        "System Settings", "Website Settings",
    )
)

# Server methods the agent may call. Anything else under /api/method is refused. The conversion methods are
# added from the write registry (see allowed_methods()); nothing else can extend this list at runtime.
FIXED_ALLOWED_METHODS: FrozenSet[str] = frozenset(
    {
        "frappe.client.get_count",
        "frappe.client.has_permission",
        "frappe.desk.query_report.run",
    }
)

_RESOURCE_PREFIX = "/api/resource/"
_METHOD_PREFIX = "/api/method/"


def is_protected_doctype(name: object) -> bool:
    return isinstance(name, str) and re.sub(r"\s+", " ", name).strip().lower() in PROTECTED_DOCTYPES


def allowed_methods() -> FrozenSet[str]:
    """The fixed read helpers plus the conversion methods the write registry names."""
    from mcp_erpnext import write_registry as wr  # late import: the registry imports the client

    methods = set(FIXED_ALLOWED_METHODS)
    for doctype in wr.WRITE_REGISTRY:
        for source in wr.get_convert_sources(doctype):
            spec = wr.get_convert_spec(doctype, source)
            if spec and isinstance(spec.get("method"), str):
                methods.add(spec["method"].strip())
    return frozenset(methods)


def _decoded_path(path: str) -> str:
    """The path as the server would read it: query and fragment dropped, percent-escapes fully decoded."""
    text = urllib.parse.urlparse(path or "").path
    if text and not text.startswith("/"):
        text = "/" + text  # the client sends "api/..." and "/api/..." to the same place
    for _ in range(5):  # a doubly encoded name must not slip through
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    return text.replace("\\", "/")


def check_request(method: str, path: str) -> None:
    """Raise ProtectedResourceError unless this request may be sent. Returns None when it may."""
    text = _decoded_path(path)
    if "\x00" in text or ".." in text.split("/") or "//" in text.replace("://", ""):
        _refuse(method, path, "malformed path")
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]  # one trailing slash names the same resource
    lowered = text.lower()
    if lowered.startswith(_RESOURCE_PREFIX):
        rest = text[len(_RESOURCE_PREFIX):]
        doctype = rest.split("/", 1)[0]
        if not doctype.strip():
            _refuse(method, path, "no doctype named")
        if is_protected_doctype(doctype):
            _refuse(method, path, f"{doctype!r} is a protected doctype")
        return
    if lowered.startswith(_METHOD_PREFIX):
        name = text[len(_METHOD_PREFIX):].strip()
        if name not in allowed_methods():
            _refuse(method, path, f"method {name!r} is not one the agent may call")
        return
    _refuse(method, path, "only /api/resource and approved /api/method paths may be used")


def _refuse(method: str, path: str, why: str) -> None:
    logger.error("BLOCKED %s %s: %s", str(method).upper(), path, why)
    raise ProtectedResourceError(
        "This request was blocked: the assistant is not allowed to read or change ERPNext scripts, settings or logic."
    )


def validate_configuration(extra_doctypes: Optional[Iterable[str]] = None) -> List[str]:
    """
    Check that nothing configured to be reachable is protected: the write registry (documents, convert
    sources and the child doctypes they read) and the doctype whitelist, including an environment override.
    Raises ProtectedResourceError listing every offender; returns [] when clean.
    """
    from mcp_erpnext import crud, write_registry as wr  # late import

    named: List[str] = []
    for doctype in wr.WRITE_REGISTRY:
        named.append(doctype)
        for source in wr.get_convert_sources(doctype):
            named.append(source)
            link = (wr.get_convert_spec(doctype, source) or {}).get("linked_by") or {}
            named.extend(v for v in (link.get("child_doctype"), link.get("parent_doctype")) if v)
    named.extend(crud.get_allowed_doctypes())
    named.extend(extra_doctypes or [])
    offenders = sorted({n for n in named if is_protected_doctype(n)})
    for method in sorted(allowed_methods() - FIXED_ALLOWED_METHODS):
        if not method.startswith(("erpnext.", "frappe.client.")) or "script" in method.lower():
            offenders.append(f"method {method}")
    if offenders:
        raise ProtectedResourceError(
            "Refusing to start: the configuration makes protected ERPNext resources reachable: " + ", ".join(offenders)
        )
    return []
