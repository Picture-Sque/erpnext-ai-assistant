import os
import sys
import logging
from pathlib import Path

# Add the 'agent' directory to sys.path to support importing local modules
# regardless of whether the app is run from inside agent/ or from the repo root.
agent_dir = str(Path(__file__).parent.resolve())
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)

from fastapi import FastAPI, Depends, HTTPException, status, Security
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

# Setup FastAPI App
app = FastAPI(
    title="ERPNext AI Assistant Agent Service",
    description="FastAPI service hosting LangGraph workflows for ERPNext integrations.",
    version="1.0.0"
)

# CORS Configuration
origins = [
    "http://localhost:5173",  # Standalone Vite dev server
    "http://localhost:8000",  # Default Frappe Bench port (often used for assets proxy)
    "http://localhost:8080",  # Alternative Frappe Bench dev server port
    "http://localhost:8081",  # ERPNext target instance port
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication Verification Dependency
security = HTTPBearer()
AGENT_AUTH_TOKEN = os.getenv("AGENT_AUTH_TOKEN", "test-token-123")

def verify_auth_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Validates Bearer token auth header against AGENT_AUTH_TOKEN env variable.
    """
    if credentials.credentials != AGENT_AUTH_TOKEN:
        logger.warning("Authentication failed: invalid token provided.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True

# Request and Response models
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

# In-memory session store to support multi-turn conversations
# Maps session_id (e.g. "default-session") to current AgentState dictionary
session_store = {}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, authorized: bool = Depends(verify_auth_token)):
    session_id = "default-session"  # Simple single-session persistence for dev testing
    
    # Initialize session if not present
    if session_id not in session_store:
        session_store[session_id] = {
            "messages": [],
            "detected_intent": "",
            "collected_fields": {},
            "final_response": ""
        }
        
    session_state = session_store[session_id]
    
    # Append the new user message
    session_state["messages"].append(HumanMessage(content=request.message))
    
    try:
        # Run state machine iteration
        updated_state = compiled_graph.invoke(session_state)
        
        # Persist updated values back to session store
        session_store[session_id] = {
            "messages": updated_state["messages"],
            "detected_intent": updated_state.get("detected_intent", ""),
            "collected_fields": updated_state.get("collected_fields", {}),
            "final_response": updated_state.get("final_response", "")
        }
        
        # If the sales order creation successfully completes, or falls back, we can clear slots for the next order
        final_resp = updated_state.get("final_response", "")
        if (
            "Successfully created" in final_resp or 
            "Failed to create" in final_resp or 
            "Inventory status for item" in final_resp or
            "No inventory bins found" in final_resp or
            "Failed to check inventory" in final_resp or
            "Customer details for" in final_resp or
            "was not found in ERPNext" in final_resp or
            "Failed to look up customer" in final_resp or
            "How can I help you today?" in final_resp
        ):
            # Reset workflow slots so subsequent queries start fresh
            session_store[session_id]["collected_fields"] = {}
            session_store[session_id]["detected_intent"] = ""
            
        return ChatResponse(response=final_resp)
        
    except Exception as e:
        logger.exception("Error executing conversation graph")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error executing conversation graph: {str(e)}"
        )

@app.post("/chat/reset")
async def reset_session(authorized: bool = Depends(verify_auth_token)):
    """
    Helper endpoint to clear conversation session memory.
    """
    session_id = "default-session"
    if session_id in session_store:
        del session_store[session_id]
    return {"status": "session reset successful"}

if __name__ == "__main__":
    import uvicorn
    # Run locally on localhost:8000
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
