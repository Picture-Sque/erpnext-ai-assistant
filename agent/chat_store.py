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


class ChatStoreError(RuntimeError):
    """Conversation state could not be read or written. Never swallowed silently."""


class ConversationAccessError(PermissionError):
    """The authenticated user does not own the requested conversation."""


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

        # Migration: conversation owner (JWT 'sub'), used to stop one user from loading
        # or answering another user's conversation.
        cursor.execute("PRAGMA table_info(conversations);")
        conv_columns = [row["name"] for row in cursor.fetchall()]
        if "owner_sub" not in conv_columns:
            cursor.execute("ALTER TABLE conversations ADD COLUMN owner_sub TEXT;")
        
        # Create index on session_id for fast lookup
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);")
        conn.commit()
    logger.info("SQLite database initialization complete.")


def get_or_create_conversation(session_id: str, user_id: str = "", owner_sub: Optional[str] = None) -> int:
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
            "INSERT INTO conversations (session_id, user_id, owner_sub, created_at, updated_at) VALUES (?, ?, ?, ?, ?);",
            (session_id, user_id, owner_sub, now, now)
        )
        conversation_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT OR IGNORE INTO conversation_state (conversation_id, detected_intent, current_skill, collected_fields, workflow_complete, extra_state, updated_at) VALUES (?, '', '', '{}', 0, '{}', ?);",
            (conversation_id, now)
        )
        conn.commit()
        return conversation_id


def verify_conversation_owner(session_id: str, owner_sub: str, user_id: str = "") -> int:
    """
    Return the conversation id for session_id if owner_sub owns it, creating it (owned by
    owner_sub) when new. Raises ConversationAccessError for another user's conversation.
    Conversations created before ownership was recorded are claimed by their first
    authenticated user after the migration, and the claim is logged.
    """
    if not owner_sub:
        raise ConversationAccessError("Authenticated user has no 'sub'; cannot scope the conversation.")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, owner_sub FROM conversations WHERE session_id = ?;", (session_id,))
        row = cursor.fetchone()
        if row is None:
            now = datetime.utcnow().isoformat()
            cursor.execute(
                "INSERT INTO conversations (session_id, user_id, owner_sub, created_at, updated_at) VALUES (?, ?, ?, ?, ?);",
                (session_id, user_id, owner_sub, now, now)
            )
            conversation_id = cursor.lastrowid
            cursor.execute(
                "INSERT OR IGNORE INTO conversation_state (conversation_id, detected_intent, current_skill, collected_fields, workflow_complete, extra_state, updated_at) VALUES (?, '', '', '{}', 0, '{}', ?);",
                (conversation_id, now)
            )
            conn.commit()
            return conversation_id
        if row["owner_sub"] is None:
            logger.warning(f"Conversation '{session_id}' had no recorded owner; assigning it to '{owner_sub}'.")
            cursor.execute("UPDATE conversations SET owner_sub = ? WHERE id = ?;", (owner_sub, row["id"]))
            conn.commit()
            return row["id"]
        if row["owner_sub"] != owner_sub:
            logger.warning(f"User '{owner_sub}' denied access to conversation '{session_id}' owned by another user.")
            raise ConversationAccessError(f"Conversation '{session_id}' belongs to another user.")
        return row["id"]


def load_session_state(session_id: str, user_roles: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Loads conversation messages and slot state from SQLite for a session_id.
    Reconstructs LangChain HumanMessage and AIMessage objects.

    Raises ChatStoreError if the state cannot be read or parsed. Returning an empty
    state instead would silently drop a pending confirmation and its chain.
    Expiry of pending confirmations (and their chains) is enforced by
    pending_action_gate_node, not here.
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
    except sqlite3.Error as e:
        logger.exception(f"Failed to load session state for {session_id}")
        raise ChatStoreError(f"Could not load conversation state: {e}") from e

    detected_intent = ""
    collected_fields: Dict[str, Any] = {}
    workflow_complete = False
    extra_state: Dict[str, Any] = {}

    if state_row:
        detected_intent = state_row["detected_intent"] or ""
        workflow_complete = bool(state_row["workflow_complete"])
        try:
            collected_fields = json.loads(state_row["collected_fields"] or "{}")
            extra_state = json.loads(state_row["extra_state"] or "{}")
        except json.JSONDecodeError as e:
            logger.exception(f"Corrupt conversation state JSON for session {session_id}")
            raise ChatStoreError(f"Conversation state is corrupt: {e}") from e

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
        "last_created_doc": extra_state.get("last_created_doc"),
        # Follow-up router state
        "last_turn_context": extra_state.get("last_turn_context"),
        "pending_slot_clarification": extra_state.get("pending_slot_clarification"),
        # Chain state (persisted only while an action in the chain awaits confirmation)
        "chain_plan": extra_state.get("chain_plan"),
        "chain_step_index": extra_state.get("chain_step_index"),
        "chain_results": extra_state.get("chain_results"),
        "chain_step_status": extra_state.get("chain_step_status"),
        "chain_id": extra_state.get("chain_id"),
        "chain_started_at": extra_state.get("chain_started_at"),
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
    except sqlite3.Error as e:
        logger.exception(f"Failed to save message for session {session_id}")
        raise ChatStoreError(f"Could not save the message: {e}") from e


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
    except (sqlite3.Error, TypeError, ValueError) as e:
        # TypeError/ValueError: state that is not JSON-serializable.
        logger.exception(f"Failed to update session state for {session_id}")
        raise ChatStoreError(f"Could not save conversation state: {e}") from e


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
