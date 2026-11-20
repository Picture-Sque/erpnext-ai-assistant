import os
import sys
import uuid
import json
import logging
from pathlib import Path

# Add the 'agent' directory to sys.path to support importing local modules
# regardless of whether the app is run from inside agent/ or from the repo root.
agent_dir = str(Path(__file__).parent.resolve())
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)
repo_root = str(Path(__file__).parent.parent.resolve())
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import jwt
from typing import List, Optional
# TODO: Remove Security, HTTPBearer, and HTTPAuthorizationCredentials imports once the commented-out old auth block is deleted.
from fastapi import FastAPI, Depends, HTTPException, status, Security, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage

from mcp_erpnext.auth import (
    RequestContext,
    set_request_context,
    get_request_context,
    is_session_expired,
    verify_jwt,
    build_request_context_from_payload,
    AuthTokenError,
    TokenExpiredError,
)
from mcp_erpnext.errors import ERPNextConfigError
from mcp_erpnext.client import Credentials

# Ensure environment variables are loaded
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_main")

# Import compiled graph and states
from workflow.graph import compiled_graph
import chat_store
from audit_logger import init_audit_db
from tools.adapter import resolve_backend, close_adapter

# Setup FastAPI App
app = FastAPI(
    title="ERPNext AI Assistant Agent Service",
    description="FastAPI service hosting LangGraph workflows for ERPNext integrations.",
    version="1.0.0"
)

@app.on_event("startup")
def on_startup():
    chat_store.init_db()
    init_audit_db()
    resolve_backend()
    if os.getenv("ALLOW_DEV_MOCK_AUTH", "false").lower() in ("true", "1"):
        base_url = os.getenv("ERPNEXT_BASE_URL") or os.getenv("ERPNEXT_URL") or "http://localhost:8080"
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            logger.error(
                "====================================================================\n"
                "SECURITY WARNING: ALLOW_DEV_MOCK_AUTH is set to true, but \n"
                f"ERPNEXT_BASE_URL host '{hostname}' is not localhost/127.0.0.1.\n"
                "REFUSING TO ENABLE DEV MOCK AUTH. All unauthenticated requests will be rejected.\n"
                "===================================================================="
            )
        else:
            logger.warning(
                "====================================================================\n"
                "SECURITY WARNING: ALLOW_DEV_MOCK_AUTH is ENABLED.\n"
                "Unauthenticated requests will run as dev_user@example.com.\n"
                "NEVER ENABLE THIS IN PRODUCTION ENVIRONMENTS!\n"
                "===================================================================="
            )

@app.on_event("shutdown")
def on_shutdown():
    close_adapter()

def is_dev_mock_allowed() -> bool:
    allow_dev_auth = os.getenv("ALLOW_DEV_MOCK_AUTH", "false").lower() in ("true", "1")
    if not allow_dev_auth:
        return False
    base_url = os.getenv("ERPNEXT_BASE_URL") or os.getenv("ERPNEXT_URL") or "http://localhost:8080"
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("localhost", "127.0.0.1", "::1"):
        logger.error(
            f"REFUSING DEV MOCK AUTH: ALLOW_DEV_MOCK_AUTH is true but ERPNEXT_BASE_URL host "
            f"'{hostname}' is not localhost/127.0.0.1. Dev mock auth is DISABLED."
        )
        return False
    return True

# CORS Configuration
def get_cors_origins() -> List[str]:
    cors_env = os.getenv("CORS_ALLOWED_ORIGINS")
    if cors_env is not None:
        origins = []
        for o in cors_env.split(","):
            trimmed = o.strip()
            if not trimmed:
                continue
            if trimmed == "*":
                logger.warning(
                    "Wildcard '*' origin is disallowed when allow_credentials=True and has been dropped from CORS origins."
                )
                continue
            origins.append(trimmed)
        return origins
    if is_dev_mock_allowed():
        return [
            "http://localhost:5173",  # Standalone Vite dev server
            "http://127.0.0.1:5173",
            "http://localhost:8000",  # Default Frappe Bench port
            "http://127.0.0.1:8000",
            "http://localhost:8080",  # ERPNext target instance port
            "http://127.0.0.1:8080",
            "http://localhost:8081",
            "http://127.0.0.1:8081",
        ]
    return []

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# TODO: Original static-token auth logic kept for diff comparison
# security = HTTPBearer()
# AGENT_AUTH_TOKEN = os.getenv("AGENT_AUTH_TOKEN", "test-token-123")
# 
# def verify_auth_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
#     """
#     Validates Bearer token auth header against AGENT_AUTH_TOKEN env variable.
#     """
#     if credentials.credentials != AGENT_AUTH_TOKEN:
#         logger.warning("Authentication failed: invalid token provided.")
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid or missing authentication token",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
#     return True

JWT_ALGORITHM = "HS256"

def get_candidate_secrets() -> List[str]:
    secrets = []
    env_secret = os.getenv("JWT_SECRET")
    if env_secret:
        secrets.append(env_secret)

    # Check frappe site config files if accessible relative to workspace
    possible_paths = [
        "../sites/common_site_config.json",
        "../../sites/common_site_config.json",
        "../sites/site1.local/site_config.json",
        "../../sites/site1.local/site_config.json",
        "../sites/frontend/site_config.json",
        "../../sites/frontend/site_config.json",
    ]
    for path in possible_paths:
        try:
            if os.path.exists(path):
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sec = data.get("jwt_secret")
                    if sec and sec not in secrets:
                        secrets.append(sec)
        except Exception:
            pass

    return secrets


async def verify_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency that extracts and validates the Authorization: Bearer <token>
    header sent from the ERPNext Desk React chat widget.
    """
    candidate_secrets = get_candidate_secrets()
    if not candidate_secrets:
        logger.error("No JWT secret key configured in environment or site configuration.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security error: JWT secret key is unconfigured.",
        )

    if not authorization:
        if is_dev_mock_allowed():
            logger.info("No Authorization header found; using ALLOW_DEV_MOCK_AUTH fallback.")
            return {
                "sub": "dev_user@example.com",
                "full_name": "Standalone Developer",
                "roles": ["System Manager", "Developer"],
                "is_dev_mock": True,
            }
        logger.warning("Missing Authorization header in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Please supply 'Authorization: Bearer <token>' header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Split 'Bearer <token>'
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("Malformed Authorization header format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    try:
        payload = verify_jwt(token, candidate_secrets, algorithms=[JWT_ALGORITHM], require_exp=False, require_sub=False)
        logger.info(f"Successfully authenticated JWT for user: {payload.get('sub')}")
        return payload
    except TokenExpiredError:
        logger.warning("JWT token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please refresh your session.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AuthTokenError as err:
        logger.error(f"JWT validation failed: {str(err)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(err),
            headers={"WWW-Authenticate": "Bearer"},
        )

# Request and Response models
class ChatRequest(BaseModel):
    message: str
    conversation_id: str = None

class ChatResponse(BaseModel):
    response: str

# One mixed chain turn (up to MAX_CHAIN_STEPS steps, each ~10 nodes) exceeds
# LangGraph's default recursion limit of 25 super-steps.
GRAPH_RECURSION_LIMIT = 200

_WRITE_TOOLS = ("create_document", "update_document", "delete_document", "cancel_document", "submit_document")


def build_persisted_state(updated_state: dict) -> dict:
    """
    What survives to the next request. A pending confirmation or a pending request for
    missing details, and the chain it belongs to, are always persisted while pending,
    regardless of is_workflow_complete, so the next turn can answer it and resume.
    Everything else follows the complete/incomplete rules below.
    """
    complete = bool(updated_state.get("is_workflow_complete"))
    pending = updated_state.get("pending_confirmation")
    pending_input = updated_state.get("pending_input")

    extra_state = {
        # Survive completed turns: follow-up resolution and "submit it" style references.
        "last_turn_context": updated_state.get("last_turn_context"),
        "last_created_doc": updated_state.get("last_created_doc"),
        "pending_confirmation": pending,
        "pending_input": pending_input,
    }
    if not complete:
        extra_state.update({
            "ambiguous_candidates": updated_state.get("ambiguous_candidates"),
            "clarification_target": updated_state.get("clarification_target"),
            "clarification_attempts": updated_state.get("clarification_attempts"),
            "resolved_entities": updated_state.get("resolved_entities"),
            "bulk_operation_scope": updated_state.get("bulk_operation_scope"),
            "pending_slot_clarification": updated_state.get("pending_slot_clarification"),
        })
    if (pending or pending_input) and updated_state.get("chain_plan") and not updated_state.get("chain_aborted"):
        extra_state.update({
            "chain_plan": updated_state.get("chain_plan"),
            "chain_step_index": updated_state.get("chain_step_index"),
            "chain_results": updated_state.get("chain_results"),
            "chain_step_status": updated_state.get("chain_step_status"),
            "chain_id": updated_state.get("chain_id"),
            "chain_started_at": updated_state.get("chain_started_at"),
        })

    # Reset slots after a finished write, a fallback or a permission denial so they
    # are not reused by the next request. Read results keep intent/fields so pronoun
    # follow-ups ("what is their customer group?") still work.
    failure = updated_state.get("failure_classification") or ""
    should_reset = complete and not pending and not pending_input and (
        updated_state.get("target_tool", "") in _WRITE_TOOLS
        or updated_state.get("detected_intent") == "fallback"
        or failure.startswith("permission_denied")
    )
    if should_reset:
        return {"detected_intent": "", "collected_fields": {}, "is_complete": True, "extra_state": extra_state}
    return {
        "detected_intent": updated_state.get("detected_intent", ""),
        "collected_fields": updated_state.get("collected_fields", {}),
        "is_complete": complete,
        "extra_state": extra_state,
    }


def _build_request_context(payload: dict) -> RequestContext:
    try:
        return build_request_context_from_payload(payload, require_ctx=True)
    except (ERPNextConfigError, AuthTokenError) as exc:
        logger.warning("Request context build failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, payload: dict = Depends(verify_token)):
    user_sub = payload.get("sub")
    if not user_sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token payload missing required 'sub' claim"
        )
    session_id = request.conversation_id or user_sub
    user_id = payload.get("full_name") or user_sub

    with set_request_context(_build_request_context(payload)):
        try:
            # A conversation belongs to the user who created it; nobody else may load it,
            # read its history, or answer its pending confirmations.
            chat_store.verify_conversation_owner(session_id, user_sub, user_id=user_id)
        except chat_store.ConversationAccessError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This conversation belongs to another user."
            )

        try:
            session_state = chat_store.load_session_state(session_id, user_roles=payload.get("roles", []))
            chat_store.save_message(session_id, "user", request.message)
        except chat_store.ChatStoreError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

        session_state["messages"].append(HumanMessage(content=request.message))
        session_state["user_id"] = user_id
        session_state["user_sub"] = user_sub

        try:
            # Run state machine iteration
            updated_state = compiled_graph.invoke(session_state, config={"recursion_limit": GRAPH_RECURSION_LIMIT})
        except Exception as e:
            if is_session_expired():
                logger.warning(f"ERPNext session expired during graph execution for user '{user_sub}'")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session has expired. Please refresh your session or log in again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            logger.exception("Error executing conversation graph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error executing conversation graph: {str(e)}"
            )

        if is_session_expired():
            logger.warning(f"ERPNext session expired during turn for user '{user_sub}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has expired. Please refresh your session or log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        final_resp = updated_state.get("final_response", "")
        persisted = build_persisted_state(updated_state)
        try:
            chat_store.save_message(session_id, "assistant", final_resp, intent=updated_state.get("detected_intent", ""))
            chat_store.update_session_state(
                session_id,
                detected_intent=persisted["detected_intent"],
                collected_fields=persisted["collected_fields"],
                is_complete=persisted["is_complete"],
                extra_state=persisted["extra_state"],
            )
        except chat_store.ChatStoreError as e:
            # The turn ran, but its state (possibly a pending confirmation) was not saved.
            # Say so rather than return a response the next turn cannot honor.
            logger.error(f"Turn completed but state was not saved for {session_id}. Response was: {final_resp!r}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{e}. The last response was: {final_resp}"
            )

        return ChatResponse(response=final_resp)

@app.post("/chat/reset")
async def reset_session(payload: dict = Depends(verify_token)):
    """
    Helper endpoint to clear conversation session memory.
    """
    session_id = payload.get("sub")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token payload missing required 'sub' claim"
        )
    chat_store.reset_session_state(session_id)
    return {"status": "session reset successful"}


def _require_sub(payload: dict) -> str:
    user_sub = payload.get("sub")
    if not user_sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token payload missing required 'sub' claim"
        )
    return user_sub


@app.get("/conversations")
async def list_conversations_endpoint(payload: dict = Depends(verify_token)):
    """The caller's past conversations for the chat history sidebar, newest first."""
    user_sub = _require_sub(payload)
    try:
        return {"conversations": chat_store.list_conversations(user_sub)}
    except chat_store.ChatStoreError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/conversations/{conversation_id}/messages")
async def conversation_messages_endpoint(conversation_id: str, payload: dict = Depends(verify_token)):
    user_sub = _require_sub(payload)
    try:
        return {"messages": chat_store.get_conversation_messages(conversation_id, user_sub)}
    except chat_store.ConversationAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This conversation belongs to another user.")
    except chat_store.ChatStoreError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


class RenameConversationRequest(BaseModel):
    title: str


@app.patch("/conversations/{conversation_id}")
async def rename_conversation_endpoint(
    conversation_id: str, request: RenameConversationRequest, payload: dict = Depends(verify_token)
):
    user_sub = _require_sub(payload)
    try:
        renamed = chat_store.rename_conversation(conversation_id, user_sub, request.title)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except chat_store.ConversationAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This conversation belongs to another user.")
    except chat_store.ChatStoreError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    if not renamed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"status": "renamed", "title": " ".join(request.title.split())}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, payload: dict = Depends(verify_token)):
    user_sub = _require_sub(payload)
    try:
        deleted = chat_store.delete_conversation(conversation_id, user_sub)
    except chat_store.ConversationAccessError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This conversation belongs to another user.")
    except chat_store.ChatStoreError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"status": "deleted"}


# Standalone endpoints for Generic CRUD Tools
from tools.generic_tools import create_document, get_list, update_document, delete_document

class GenericToolRequest(BaseModel):
    doctype_name: str
    id: Optional[str] = None
    parameters: Optional[dict] = None

def get_tools_api_allowed_roles() -> List[str]:
    raw = os.getenv("TOOLS_API_ALLOWED_ROLES")
    if raw is not None:
        roles = [r.strip() for r in raw.split(",") if r.strip()]
        if roles:
            return roles
    # Fail closed: empty or missing config means System Manager only
    return ["System Manager"]

def verify_tools_api_role(payload: dict = Depends(verify_token)) -> dict:
    """
    Role gate for /api/tools/* endpoints.
    Requires caller to possess at least one of the configured TOOLS_API_ALLOWED_ROLES.
    Fails closed: defaults to ['System Manager'].
    """
    allowed_roles = get_tools_api_allowed_roles()
    user_roles = payload.get("roles", [])
    if not isinstance(user_roles, (list, tuple, set)):
        user_roles = [user_roles] if user_roles else []

    if not any(role in allowed_roles for role in user_roles):
        logger.warning(
            f"Access denied to /api/tools for user '{payload.get('sub')}': "
            f"roles {user_roles} lack required {allowed_roles}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: System Manager role required for direct tools API."
        )
    return payload

@app.post("/api/tools/list")
async def list_doctype_endpoint(request: GenericToolRequest, payload: dict = Depends(verify_tools_api_role)):
    with set_request_context(_build_request_context(payload)):
        params = request.parameters or {}
        return get_list(
            request.doctype_name,
            filters=params.get("filters"),
            fields=params.get("fields"),
            limit=params.get("limit"),
            order_by=params.get("order_by")
        )

@app.post("/api/tools/add")
async def add_doctype_endpoint(request: GenericToolRequest, payload: dict = Depends(verify_tools_api_role)):
    ctx = _build_request_context(payload)
    if ctx.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted: this session is read-only.",
        )
    with set_request_context(ctx):
        return create_document(request.doctype_name, request.parameters or {})

@app.put("/api/tools/update")
async def update_doctype_endpoint(request: GenericToolRequest, payload: dict = Depends(verify_tools_api_role)):
    if not request.id:
        raise HTTPException(status_code=400, detail="Missing record 'id' for update")
    ctx = _build_request_context(payload)
    if ctx.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted: this session is read-only.",
        )
    with set_request_context(ctx):
        return update_document(request.doctype_name, request.id, request.parameters or {})

@app.delete("/api/tools/delete")
async def delete_doctype_endpoint(request: GenericToolRequest, payload: dict = Depends(verify_tools_api_role)):
    if not request.id:
        raise HTTPException(status_code=400, detail="Missing record 'id' for delete")
    ctx = _build_request_context(payload)
    if ctx.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted: this session is read-only.",
        )
    with set_request_context(ctx):
        return delete_document(request.doctype_name, request.id)

if __name__ == "__main__":
    import uvicorn
    # Run locally on 0.0.0.0:8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

