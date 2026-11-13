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
    assert observed_context.credentials is None
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

    # With valid token -> authenticated
    with patch("main.get_list", return_value={"status": "success", "data": []}):
        resp_authed = client.post(
            "/api/tools/list",
            json={"doctype_name": "Customer"},
            headers=valid_token_header,
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
