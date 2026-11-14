import json
import os
import jwt
import frappe
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from frappe.sessions import get_csrf_token

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

	try:
		fernet = Fernet(enc_key.encode("utf-8") if isinstance(enc_key, str) else enc_key)
	except Exception:
		frappe.throw("AI Assistant authentication failed: Invalid CREDENTIAL_ENC_KEY.", frappe.ValidationError)

	full_name = frappe.utils.get_fullname(session_user) or session_user
	roles = frappe.get_roles(session_user) or []

	sid = getattr(frappe.session, "sid", None)
	if not sid and hasattr(frappe, "local") and hasattr(frappe.local, "session"):
		sid = getattr(frappe.local.session, "sid", None)

	csrf_token = get_csrf_token()

	if not sid or not csrf_token:
		frappe.throw("AI Assistant authentication failed: Missing active session sid or CSRF token.", frappe.AuthenticationError)

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

	return {
		"status": "success",
		"token": token,
		"user": session_user,
		"full_name": full_name,
		"roles": roles,
		"expires_in_seconds": 3600
	}

@frappe.whitelist()
def get_chat_token():
	"""
	Alias for get_auth_token to preserve backwards compatibility with desk assets.
	"""
	return get_auth_token()
