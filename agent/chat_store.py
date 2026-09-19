import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

logger = logging.getLogger("chat_store")

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(DB_DIR, "chat_history.db")
DB_PATH = os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)


def get_db_connection() -> sqlite3.Connection:
    """
    Creates and returns a SQLite connection with busy timeout and Row factory.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode and foreign keys for performance and safety
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception as e:
        logger.warning(f"Error configuring SQLite PRAGMAs: {e}")
    return conn


def init_db() -> None:
    """
    Initializes the SQLite database schema if missing.
    """
    logger.info(f"Initializing SQLite database at: {DB_PATH}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. conversations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # 2. messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT DEFAULT '',
                skill TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)
        
        # 3. conversation_state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                conversation_id INTEGER PRIMARY KEY,
                detected_intent TEXT DEFAULT '',
                current_skill TEXT DEFAULT '',
                collected_fields TEXT DEFAULT '{}',
                workflow_complete INTEGER DEFAULT 0,
                extra_state TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)

        # Migration: ensure extra_state column exists in existing databases
        cursor.execute("PRAGMA table_info(conversation_state);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "extra_state" not in columns:
            cursor.execute("ALTER TABLE conversation_state ADD COLUMN extra_state TEXT DEFAULT '{}';")
        
        # Create index on session_id for fast lookup
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);")
        conn.commit()
    logger.info("SQLite database initialization complete.")


def get_or_create_conversation(session_id: str, user_id: str = "") -> int:
    """
    Retrieves the conversation_id for a given session_id, creating one if it doesn't exist.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM conversations WHERE session_id = ?;", (session_id,))
        row = cursor.fetchone()
        if row:
            return row["id"]
        
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO conversations (session_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?);",
            (session_id, user_id, now, now)
        )
        conversation_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT OR IGNORE INTO conversation_state (conversation_id, detected_intent, current_skill, collected_fields, workflow_complete, extra_state, updated_at) VALUES (?, '', '', '{}', 0, '{}', ?);",
            (conversation_id, now)
        )
        conn.commit()
        return conversation_id


def load_session_state(session_id: str, user_roles: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Loads conversation messages and slot state from SQLite for a session_id.
    Reconstructs LangChain HumanMessage and AIMessage objects.
    """
    user_roles = user_roles or []
    try:
        conversation_id = get_or_create_conversation(session_id)
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Load messages
            cursor.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC;",
                (conversation_id,)
            )
            msg_rows = cursor.fetchall()
            
            messages: List[BaseMessage] = []
            for r in msg_rows:
                role = r["role"].lower()
                content = r["content"]
                if role in ("user", "human"):
                    messages.append(HumanMessage(content=content))
                elif role in ("assistant", "ai"):
                    messages.append(AIMessage(content=content))
            
            # Load state
            cursor.execute(
                "SELECT detected_intent, current_skill, collected_fields, workflow_complete, extra_state FROM conversation_state WHERE conversation_id = ?;",
                (conversation_id,)
            )
            state_row = cursor.fetchone()
            
            detected_intent = ""
            collected_fields = {}
            workflow_complete = False
            extra_state = {}
            
            if state_row:
                detected_intent = state_row["detected_intent"] or ""
                workflow_complete = bool(state_row["workflow_complete"])
                fields_raw = state_row["collected_fields"]
                if fields_raw:
                    try:
                        collected_fields = json.loads(fields_raw)
                    except Exception as e:
                        logger.error(f"Error parsing collected_fields JSON for session {session_id}: {e}")
                        collected_fields = {}
                
                extra_raw = state_row["extra_state"] if "extra_state" in state_row.keys() else None
                if extra_raw:
                    try:
                        extra_state = json.loads(extra_raw)
                    except Exception as e:
                        logger.error(f"Error parsing extra_state JSON for session {session_id}: {e}")
                        extra_state = {}
            
            return {
                "messages": messages,
                "detected_intent": detected_intent,
                "collected_fields": collected_fields,
                "final_response": "",
                "user_roles": user_roles,
                "is_workflow_complete": workflow_complete,
                "pending_confirmation": extra_state.get("pending_confirmation"),
                "ambiguous_candidates": extra_state.get("ambiguous_candidates"),
                "clarification_target": extra_state.get("clarification_target"),
                "clarification_attempts": extra_state.get("clarification_attempts") or 0,
                "resolved_entities": extra_state.get("resolved_entities") or {},
                "bulk_operation_scope": extra_state.get("bulk_operation_scope"),
                # Follow-up router state
                "last_turn_context": extra_state.get("last_turn_context"),
                "pending_slot_clarification": extra_state.get("pending_slot_clarification"),
            }
    except Exception as e:
        logger.exception(f"Failed to load session state for {session_id}: {e}")
        return {
            "messages": [],
            "detected_intent": "",
            "collected_fields": {},
            "final_response": "",
            "user_roles": user_roles,
            "is_workflow_complete": False,
            "pending_confirmation": None,
            "ambiguous_candidates": None,
            "clarification_target": None,
            "clarification_attempts": 0,
            "resolved_entities": {},
            "bulk_operation_scope": None,
            # Follow-up router state
            "last_turn_context": None,
            "pending_slot_clarification": None,
        }


def save_message(session_id: str, role: str, content: str, intent: str = "", skill: str = "") -> None:
    """
    Appends a message to the SQLite messages table for the session.
    """
    try:
        conversation_id = get_or_create_conversation(session_id)
        now = datetime.utcnow().isoformat()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO messages (conversation_id, role, content, intent, skill, created_at) VALUES (?, ?, ?, ?, ?, ?);",
                (conversation_id, role, content, intent, skill, now)
            )
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?;",
                (now, conversation_id)
            )
            conn.commit()
    except Exception as e:
        logger.exception(f"Failed to save message for session {session_id}: {e}")


def update_session_state(
    session_id: str,
    detected_intent: str = "",
    collected_fields: Optional[Dict[str, Any]] = None,
    current_skill: str = "",
    is_complete: bool = False,
    extra_state: Optional[Dict[str, Any]] = None
) -> None:
    """
    Updates slot filling and workflow state in SQLite for the given session.
    """
    try:
        conversation_id = get_or_create_conversation(session_id)
        collected_fields = collected_fields or {}
        fields_json = json.dumps(collected_fields)
        extra_json = json.dumps(extra_state or {})
        complete_int = 1 if is_complete else 0
        now = datetime.utcnow().isoformat()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO conversation_state (conversation_id, detected_intent, current_skill, collected_fields, workflow_complete, extra_state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    detected_intent = excluded.detected_intent,
                    current_skill = excluded.current_skill,
                    collected_fields = excluded.collected_fields,
                    workflow_complete = excluded.workflow_complete,
                    extra_state = excluded.extra_state,
                    updated_at = excluded.updated_at;
                """,
                (conversation_id, detected_intent, current_skill, fields_json, complete_int, extra_json, now)
            )
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?;",
                (now, conversation_id)
            )
            conn.commit()
    except Exception as e:
        logger.exception(f"Failed to update session state for {session_id}: {e}")


def reset_session_state(session_id: str) -> None:
    """
    Resets the conversation state and messages for a given session in SQLite.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM conversations WHERE session_id = ?;", (session_id,))
            row = cursor.fetchone()
            if row:
                conversation_id = row["id"]
                cursor.execute("DELETE FROM messages WHERE conversation_id = ?;", (conversation_id,))
                cursor.execute(
                    "UPDATE conversation_state SET detected_intent = '', current_skill = '', collected_fields = '{}', workflow_complete = 0, extra_state = '{}', updated_at = ? WHERE conversation_id = ?;",
                    (datetime.utcnow().isoformat(), conversation_id)
                )
                cursor.execute("UPDATE conversations SET updated_at = ? WHERE id = ?;", (datetime.utcnow().isoformat(), conversation_id))
                conn.commit()
    except Exception as e:
        logger.exception(f"Failed to reset session state for {session_id}: {e}")
