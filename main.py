import os
import jwt
import logging
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
	from dotenv import load_dotenv
	load_dotenv()
except ImportError:
	pass

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fastapi_assistant")

# Initialize FastAPI App
app = FastAPI(
	title="ERPNext AI Assistant Backend Service",
	description="Standalone FastAPI microservice authenticated via JWT from ERPNext Desk",
	version="1.0.0"
)

# -----------------------------------------------------------------------------
# Configuration & Security
# -----------------------------------------------------------------------------
DEFAULT_JWT_SECRET = "my_super_secret_shared_jwt_key_2026_erpnext_assistant_secure"
JWT_SECRET = os.getenv("JWT_SECRET") or DEFAULT_JWT_SECRET
JWT_ALGORITHM = "HS256"

# Configure CORS dynamically for dev and production origins
custom_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
ORIGINS = [
	"http://localhost:8080",
	"http://127.0.0.1:8080",
	"http://localhost:5173",
	"http://127.0.0.1:5173",
	"http://localhost:8000",
	"http://127.0.0.1:8000",
	"http://localhost:8001",
	"http://127.0.0.1:8001",
	"http://localhost:3000",
] + custom_origins

app.add_middleware(
	CORSMiddleware,
	allow_origins=ORIGINS,
	allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*", "Authorization", "Content-Type", "X-Frappe-CSRF-Token"],
)

# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
class ChatRequest(BaseModel):
	message: str

class ChatResponse(BaseModel):
	response: str
	authenticated_user: str
	full_name: str
	roles: List[str]

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

	if DEFAULT_JWT_SECRET not in secrets:
		secrets.append(DEFAULT_JWT_SECRET)

	return secrets

# -----------------------------------------------------------------------------
# JWT Verification Dependency
# -----------------------------------------------------------------------------
async def verify_token(authorization: Optional[str] = Header(None)) -> dict:
	"""
	FastAPI dependency that extracts and validates the Authorization: Bearer <token>
	header sent from the ERPNext Desk React chat widget.
	"""
	if not authorization:
		# Check if running in explicit dev mock mode
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

	candidate_secrets = get_candidate_secrets()
	last_err = None

	for secret in candidate_secrets:
		try:
			payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
			logger.info(f"Successfully authenticated JWT for user: {payload.get('sub')}")
			print(f"[JWT OK] Authenticated user: {payload.get('sub')}")
			return payload
		except jwt.ExpiredSignatureError:
			print("[JWT ERROR] Token has expired")
			logger.warning("JWT token has expired")
			raise HTTPException(
				status_code=status.HTTP_401_UNAUTHORIZED,
				detail="Token has expired. Please refresh your session.",
				headers={"WWW-Authenticate": "Bearer"},
			)
		except jwt.InvalidTokenError as err:
			last_err = err

	print(f"[JWT ERROR] Invalid token: {str(last_err)}")
	logger.error(f"JWT validation failed: {str(last_err)}")
	raise HTTPException(
		status_code=status.HTTP_401_UNAUTHORIZED,
		detail=f"Invalid token: {str(last_err)}",
		headers={"WWW-Authenticate": "Bearer"},
	)

# -----------------------------------------------------------------------------
# API Routes
# -----------------------------------------------------------------------------
@app.get("/health")
def health_check():
	return {"status": "ok", "service": "fastapi-ai-assistant"}

@app.post("/api/chat", response_model=ChatResponse)
@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
	request: ChatRequest,
	payload: dict = Depends(verify_token)
):
	"""
	Protected Chat Endpoint. Authenticated via JWT bearer token.
	"""
	user_email = payload.get("sub", "Unknown")
	full_name = payload.get("full_name", user_email)
	roles = payload.get("roles", [])

	response_text = (
		f"Hello {full_name}! Identity verified as '{user_email}'. "
		f"Active roles: [{', '.join(roles[:4])}{'...' if len(roles) > 4 else ''}]. "
		f"Your message: \"{request.message}\""
	)

	return ChatResponse(
		response=response_text,
		authenticated_user=user_email,
		full_name=full_name,
		roles=roles
	)

if __name__ == "__main__":
	import uvicorn
	print("Starting FastAPI AI Assistant Backend on http://localhost:8000...")
	uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

