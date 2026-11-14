import os
import sys
import json
import unittest
import asyncio
import jwt
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

# Add parent directory to path to import main and ai_assistant
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set environment JWT_SECRET for test suite
TEST_SECRET = "test_environment_jwt_secret_key_32bytes_long!"
os.environ["JWT_SECRET"] = TEST_SECRET

from agent.main import app, verify_token, JWT_ALGORITHM, get_candidate_secrets

class TestAuth(unittest.TestCase):
	def setUp(self):
		self.secret = TEST_SECRET
		self.algorithm = JWT_ALGORITHM
		self._env_patch = patch.dict(os.environ, {"ALLOW_DEV_MOCK_AUTH": "false"})
		self._env_patch.start()

	def tearDown(self):
		self._env_patch.stop()

	def create_valid_token(self, user="testuser@example.com", full_name="Test User", roles=None):
		if roles is None:
			roles = ["System Manager", "Script Manager"]
		now = datetime.now(timezone.utc)
		payload = {
			"sub": user,
			"full_name": full_name,
			"roles": roles,
			"iat": now,
			"exp": now + timedelta(hours=24)
		}
		return jwt.encode(payload, self.secret, algorithm=self.algorithm)

	def create_expired_token(self, user="testuser@example.com"):
		now = datetime.now(timezone.utc) - timedelta(hours=2)
		payload = {
			"sub": user,
			"full_name": "Expired User",
			"roles": ["Guest"],
			"iat": now - timedelta(hours=24),
			"exp": now
		}
		return jwt.encode(payload, self.secret, algorithm=self.algorithm)

	def test_verify_token_missing_header(self):
		async def run():
			with self.assertRaises(HTTPException) as ctx:
				await verify_token(authorization=None)
			self.assertEqual(ctx.exception.status_code, 401)
			self.assertIn("Missing Authorization header", ctx.exception.detail)
		asyncio.run(run())

	def test_verify_token_malformed_header(self):
		async def run():
			with self.assertRaises(HTTPException) as ctx:
				await verify_token(authorization="InvalidFormat")
			self.assertEqual(ctx.exception.status_code, 401)
			self.assertIn("Invalid Authorization header format", ctx.exception.detail)
		asyncio.run(run())

	def test_verify_token_invalid_signature(self):
		token = self.create_valid_token()
		wrong_secret = "wrong_secret_key_12345"
		payload = jwt.decode(token, options={"verify_signature": False})
		bad_token = jwt.encode(payload, wrong_secret, algorithm=self.algorithm)

		async def run():
			with self.assertRaises(HTTPException) as ctx:
				await verify_token(authorization=f"Bearer {bad_token}")
			self.assertEqual(ctx.exception.status_code, 401)
			self.assertIn("Invalid token", ctx.exception.detail)
		asyncio.run(run())

	def test_verify_token_expired(self):
		expired_token = self.create_expired_token()
		async def run():
			with self.assertRaises(HTTPException) as ctx:
				await verify_token(authorization=f"Bearer {expired_token}")
			self.assertEqual(ctx.exception.status_code, 401)
			self.assertIn("Token has expired", ctx.exception.detail)
		asyncio.run(run())

	def test_verify_token_valid(self):
		token = self.create_valid_token(user="admin@example.com", full_name="Admin User", roles=["System Manager"])
		async def run():
			payload = await verify_token(authorization=f"Bearer {token}")
			self.assertEqual(payload.get("sub"), "admin@example.com")
			self.assertEqual(payload.get("full_name"), "Admin User")
			self.assertEqual(payload.get("roles"), ["System Manager"])
		asyncio.run(run())

	def test_missing_jwt_secret_fails_closed(self):
		"""FastAPI must fail closed with 500 when no JWT_SECRET is configured."""
		with patch.dict(os.environ, {}, clear=True), patch("agent.main.get_candidate_secrets", return_value=[]):
			async def run():
				with self.assertRaises(HTTPException) as ctx:
					await verify_token(authorization="Bearer sdr.sdf.sdf")
				self.assertEqual(ctx.exception.status_code, 500)
				self.assertIn("JWT secret key is unconfigured", ctx.exception.detail)
			asyncio.run(run())

	def test_frappe_api_guest_rejection(self):
		"""Frappe API must reject Guest users with PermissionError."""
		mock_frappe = MagicMock()
		mock_frappe.session.user = "Guest"
		mock_frappe.PermissionError = Exception
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))
		mock_frappe.throw.side_effect = Exception("PermissionError: Guest user")
		mock_sessions = MagicMock()

		with patch.dict(sys.modules, {"frappe": mock_frappe, "frappe.sessions": mock_sessions}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token
			with self.assertRaises(Exception) as ctx:
				get_auth_token()
			self.assertIn("Guest", str(ctx.exception))

	def test_frappe_api_authenticated_user_identity(self):
		"""Frappe API derives identity strictly from frappe.session.user and includes encrypted ctx."""
		from cryptography.fernet import Fernet
		mock_frappe = MagicMock()
		mock_sessions = MagicMock()
		mock_sessions.get_csrf_token.return_value = "mock_valid_csrf_token"
		mock_frappe.session.user = "jane@example.com"
		mock_frappe.session.sid = "mock_valid_session_sid"
		test_fernet_key = Fernet.generate_key().decode()

		def mock_conf_get(key):
			if key == "credential_enc_key":
				return test_fernet_key
			return TEST_SECRET

		mock_frappe.conf.get.side_effect = mock_conf_get
		mock_frappe.utils.get_fullname.return_value = "Jane Doe"
		mock_frappe.get_roles.return_value = ["Accounts User"]
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))

		with patch.dict(sys.modules, {"frappe": mock_frappe, "frappe.sessions": mock_sessions}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token
			res = get_auth_token()
			self.assertEqual(res["user"], "jane@example.com")
			self.assertEqual(res["full_name"], "Jane Doe")
			self.assertEqual(res["roles"], ["Accounts User"])

			# Verify signed token
			decoded = jwt.decode(res["token"], TEST_SECRET, algorithms=["HS256"])
			self.assertEqual(decoded["sub"], "jane@example.com")
			self.assertEqual(decoded["full_name"], "Jane Doe")
			self.assertIn("ctx", decoded)

			# Verify decrypted ctx
			f = Fernet(test_fernet_key.encode())
			decrypted = json.loads(f.decrypt(decoded["ctx"].encode()).decode())
			self.assertEqual(decrypted["sid"], "mock_valid_session_sid")
			self.assertEqual(decrypted["csrf_token"], "mock_valid_csrf_token")

	def test_frappe_api_empty_sid_or_csrf_fails_closed(self):
		"""Frappe API must raise AuthenticationError when sid or csrf token is missing."""
		from cryptography.fernet import Fernet
		mock_frappe = MagicMock()
		mock_sessions = MagicMock()
		mock_frappe.session.user = "jane@example.com"
		mock_frappe.session.sid = None
		mock_frappe.local.session.sid = None
		mock_sessions.get_csrf_token.return_value = "csrf_token"
		mock_frappe.AuthenticationError = type("AuthenticationError", (Exception,), {})
		mock_frappe.throw.side_effect = lambda msg, exc: (_ for _ in ()).throw(exc(msg))
		test_fernet_key = Fernet.generate_key().decode()

		mock_frappe.conf.get.side_effect = lambda k: test_fernet_key if k == "credential_enc_key" else TEST_SECRET
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))

		with patch.dict(sys.modules, {"frappe": mock_frappe, "frappe.sessions": mock_sessions}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token
			with self.assertRaises(mock_frappe.AuthenticationError) as ctx:
				get_auth_token()
			self.assertIn("Missing active session sid or CSRF token", str(ctx.exception))

	def test_frappe_api_malformed_credential_enc_key_fails_closed(self):
		"""Frappe API must raise ValidationError when CREDENTIAL_ENC_KEY is not a valid Fernet key."""
		mock_frappe = MagicMock()
		mock_sessions = MagicMock()
		mock_frappe.session.user = "jane@example.com"
		mock_frappe.session.sid = "sid"
		mock_sessions.get_csrf_token.return_value = "csrf_token"
		mock_frappe.ValidationError = type("ValidationError", (Exception,), {})
		mock_frappe.throw.side_effect = lambda msg, exc: (_ for _ in ()).throw(exc(msg))

		mock_frappe.conf.get.side_effect = lambda k: "malformed_not_base64_key" if k == "credential_enc_key" else TEST_SECRET
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))

		with patch.dict(sys.modules, {"frappe": mock_frappe, "frappe.sessions": mock_sessions}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token
			with self.assertRaises(mock_frappe.ValidationError) as ctx:
				get_auth_token()
			self.assertIn("Invalid CREDENTIAL_ENC_KEY", str(ctx.exception))

	def test_frappe_api_get_readonly_auth_token_minting(self):
		"""get_readonly_auth_token mints scope='read' and shorter TTL, while get_auth_token output is unchanged."""
		from cryptography.fernet import Fernet
		mock_frappe = MagicMock()
		mock_sessions = MagicMock()
		mock_sessions.get_csrf_token.return_value = "mock_valid_csrf"
		mock_frappe.session.user = "jane@example.com"
		mock_frappe.session.sid = "mock_valid_sid"
		test_fernet_key = Fernet.generate_key().decode()

		mock_frappe.conf.get.side_effect = lambda k: test_fernet_key if k == "credential_enc_key" else TEST_SECRET
		mock_frappe.utils.get_fullname.return_value = "Jane Doe"
		mock_frappe.get_roles.return_value = ["Accounts User"]
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))

		with patch.dict(sys.modules, {"frappe": mock_frappe, "frappe.sessions": mock_sessions}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token, get_readonly_auth_token

			# 1. get_auth_token output remains unchanged
			normal_res = get_auth_token()
			self.assertEqual(normal_res["expires_in_seconds"], 3600)
			self.assertNotIn("scope", normal_res)
			normal_payload = jwt.decode(normal_res["token"], TEST_SECRET, algorithms=["HS256"])
			self.assertNotIn("scope", normal_payload)
			self.assertEqual(normal_payload["exp"] - normal_payload["iat"], 3600)

			# 2. get_readonly_auth_token mints with scope="read" and 15 min exp
			ro_res = get_readonly_auth_token()
			self.assertEqual(ro_res["expires_in_seconds"], 900)
			self.assertEqual(ro_res["scope"], "read")
			ro_payload = jwt.decode(ro_res["token"], TEST_SECRET, algorithms=["HS256"])
			self.assertEqual(ro_payload["scope"], "read")
			self.assertEqual(ro_payload["exp"] - ro_payload["iat"], 900)

			# Verify encrypted ctx is present and valid
			f = Fernet(test_fernet_key.encode())
			decrypted = json.loads(f.decrypt(ro_payload["ctx"].encode()).decode())
			self.assertEqual(decrypted["sid"], "mock_valid_sid")
			self.assertEqual(decrypted["csrf_token"], "mock_valid_csrf")

if __name__ == "__main__":
	unittest.main()

