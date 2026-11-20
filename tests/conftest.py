"""
Shared test configuration.

The write registry (mcp_erpnext/write_registry.py) only lists Sales Order and Purchase
Order, because those are the doctypes the assistant is set up to write. Many older tests
exercise the generic crud mechanics (HTTP status mapping, envelopes, transport errors)
with Customer and Item records. Those tests are about transport behaviour, not about what
the assistant may write, so this fixture registers Customer and Item for them.

Tests that check the registry itself must see the real one. Mark them with
``@pytest.mark.real_write_registry`` (or set ``pytestmark`` at module level).
"""

import copy
import os
import tempfile

import pytest

# Tests must never touch the developer's real audit database: one test deletes and
# recreates the file at AUDIT_DB_PATH. Set before audit_logger is first imported.
# Both conftest files use the same path so one test session sees one database.
os.environ["SQLITE_AUDIT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "erpnext_assistant_test_audit.db")

_ALL_OPS = ("create", "update", "submit", "cancel", "delete")
_TEST_ROLES = ["System Manager", "Administrator"]

_LEGACY_TEST_DOCTYPES = {
    "Customer": {
        "duplicate_keys": ["customer_name"],
        "operations": {
            op: {
                "roles": list(_TEST_ROLES),
                "fields": ["customer_name", "phone", "email_id", "customer_group", "territory", "customer_type"],
                "child_fields": {},
            }
            for op in _ALL_OPS
        },
    },
    "Item": {
        "duplicate_keys": ["item_code"],
        "operations": {
            op: {
                "roles": list(_TEST_ROLES),
                "fields": ["item_code", "item_name", "item_group", "stock_uom"],
                "child_fields": {},
            }
            for op in _ALL_OPS
        },
    },
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_write_registry: run against the real write registry, without the legacy test doctypes",
    )


@pytest.fixture(autouse=True)
def _legacy_write_doctypes(request, monkeypatch):
    if "real_write_registry" in request.keywords:
        yield
        return
    import mcp_erpnext.write_registry as write_registry

    extended = copy.deepcopy(write_registry.WRITE_REGISTRY)
    for name, spec in _LEGACY_TEST_DOCTYPES.items():
        extended.setdefault(name, copy.deepcopy(spec))
    monkeypatch.setattr(write_registry, "WRITE_REGISTRY", extended)
    yield
