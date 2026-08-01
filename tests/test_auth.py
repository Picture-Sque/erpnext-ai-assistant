import os
import sys
import unittest
import asyncio
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

# Add parent directory to path to import main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app, verify_token, JWT_SECRET, JWT_ALGORITHM

class TestAuth(unittest.TestCase):
	def setUp(self):
		self.secret = JWT_SECRET
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

if __name__ == "__main__":
	unittest.main()
