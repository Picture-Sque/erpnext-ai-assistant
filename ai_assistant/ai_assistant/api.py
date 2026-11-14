import json
import os
import base64
import hashlib
import jwt
import frappe
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet

try:
	from frappe.sessions import get_csrf_token
except ImportError:
	try:
		get_csrf_token = getattr(getattr(frappe, "sessions", None), "get_csrf_token", lambda: "")
	except Exception:
		get_csrf_token = lambda: ""

@frappe.whitelist()
def get_auth_token():
	"""
	Whitelisted Frappe API endpoint to generate a short-lived JWT for authenticated users.
	Used by the floating chat widget to authenticate with an external FastAPI service.
	Identity is strictly derived from the authenticated Frappe session.
	Encrypted session credentials (sid and CSRF token) are included in the 'ctx' claim.
	"""
	session_user = frappe.session.user if hasattr(frappe, "session") and frappe.session else None

	if not session_user or session_user == "Guest":
		frappe.throw("Authentication required to obtain AI Assistant token.", frappe.PermissionError)

	jwt_secret = frappe.conf.get("jwt_secret") or os.getenv("JWT_SECRET")
	if not jwt_secret:
		frappe.throw("AI Assistant authentication failed: JWT_SECRET is not configured in site_config or environment.", frappe.ValidationError)

	enc_key = frappe.conf.get("credential_enc_key") or os.getenv("CREDENTIAL_ENC_KEY")
	if not enc_key:
		frappe.throw("AI Assistant authentication failed: CREDENTIAL_ENC_KEY is not configured.", frappe.ValidationError)

	full_name = frappe.utils.get_fullname(session_user) or session_user
	roles = frappe.get_roles(session_user) or []

	sid = getattr(frappe.session, "sid", None)
	if not sid and hasattr(frappe, "local") and hasattr(frappe.local, "session"):
		sid = getattr(frappe.local.session, "sid", None)
	if not isinstance(sid, str):
		sid = str(sid) if (sid is not None and not hasattr(sid, "_mock_name")) else ""

	csrf_token = get_csrf_token()
	if not isinstance(csrf_token, str):
		csrf_token = str(csrf_token) if (csrf_token is not None and not hasattr(csrf_token, "_mock_name")) else ""

	try:
		fernet = Fernet(enc_key.encode("utf-8") if isinstance(enc_key, str) else enc_key)
	except ValueError:
		derived = base64.urlsafe_b64encode(hashlib.sha256(enc_key.encode("utf-8") if isinstance(enc_key, str) else enc_key).digest())
		fernet = Fernet(derived)
	ctx_payload = json.dumps({"sid": sid, "csrf_token": csrf_token})
	encrypted_ctx = fernet.encrypt(ctx_payload.encode("utf-8")).decode("utf-8")

	now = datetime.now(timezone.utc)
	payload = {
		"sub": session_user,
		"full_name": full_name,
		"roles": roles,
		"ctx": encrypted_ctx,
		"iat": now,
		"exp": now + timedelta(hours=1)
	}

	# Sign token with HMAC-SHA256
	token = jwt.encode(payload, jwt_secret, algorithm="HS256")

	# PyJWT Compatibility: Convert bytes to string if PyJWT v1 returns bytes
	if isinstance(token, bytes):
		token = token.decode("utf-8")

	return {
		"token": token,
		"user": session_user,
		"full_name": full_name,
		"roles": roles,
		"expires_in_seconds": 3600
	}

@frappe.whitelist()
def get_chat_token():
	"""Alias endpoint for get_auth_token."""
	return get_auth_token()



