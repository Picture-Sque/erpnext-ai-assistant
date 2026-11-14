import os
import jwt
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app, verify_token
from mcp_erpnext.auth import get_request_context, RequestContext


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def valid_token_header(monkeypatch):
    secret = "test_jwt_secret_key_32_bytes_long!!"
    monkeypatch.setenv("JWT_SECRET", secret)
    payload = {
        "sub": "user@example.com",
        "full_name": "Test User",
        "roles": ["Sales User", "Desk User"],
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_contextvar_propagates_into_graph_and_resets_after_request(client, valid_token_header):
    """
    PROVE that RequestContext is visible inside the graph invocation path
    and is properly reset to None after the request completes.
    """
    observed_context = None

    def fake_graph_invoke(state, config=None):
        nonlocal observed_context
        # Read the active RequestContext inside the graph execution path
        observed_context = get_request_context()
        return {
            "final_response": "Graph executed successfully",
            "detected_intent": "greeting",
            "collected_fields": {},
            "is_complete": True,
        }

    # Context should be None before request
    assert get_request_context() is None

    with patch("main.compiled_graph.invoke", side_effect=fake_graph_invoke):
        resp = client.post(
            "/chat",
            json={"message": "hello"},
            headers=valid_token_header,
        )
        assert resp.status_code == 200
        assert resp.json()["response"] == "Graph executed successfully"

    # Verify context was captured inside the graph invocation
    assert observed_context is not None
    assert observed_context.user_id == "user@example.com"
    assert observed_context.roles == ("Sales User", "Desk User")
    assert observed_context.credentials is not None
    assert "Cookie" in observed_context.credentials.get_headers()
    assert observed_context.read_only is False
    assert observed_context.request_id.startswith("req-")

    # Verify context is completely reset after the request exits
    assert get_request_context() is None


def test_cors_allowed_and_disallowed_origins(client):
    # Allowed origin gets access-control-allow-origin
    resp = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8080"

    # Disallowed origin does NOT get access-control-allow-origin
    resp_evil = client.options(
        "/chat",
        headers={
            "Origin": "http://evil-attacker-site.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp_evil.headers.get("access-control-allow-origin") is None


def test_tools_endpoints_require_authentication(client, valid_token_header):
    # Missing token -> 401
    resp = client.post("/api/tools/list", json={"doctype_name": "Customer"})
    assert resp.status_code == 401

    resp_add = client.post("/api/tools/add", json={"doctype_name": "Customer"})
    assert resp_add.status_code == 401

    resp_update = client.put("/api/tools/update", json={"doctype_name": "Customer", "id": "1"})
    assert resp_update.status_code == 401

    resp_del = client.request("DELETE", "/api/tools/delete", json={"doctype_name": "Customer", "id": "1"})
    assert resp_del.status_code == 401

    # With valid token possessing allowed role (System Manager) -> authenticated
    secret = os.getenv("JWT_SECRET", "test_jwt_secret_key_32_bytes_long!!")
    sys_token = jwt.encode({"sub": "admin@example.com", "roles": ["System Manager"]}, secret, algorithm="HS256")
    with patch("main.get_list", return_value={"status": "success", "data": []}):
        resp_authed = client.post(
            "/api/tools/list",
            json={"doctype_name": "Customer"},
            headers={"Authorization": f"Bearer {sys_token}"},
        )
        assert resp_authed.status_code == 200


def test_malformed_auth_header_does_not_log_token_secret(client, caplog):
    sensitive_token = "secret_token_1234567890_sensitive"
    with caplog.at_level("WARNING"):
        resp = client.post(
            "/chat",
            json={"message": "hello"},
            headers={"Authorization": f"Basic {sensitive_token}"},
        )
    assert resp.status_code == 401
    assert "Malformed Authorization header format" in caplog.text
    assert sensitive_token not in caplog.text


def test_dev_mock_auth_never_returned_when_invalid_token_is_supplied(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_MOCK_AUTH", "true")
    monkeypatch.setenv("JWT_SECRET", "test_secret_32_chars_minimum_here!!")

    # 1. No token supplied -> mock dev user returned
    user_no_token = asyncio.run(verify_token(authorization=None))
    assert user_no_token["sub"] == "dev_user@example.com"

    # 2. Invalid token supplied -> 401 HTTPException raised, NOT dev user
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_token(authorization="Bearer this_is_a_completely_invalid_token"))
    assert exc_info.value.status_code == 401
    assert "Invalid token" in exc_info.value.detail


def test_encrypted_ctx_claim_populates_credentials_and_redacts(monkeypatch):
    import json
    from cryptography.fernet import Fernet
    from main import _build_request_context

    enc_key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", enc_key)

    sid_secret = "sensitive_sid_secret_value_12345"
    csrf_secret = "sensitive_csrf_secret_value_abcde"

    fernet = Fernet(enc_key.encode())
    encrypted_ctx = fernet.encrypt(json.dumps({"sid": sid_secret, "csrf_token": csrf_secret}).encode()).decode()

    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "ctx": encrypted_ctx,
    }

    ctx = _build_request_context(payload)
    assert ctx.user_id == "alice@example.com"
    assert ctx.credentials is not None

    headers = ctx.credentials.get_headers()
    assert headers["Cookie"] == f"sid={sid_secret}"
    assert headers["X-Frappe-CSRF-Token"] == csrf_secret

    # Verify complete redaction in repr and str
    assert sid_secret not in repr(ctx)
    assert csrf_secret not in repr(ctx)
    assert sid_secret not in repr(ctx.credentials)
    assert csrf_secret not in repr(ctx.credentials)
    assert sid_secret not in str(ctx)
    assert csrf_secret not in str(ctx)


def test_missing_ctx_claim_fails_closed_with_401(monkeypatch):
    from cryptography.fernet import Fernet
    from main import _build_request_context

    enc_key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", enc_key)

    payload_no_ctx = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_request_context(payload_no_ctx)
    assert exc_info.value.status_code == 401
    assert "claim required" in exc_info.value.detail


def test_tampered_ctx_claim_fails_closed_with_401(monkeypatch):
    from cryptography.fernet import Fernet
    from main import _build_request_context

    enc_key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", enc_key)

    payload_tampered = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "ctx": "gAAAAABl_tampered_ciphertext_that_is_invalid",
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_request_context(payload_tampered)
    assert exc_info.value.status_code == 401
    assert "Invalid, tampered, or undecryptable" in exc_info.value.detail


def test_wrong_fernet_key_fails_closed_with_401(monkeypatch):
    import json
    from cryptography.fernet import Fernet
    from main import _build_request_context

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", key_b)

    # Encrypted with Key A, agent configured with Key B
    fernet_a = Fernet(key_a.encode())
    encrypted_ctx_a = fernet_a.encrypt(json.dumps({"sid": "sid_val", "csrf_token": "csrf_val"}).encode()).decode()

    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "ctx": encrypted_ctx_a,
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_request_context(payload)
    assert exc_info.value.status_code == 401
    assert "Invalid, tampered, or undecryptable" in exc_info.value.detail


def test_key_unset_fails_closed_with_401(monkeypatch):
    from main import _build_request_context

    monkeypatch.delenv("CREDENTIAL_ENC_KEY", raising=False)
    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_request_context(payload)
    assert exc_info.value.status_code == 401
    assert "CREDENTIAL_ENC_KEY is unconfigured" in exc_info.value.detail


def test_malformed_credential_enc_key_fails_closed_with_401(monkeypatch):
    from main import _build_request_context

    monkeypatch.setenv("CREDENTIAL_ENC_KEY", "not_a_valid_fernet_key_at_all!")
    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "ctx": "gAAAAABl_some_ciphertext",
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_request_context(payload)
    assert exc_info.value.status_code == 401
    assert "Invalid CREDENTIAL_ENC_KEY" in exc_info.value.detail


def test_dev_mock_auth_bypasses_credential_enc_key_when_explicitly_enabled(monkeypatch):
    from main import _build_request_context

    monkeypatch.delenv("CREDENTIAL_ENC_KEY", raising=False)
    monkeypatch.setenv("ALLOW_DEV_MOCK_AUTH", "true")

    payload = {
        "sub": "dev_user@example.com",
        "full_name": "Standalone Developer",
        "roles": ["System Manager", "Developer"],
        "is_dev_mock": True,
    }

    ctx = _build_request_context(payload)
    assert ctx.user_id == "dev_user@example.com"
    assert ctx.credentials is None


def _make_valid_ctx(enc_key: str) -> str:
    from cryptography.fernet import Fernet
    import json
    f = Fernet(enc_key.encode())
    return f.encrypt(json.dumps({"sid": "sid_123", "csrf_token": "csrf_123"}).encode()).decode()


def test_scope_absent_sets_read_only_false(monkeypatch):
    from cryptography.fernet import Fernet
    from main import _build_request_context

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", key)
    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "ctx": _make_valid_ctx(key),
    }
    ctx = _build_request_context(payload)
    assert ctx.read_only is False


def test_scope_read_sets_read_only_true(monkeypatch):
    from cryptography.fernet import Fernet
    from main import _build_request_context

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", key)
    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "scope": "read",
        "ctx": _make_valid_ctx(key),
    }
    ctx = _build_request_context(payload)
    assert ctx.read_only is True


@pytest.mark.parametrize("invalid_scope", ["banana", ["read"], None, 123, {"scope": "read"}])
def test_scope_invalid_values_and_types_fail_closed_to_read_only_true(monkeypatch, invalid_scope):
    from cryptography.fernet import Fernet
    from main import _build_request_context

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", key)
    payload = {
        "sub": "alice@example.com",
        "roles": ["Sales User"],
        "scope": invalid_scope,
        "ctx": _make_valid_ctx(key),
    }
    ctx = _build_request_context(payload)
    assert ctx.read_only is True


def test_tools_api_blocks_read_only_token_with_403(client, monkeypatch):
    from cryptography.fernet import Fernet
    import jwt
    from unittest.mock import patch

    secret = os.getenv("JWT_SECRET", "test_jwt_secret_key_32_bytes_long!!")
    monkeypatch.setenv("JWT_SECRET", secret)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", key)

    payload = {
        "sub": "admin@example.com",
        "roles": ["System Manager"],
        "scope": "read",
        "ctx": _make_valid_ctx(key),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Allowed read operation (POST /api/tools/list) still proceeds
    with patch("main.get_list", return_value={"status": "success", "data": []}):
        resp_list = client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=headers)
        assert resp_list.status_code == 200

    # 2. Mutating endpoints are blocked with 403
    resp_add = client.post("/api/tools/add", json={"doctype_name": "Customer", "parameters": {}}, headers=headers)
    assert resp_add.status_code == 403
    assert "this session is read-only" in resp_add.json()["detail"]

    resp_update = client.put("/api/tools/update", json={"doctype_name": "Customer", "id": "1", "parameters": {}}, headers=headers)
    assert resp_update.status_code == 403
    assert "this session is read-only" in resp_update.json()["detail"]

    resp_del = client.request("DELETE", "/api/tools/delete", json={"doctype_name": "Customer", "id": "1"}, headers=headers)
    assert resp_del.status_code == 403
    assert "this session is read-only" in resp_del.json()["detail"]
