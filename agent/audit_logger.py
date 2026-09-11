import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger("audit_logger")

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AUDIT_DB_PATH = os.path.join(DB_DIR, "audit_log.db")
AUDIT_DB_PATH = os.getenv("SQLITE_AUDIT_DB_PATH", DEFAULT_AUDIT_DB_PATH)


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
    Initializes the SQLite audit database schema if missing.
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
                    details TEXT
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_doctype_target ON audit_log(doctype, target_name);")
            conn.commit()
        logger.info("SQLite audit database initialization complete.")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite audit database: {e}")


def log_audit_event(
    operation: str,
    doctype: str,
    target_name: str,
    outcome: str,
    user_id: str = "system",
    failure_classification: Optional[str] = None,
    details: str = ""
) -> None:
    """
    Logs an audit event to the audit database.
    This function is best-effort and will not raise exceptions if the logging fails,
    ensuring it never blocks the main pipeline execution.
    """
    try:
        now = datetime.utcnow().isoformat()
        with get_audit_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_log 
                (timestamp, user_id, operation, doctype, target_name, outcome, failure_classification, details) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now, user_id, operation, doctype, target_name, outcome, failure_classification, details)
            )
            conn.commit()
    except Exception as e:
        # Audit logging is best-effort observability, not a blocking dependency.
        logger.error(f"Failed to log audit event ({operation} {doctype} {target_name}): {e}")

