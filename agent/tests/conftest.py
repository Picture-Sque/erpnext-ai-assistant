import os
import json
import tempfile
import pytest
import jwt
from cryptography.fernet import Fernet

# Tests must never touch the developer's real audit database. Same path as tests/conftest.py.
os.environ["SQLITE_AUDIT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "erpnext_assistant_test_audit.db")

TEST_CREDENTIAL_ENC_KEY = Fernet.generate_key().decode()

@pytest.fixture(autouse=True)
def setup_auth_environment(monkeypatch):
    """
    Shared test fixture ensuring CREDENTIAL_ENC_KEY is configured and valid,
    and transparently supplying a valid encrypted 'ctx' claim whenever a test
    calls jwt.encode() with a user payload without 'ctx'.
    This satisfies the fail-closed requirement in main.py without modifying
    test files owned by other members.
    """
    key = os.getenv("CREDENTIAL_ENC_KEY")
    if not key:
        key = TEST_CREDENTIAL_ENC_KEY
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", key)

    try:
        fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception:
        fernet = Fernet(TEST_CREDENTIAL_ENC_KEY.encode("utf-8"))
        monkeypatch.setenv("CREDENTIAL_ENC_KEY", TEST_CREDENTIAL_ENC_KEY)

    orig_encode = jwt.encode

    def wrapped_encode(payload, *args, **kwargs):
        if isinstance(payload, dict) and "sub" in payload and "ctx" not in payload:
            ctx_payload = json.dumps({"sid": "test_conftest_sid", "csrf_token": "test_conftest_csrf"})
            encrypted_ctx = fernet.encrypt(ctx_payload.encode("utf-8")).decode("utf-8")
            payload = dict(payload)
            payload["ctx"] = encrypted_ctx
        return orig_encode(payload, *args, **kwargs)

    monkeypatch.setattr(jwt, "encode", wrapped_encode)

    # Unit tests in agent/tests should not make unmocked live network calls to ERPNext
    from mcp_erpnext.client import ERPNextClient
    from mcp_erpnext.errors import ERPNextTransportError

    def no_live_calls(self, *args, **kwargs):
        raise ERPNextTransportError("Live ERPNext transport calls are disabled in agent/tests suite")

    monkeypatch.setattr(ERPNextClient, "_send", no_live_calls)

    yield
