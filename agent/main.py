import os
import sys
import logging
from pathlib import Path

# Add the 'agent' directory to sys.path to support importing local modules
# regardless of whether the app is run from inside agent/ or from the repo root.
agent_dir = str(Path(__file__).parent.resolve())
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)

import jwt
from typing import List, Optional
# TODO: Remove Security, HTTPBearer, and HTTPAuthorizationCredentials imports once the commented-out old auth block is deleted.
from fastapi import FastAPI, Depends, HTTPException, status, Security, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage

# Ensure environment variables are loaded
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_main")

# Import compiled graph and states
from workflow.graph import compiled_graph
import chat_store

# Setup FastAPI App
app = FastAPI(
    title="ERPNext AI Assistant Agent Service",
    description="FastAPI service hosting LangGraph workflows for ERPNext integrations.",
    version="1.0.0"
)

@app.on_event("startup")
def on_startup():
    chat_store.init_db()

# CORS Configuration
origins = [
    "http://localhost:5173",  # Standalone Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:8000",  # Default Frappe Bench port
    "http://127.0.0.1:8000",
    "http://localhost:8080",  # ERPNext target instance port (confirmed port 8080)
    "http://127.0.0.1:8080",
    "http://localhost:8081",
    "http://127.0.0.1:8081",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        allow_dev_auth = os.getenv("ALLOW_DEV_MOCK_AUTH", "false").lower() in ("true", "1")
        if allow_dev_auth:
            logger.info("No Authorization header found; using ALLOW_DEV_MOCK_AUTH fallback.")
            return {
                "sub": "dev_user@example.com",
                "full_name": "Standalone Developer",
                "roles": ["System Manager", "Developer"],
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
        logger.warning(f"Malformed Authorization header: {authorization[:30]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    last_err = None

    for secret in candidate_secrets:
        try:
            payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
            logger.info(f"Successfully authenticated JWT for user: {payload.get('sub')}")
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token has expired")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired. Please refresh your session.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as err:
            last_err = err

    logger.error(f"JWT validation failed: {str(last_err)}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Invalid token: {str(last_err)}",
        headers={"WWW-Authenticate": "Bearer"},
    )

# Request and Response models
class ChatRequest(BaseModel):
    message: str
    conversation_id: str = None

class ChatResponse(BaseModel):
    response: str

# In-memory session store to support multi-turn conversations
# Maps session_id (e.g. "default-session") to current AgentState dictionary
session_store = {}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, payload: dict = Depends(verify_token)):
    session_id = request.conversation_id or payload.get("sub")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token payload missing required 'sub' claim"
        )
    
    user_id = payload.get("full_name") or payload.get("sub") or ""
    # Ensure conversation record exists and load persistent session state from SQLite
    chat_store.get_or_create_conversation(session_id, user_id=user_id)
    session_state = chat_store.load_session_state(session_id, user_roles=payload.get("roles", []))
    
    # Save user message to SQLite message history
    chat_store.save_message(session_id, "user", request.message)
    session_state["messages"].append(HumanMessage(content=request.message))
    session_state["user_id"] = user_id
    
    try:
        # Run state machine iteration
        updated_state = compiled_graph.invoke(session_state)
        final_resp = updated_state.get("final_response", "")
        
        # Save assistant message to SQLite message history
        chat_store.save_message(
            session_id,
            "assistant",
            final_resp,
            intent=updated_state.get("detected_intent", "")
        )
        
        # Reset workflow slots if the query completes a WRITE operation or falls back
        # For READ operations (customer lookup, inventory) keep intent/fields so
        # follow-up questions like "What is their customer group?" still work.
        target_tool = updated_state.get("target_tool", "")
        is_write_op = target_tool in ("create_document", "update_document", "delete_document", "cancel_document", "submit_document")
        
        should_reset = (
            (updated_state.get("is_workflow_complete") and is_write_op) or
            "Successfully created" in final_resp or
            "Failed to create" in final_resp or
            "How can I help you today?" in final_resp or
            "Permission Denied" in final_resp
        )
        
        extra_state = {}
        if not updated_state.get("is_workflow_complete"):
            extra_state = {
                "pending_confirmation": updated_state.get("pending_confirmation"),
                "ambiguous_candidates": updated_state.get("ambiguous_candidates"),
                "clarification_target": updated_state.get("clarification_target"),
                "clarification_attempts": updated_state.get("clarification_attempts"),
                "resolved_entities": updated_state.get("resolved_entities"),
                "bulk_operation_scope": updated_state.get("bulk_operation_scope"),
                "pending_slot_clarification": updated_state.get("pending_slot_clarification"),
            }
            if updated_state.get("chain_plan") and not updated_state.get("chain_aborted"):
                from datetime import datetime, timezone
                extra_state["chain_plan"] = updated_state.get("chain_plan")
                extra_state["chain_step_index"] = updated_state.get("chain_step_index")
                extra_state["chain_results"] = updated_state.get("chain_results")
                extra_state["chain_started_at"] = updated_state.get("chain_started_at") or datetime.now(tz=timezone.utc).isoformat()
            
        # last_turn_context persists across completed turns so follow-up resolution
        # can reference the previous successful read-skill result on the NEXT request.
        # Always include it (None is fine — it won't break anything).
        extra_state["last_turn_context"] = updated_state.get("last_turn_context")
        
        if should_reset:
            chat_store.update_session_state(
                session_id,
                detected_intent="",
                collected_fields={},
                is_complete=True,
                extra_state={}
            )
        else:
            chat_store.update_session_state(
                session_id,
                detected_intent=updated_state.get("detected_intent", ""),
                collected_fields=updated_state.get("collected_fields", {}),
                is_complete=updated_state.get("is_workflow_complete", False),
                extra_state=extra_state
            )
            
        return ChatResponse(response=final_resp)
        
    except Exception as e:
        logger.exception("Error executing conversation graph")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing conversation graph: {str(e)}"
        )

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


# Standalone endpoints for Generic CRUD Tools
from tools.generic_tools import create_document, get_list, update_document, delete_document

class GenericToolRequest(BaseModel):
    doctype_name: str
    id: Optional[str] = None
    parameters: Optional[dict] = None

@app.post("/api/tools/list")
async def list_doctype_endpoint(request: GenericToolRequest):
    params = request.parameters or {}
    return get_list(request.doctype_name, filters=params.get("filters"), fields=params.get("fields"), limit=params.get("limit"))

@app.post("/api/tools/add")
async def add_doctype_endpoint(request: GenericToolRequest):
    return create_document(request.doctype_name, request.parameters or {})

@app.put("/api/tools/update")
async def update_doctype_endpoint(request: GenericToolRequest):
    if not request.id:
        raise HTTPException(status_code=400, detail="Missing record 'id' for update")
    return update_document(request.doctype_name, request.id, request.parameters or {})

@app.delete("/api/tools/delete")
async def delete_doctype_endpoint(request: GenericToolRequest):
    if not request.id:
        raise HTTPException(status_code=400, detail="Missing record 'id' for delete")
    return delete_document(request.doctype_name, request.id)

if __name__ == "__main__":
    import uvicorn
    # Run locally on 0.0.0.0:8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

