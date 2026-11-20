import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from mcp_erpnext.auth import get_request_context

logger = logging.getLogger("audit_logger")

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AUDIT_DB_PATH = os.path.join(DB_DIR, "audit_log.db")
AUDIT_DB_PATH = os.getenv("SQLITE_AUDIT_DB_PATH", DEFAULT_AUDIT_DB_PATH)

# user_id values that mean "the caller did not say who". They are replaced by the user of
# the active request, so denied, failed and executed events are never filed under "system".
_UNKNOWN_USERS = (None, "", "system", "unknown")


def get_audit_db_connection() -> sqlite3.Connection:
    """
    Creates and returns a SQLite connection for the audit log.
    """
    conn = sqlite3.connect(AUDIT_DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception as e:
        logger.warning(f"Error configuring SQLite PRAGMAs for audit DB: {e}")
    return conn


def init_audit_db() -> None:
    """
    Initializes the SQLite audit database schema if missing, and adds columns introduced
    after the table was first created.
    """
    logger.info(f"Initializing SQLite audit database at: {AUDIT_DB_PATH}")
    try:
        with get_audit_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT,
                    operation TEXT NOT NULL,
                    doctype TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    failure_classification TEXT,
                    details TEXT,
                    request_id TEXT
                );
            """)
            existing = {row["name"] for row in cursor.execute("PRAGMA table_info(audit_log)").fetchall()}
            if "request_id" not in existing:
                cursor.execute("ALTER TABLE audit_log ADD COLUMN request_id TEXT;")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_doctype_target ON audit_log(doctype, target_name);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);")
            conn.commit()
        logger.info("SQLite audit database initialization complete.")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite audit database: {e}")


def _resolve_identity(user_id: Optional[str], request_id: Optional[str]):
    """Fill a missing user or request id from the active request context."""
    ctx = get_request_context()
    if user_id in _UNKNOWN_USERS:
        ctx_user = getattr(ctx, "user_id", None) if ctx is not None else None
        user_id = ctx_user or user_id or "system"
    if not request_id and ctx is not None:
        request_id = getattr(ctx, "request_id", None) or getattr(ctx, "correlation_id", None)
    return user_id, request_id


def _insert(row: tuple) -> None:
    with get_audit_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log
            (timestamp, user_id, operation, doctype, target_name, outcome, failure_classification, details, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        conn.commit()


def log_audit_event(
    operation: str,
    doctype: str,
    target_name: str,
    outcome: str,
    user_id: Optional[str] = None,
    failure_classification: Optional[str] = None,
    details: str = "",
    request_id: Optional[str] = None,
) -> None:
    """
    Logs an audit event to the audit database.

    When user_id or request_id is not given, they are taken from the active request
    context, so every event is attributed to the user who made the request.

    This function is best-effort and will not raise exceptions if the logging fails,
    ensuring it never blocks the main pipeline execution.
    """
    try:
        now = datetime.utcnow().isoformat()
        user_id, request_id = _resolve_identity(user_id, request_id)
        row = (now, user_id, operation, doctype, target_name, outcome, failure_classification, details, request_id)
        try:
            _insert(row)
        except sqlite3.OperationalError as e:
            # A fresh deployment or a replaced database file has no table yet. Create it
            # once and retry rather than losing the event.
            if "no such table" not in str(e).lower() and "no column named" not in str(e).lower():
                raise
            init_audit_db()
            _insert(row)
    except Exception as e:
        # Audit logging is best-effort observability, not a blocking dependency.
        logger.error(f"Failed to log audit event ({operation} {doctype} {target_name}): {e}")
