"""
Audit events are attributed to the user of the request, carry the request id, and the
audit database creates itself (and migrates) instead of silently losing events.
"""

import sqlite3

import pytest

import audit_logger
from mcp_erpnext.auth import RequestContext, set_request_context
from workflow import graph


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    path = str(tmp_path / "audit.db")
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", path)
    audit_logger.init_audit_db()
    return path


def rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()]
    finally:
        conn.close()


def ctx(user="sandra@example.com", request_id="req-abc123"):
    return RequestContext(user_id=user, roles=("Sales User",), read_only=False, request_id=request_id)


def test_missing_user_is_taken_from_the_request(audit_db):
    with set_request_context(ctx()):
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "executed")
    (row,) = rows(audit_db)
    assert row["user_id"] == "sandra@example.com"
    assert row["request_id"] == "req-abc123"


def test_unknown_placeholder_is_replaced_by_the_request_user(audit_db):
    with set_request_context(ctx()):
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "pending", user_id="unknown")
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "pending", user_id="system")
    assert [r["user_id"] for r in rows(audit_db)] == ["sandra@example.com"] * 2


def test_explicit_user_wins_over_the_request(audit_db):
    with set_request_context(ctx()):
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "confirmed", user_id="other@example.com")
    assert rows(audit_db)[0]["user_id"] == "other@example.com"


def test_explicit_request_id_wins(audit_db):
    with set_request_context(ctx()):
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "executed", request_id="req-explicit")
    assert rows(audit_db)[0]["request_id"] == "req-explicit"


def test_without_a_request_the_user_is_system(audit_db):
    audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "executed")
    row = rows(audit_db)[0]
    assert row["user_id"] == "system"
    assert row["request_id"] is None


def test_old_table_without_request_id_is_migrated_and_keeps_its_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT, operation TEXT NOT NULL, doctype TEXT NOT NULL, target_name TEXT NOT NULL,
            outcome TEXT NOT NULL, failure_classification TEXT, details TEXT)"""
    )
    conn.execute(
        "INSERT INTO audit_log (user_id, operation, doctype, target_name, outcome) VALUES ('a@x', 'create', 'Sales Order', 'SO-0', 'executed')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", path)
    audit_logger.init_audit_db()
    with set_request_context(ctx()):
        audit_logger.log_audit_event("submit", "Sales Order", "SO-1", "executed")
    result = rows(path)
    assert [r["target_name"] for r in result] == ["SO-0", "SO-1"]
    assert result[0]["request_id"] is None
    assert result[1]["request_id"] == "req-abc123"


def test_event_is_not_lost_when_the_table_does_not_exist_yet(tmp_path, monkeypatch):
    path = str(tmp_path / "fresh.db")
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", path)
    audit_logger.log_audit_event("cancel", "Purchase Order", "PO-1", "denied", user_id="u@example.com")
    (row,) = rows(path)
    assert row["target_name"] == "PO-1"
    assert row["user_id"] == "u@example.com"


def test_logging_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_logger, "AUDIT_DB_PATH", str(tmp_path / "no_such_dir" / "audit.db"))
    audit_logger.log_audit_event("cancel", "Purchase Order", "PO-1", "denied")


def test_denied_write_at_the_gate_is_filed_under_the_real_user(audit_db):
    state = {
        "detected_intent": "submit-sales-order",
        "user_roles": ["Sales User"],
        "collected_fields": {"name": "SO-1"},
    }
    with set_request_context(ctx(user="sandra@example.com", request_id="req-gate")):
        out = graph.write_rbac_gate_node(state)
    assert out["write_rbac_passed"] is False
    denied = [r for r in rows(audit_db) if r["outcome"] == "denied"]
    assert len(denied) == 1
    assert denied[0]["user_id"] == "sandra@example.com"
    assert denied[0]["request_id"] == "req-gate"
    assert denied[0]["operation"] == "submit"
