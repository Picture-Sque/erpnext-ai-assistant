import os
import jwt
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


SECRET = "test_jwt_secret_key_32_bytes_long!!"


def _make_auth_header(roles, sub="user@example.com"):
    token = jwt.encode({"sub": sub, "roles": roles}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("ALLOW_DEV_MOCK_AUTH", "false")


def test_no_token_returns_401_or_403(client):
    assert client.post("/api/tools/list", json={"doctype_name": "Customer"}).status_code in (401, 403)
    assert client.post("/api/tools/add", json={"doctype_name": "Customer"}).status_code in (401, 403)
    assert client.put("/api/tools/update", json={"doctype_name": "Customer", "id": "1"}).status_code in (401, 403)
    assert client.request("DELETE", "/api/tools/delete", json={"doctype_name": "Customer", "id": "1"}).status_code in (401, 403)


def test_sales_user_gets_403_on_all_four_endpoints(client):
    headers = _make_auth_header(["Sales User"])
    endpoints = [
        ("POST", "/api/tools/list", {"doctype_name": "Customer"}),
        ("POST", "/api/tools/add", {"doctype_name": "Customer", "parameters": {}}),
        ("PUT", "/api/tools/update", {"doctype_name": "Customer", "id": "1"}),
        ("DELETE", "/api/tools/delete", {"doctype_name": "Customer", "id": "1"}),
    ]
    for method, path, payload in endpoints:
        resp = client.request(method, path, json=payload, headers=headers)
        assert resp.status_code == 403, f"{method} {path} expected 403, got {resp.status_code}"
        assert "Access denied" in resp.json()["detail"]


def test_system_manager_passes_role_gate(client):
    headers = _make_auth_header(["System Manager"])
    with patch("main.get_list", return_value={"status": "success", "data": []}), \
         patch("main.create_document", return_value={"status": "success", "data": {}}), \
         patch("main.update_document", return_value={"status": "success", "data": {}}), \
         patch("main.delete_document", return_value={"status": "success", "data": {}}):
        assert client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=headers).status_code == 200
        assert client.post("/api/tools/add", json={"doctype_name": "Customer"}, headers=headers).status_code == 200
        assert client.put("/api/tools/update", json={"doctype_name": "Customer", "id": "1"}, headers=headers).status_code == 200
        assert client.request("DELETE", "/api/tools/delete", json={"doctype_name": "Customer", "id": "1"}, headers=headers).status_code == 200


def test_env_override_allows_custom_roles(client, monkeypatch):
    monkeypatch.setenv("TOOLS_API_ALLOWED_ROLES", "Sales User, Custom Auditor")
    headers = _make_auth_header(["Sales User"])
    with patch("main.get_list", return_value={"status": "success", "data": []}):
        resp = client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=headers)
        assert resp.status_code == 200

    # User with non-allowed role is still rejected with 403
    other_headers = _make_auth_header(["HR User"])
    resp = client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=other_headers)
    assert resp.status_code == 403


def test_empty_env_override_fails_closed_to_system_manager_only(client, monkeypatch):
    monkeypatch.setenv("TOOLS_API_ALLOWED_ROLES", "   ")
    sales_headers = _make_auth_header(["Sales User"])
    resp = client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=sales_headers)
    assert resp.status_code == 403

    sys_headers = _make_auth_header(["System Manager"])
    with patch("main.get_list", return_value={"status": "success", "data": []}):
        resp = client.post("/api/tools/list", json={"doctype_name": "Customer"}, headers=sys_headers)
        assert resp.status_code == 200
