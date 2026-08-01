import os
import sys
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

from main import app, verify_token, JWT_ALGORITHM, get_candidate_secrets

class TestAuth(unittest.TestCase):
	def setUp(self):
		self.secret = TEST_SECRET
		self.algorithm = JWT_ALGORITHM

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
		with patch.dict(os.environ, {}, clear=True), patch("main.get_candidate_secrets", return_value=[]):
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

		with patch.dict(sys.modules, {"frappe": mock_frappe}):
			if "ai_assistant.ai_assistant.api" in sys.modules:
				del sys.modules["ai_assistant.ai_assistant.api"]
			from ai_assistant.ai_assistant.api import get_auth_token
			with self.assertRaises(Exception) as ctx:
				get_auth_token()
			self.assertIn("Guest", str(ctx.exception))

	def test_frappe_api_authenticated_user_identity(self):
		"""Frappe API derives identity strictly from frappe.session.user."""
		mock_frappe = MagicMock()
		mock_frappe.session.user = "jane@example.com"
		mock_frappe.conf.get.return_value = TEST_SECRET
		mock_frappe.utils.get_fullname.return_value = "Jane Doe"
		mock_frappe.get_roles.return_value = ["Accounts User"]
		mock_frappe.whitelist.side_effect = lambda *a, **kw: (a[0] if (len(a) > 0 and callable(a[0])) else (lambda fn: fn))

		with patch.dict(sys.modules, {"frappe": mock_frappe}):
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

if __name__ == "__main__":
	unittest.main()

