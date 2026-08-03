import os
import jwt
import frappe
from datetime import datetime, timedelta, timezone

@frappe.whitelist()
def get_auth_token():
	"""
	Whitelisted Frappe API endpoint to generate a short-lived JWT for authenticated users.
	Used by the floating chat widget to authenticate with an external FastAPI service.
	Identity is strictly derived from the authenticated Frappe session.
	"""
	session_user = frappe.session.user if hasattr(frappe, "session") and frappe.session else None

	if not session_user or session_user == "Guest":
		frappe.throw("Authentication required to obtain AI Assistant token.", frappe.PermissionError)

	jwt_secret = frappe.conf.get("jwt_secret") or os.getenv("JWT_SECRET")
	if not jwt_secret:
		frappe.throw("AI Assistant authentication failed: JWT_SECRET is not configured in site_config or environment.", frappe.ValidationError)

	full_name = frappe.utils.get_fullname(session_user) or session_user
	roles = frappe.get_roles(session_user) or []

	now = datetime.now(timezone.utc)
	payload = {
		"sub": session_user,
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

	return {
		"token": token,
		"user": session_user,
		"full_name": full_name,
		"roles": roles,
		"expires_in_seconds": 86400
	}

@frappe.whitelist()
def get_chat_token():
	"""Alias endpoint for get_auth_token."""
	return get_auth_token()



