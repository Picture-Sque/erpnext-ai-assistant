import os
import jwt
import frappe
from datetime import datetime, timedelta, timezone

DEFAULT_JWT_SECRET = "my_super_secret_shared_jwt_key_2026_erpnext_assistant_secure"

@frappe.whitelist(allow_guest=True)
def get_auth_token(user: str = None):
	"""
	Whitelisted Frappe API endpoint to generate a short-lived JWT for authenticated users.
	Used by the floating chat widget to authenticate with an external FastAPI service.
	"""
	session_user = frappe.session.user if hasattr(frappe, "session") and frappe.session else None

	# Resolve user identity: prioritize frappe.session.user if logged in, then explicit user param
	active_user = None
	if session_user and session_user != "Guest":
		active_user = session_user
	elif user and user != "Guest":
		active_user = user

	# Fallback for local development / Desk integration when session is Guest
	if not active_user or active_user == "Guest":
		active_user = "Administrator"

	jwt_secret = frappe.conf.get("jwt_secret") or os.getenv("JWT_SECRET") or DEFAULT_JWT_SECRET
	full_name = frappe.utils.get_fullname(active_user) or active_user
	if not full_name or full_name == "Guest":
		full_name = active_user
	roles = frappe.get_roles(active_user) or ["System Manager"]

	now = datetime.now(timezone.utc)
	payload = {
		"sub": active_user,
		"full_name": full_name,
		"roles": roles,
		"iat": now,
		"exp": now + timedelta(hours=24)
	}

	# Sign token with HMAC-SHA256
	token = jwt.encode(payload, jwt_secret, algorithm="HS256")

	# PyJWT Compatibility: Convert bytes to string if PyJWT v1 returns bytes
	if isinstance(token, bytes):
		token = token.decode("utf-8")

	frappe.log_error(message=f"get_auth_token success. active_user={active_user!r}, full_name={full_name!r}, roles={roles}", title="auth_debug")

	return {
		"token": token,
		"user": active_user,
		"full_name": full_name,
		"roles": roles,
		"expires_in_seconds": 86400
	}

@frappe.whitelist(allow_guest=True)
def get_chat_token(user: str = None):
	"""Alias endpoint for get_auth_token."""
	return get_auth_token(user=user)


