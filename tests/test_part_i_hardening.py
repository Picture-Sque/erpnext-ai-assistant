import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from mcp_erpnext.auth import (
    RequestContext,
    set_request_context,
    get_request_context,
    is_session_expired,
    mark_session_expired,
    reset_session_expired,
)
from mcp_erpnext.client import ERPNextClient, Credentials
from mcp_erpnext.errors import ERPNextConfigError
from mcp_erpnext.defaults import _get_cache_key, reset_resolver_caches
from agent.main import app, is_dev_mock_allowed


def test_ctx_with_credentials_none_and_not_dev_mock_never_reaches_env(monkeypatch):
    """If a context exists with credentials None and dev_mock False, raise ERPNextConfigError."""
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_service_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_service_secret")

    ctx = RequestContext(user_id="alice@example.com", credentials=None, dev_mock=False)
    with set_request_context(ctx):
        client = ERPNextClient()
        with pytest.raises(ERPNextConfigError) as exc_info:
            client.get_credentials()
        assert "No per-user credentials available in request context for user 'alice@example.com'" in str(exc_info.value)


def test_ctx_with_credentials_none_and_dev_mock_true_falls_back_to_env(monkeypatch):
    """If a context exists with credentials None and dev_mock True, allow fallback to env credentials."""
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_dev_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_dev_secret")

    ctx = RequestContext(user_id="dev_user@example.com", credentials=None, dev_mock=True)
    with set_request_context(ctx):
        client = ERPNextClient()
        creds = client.get_credentials()
        assert creds.api_key == "env_dev_key"
        assert creds.api_secret == "env_dev_secret"


def test_no_context_strict_identity_true_raises_config_error(monkeypatch):
    """When outside context and ERPNEXT_STRICT_IDENTITY is true, raise ERPNextConfigError."""
    monkeypatch.setenv("ERPNEXT_STRICT_IDENTITY", "true")
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_service_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_service_secret")

    assert get_request_context() is None
    client = ERPNextClient()
    with pytest.raises(ERPNextConfigError) as exc_info:
        client.get_credentials()
    assert "No request context is active and ERPNEXT_STRICT_IDENTITY is enabled" in str(exc_info.value)


def test_no_context_strict_identity_false_falls_back_to_env(monkeypatch):
    """When outside context and ERPNEXT_STRICT_IDENTITY is false, fall back to env credentials."""
    monkeypatch.setenv("ERPNEXT_STRICT_IDENTITY", "false")
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_service_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_service_secret")

    assert get_request_context() is None
    client = ERPNextClient()
    creds = client.get_credentials()
    assert creds.api_key == "env_service_key"
    assert creds.api_secret == "env_service_secret"


def test_session_expired_sets_flag_and_chat_returns_401(monkeypatch):
    """ERPNext returning session_expired or 401 sets the flag and causes /chat to return 401."""
    import jwt
    from cryptography.fernet import Fernet
    import json

    jwt_secret = "test_jwt_secret_32bytes_long_exact!"
    enc_key = Fernet.generate_key().decode()
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    monkeypatch.setenv("CREDENTIAL_ENC_KEY", enc_key)

    f = Fernet(enc_key.encode())
    enc_ctx = f.encrypt(json.dumps({"sid": "expired_sid_123"}).encode()).decode()

    token = jwt.encode(
        {"sub": "alice@example.com", "roles": ["Desk User"], "ctx": enc_ctx},
        jwt_secret,
        algorithm="HS256",
    )

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate compiled_graph.invoke encountering session_expired in ERPNextClient
    with patch("agent.main.compiled_graph.invoke") as mock_invoke:
        def side_effect(*args, **kwargs):
            # Simulate ERPNextClient getting a 403 with session_expired: 1
            mark_session_expired()
            return {"final_response": "Graph finished", "detected_intent": "greeting"}

        mock_invoke.side_effect = side_effect
        resp = client.post("/chat", json={"message": "hello"}, headers=headers)
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"] == "Bearer"
        assert "expired" in resp.json()["detail"].lower()


def test_two_different_session_credentials_never_share_defaults_cache():
    """Two user session credentials produce distinct cache keys in defaults cache."""
    reset_resolver_caches()
    creds_a = Credentials(base_url="http://localhost:8080", auth_headers={"Cookie": "sid=user_a_session_1"})
    creds_b = Credentials(base_url="http://localhost:8080", auth_headers={"Cookie": "sid=user_b_session_2"})

    client_a = ERPNextClient(credentials=creds_a)
    client_b = ERPNextClient(credentials=creds_b)

    key_a = _get_cache_key(client_a)
    key_b = _get_cache_key(client_b)

    assert key_a != key_b
    assert key_a[0] == key_b[0]  # same base_url
    assert key_a[1] != key_b[1]  # distinct HMAC hashes


def test_dev_mock_refusal_on_non_local_base_url(monkeypatch):
    """Dev-mock auth is refused if ERPNEXT_BASE_URL host is not localhost or 127.0.0.1."""
    monkeypatch.setenv("ALLOW_DEV_MOCK_AUTH", "true")

    # 1. Non-local hostname -> refused
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://erpnext.mycompany.internal:8080")
    assert is_dev_mock_allowed() is False

    # 2. Localhost -> allowed
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    assert is_dev_mock_allowed() is True

    # 3. 127.0.0.1 -> allowed
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://127.0.0.1:8080")
    assert is_dev_mock_allowed() is True

    # 4. ALLOW_DEV_MOCK_AUTH false -> always False
    monkeypatch.setenv("ALLOW_DEV_MOCK_AUTH", "false")
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8080")
    assert is_dev_mock_allowed() is False
