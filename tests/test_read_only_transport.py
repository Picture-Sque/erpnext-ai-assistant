import pytest
from unittest.mock import patch, MagicMock
from mcp_erpnext.auth import (
    RequestContext,
    set_request_context,
    require_writable,
    ReadOnlySessionError,
)
from mcp_erpnext.client import ERPNextClient, Credentials, READ_ONLY_POST_ALLOWLIST


def test_require_writable_behavior():
    # 1. No context -> passes
    require_writable()

    # 2. Context with read_only=False -> passes
    ctx_writable = RequestContext(user_id="alice@example.com", read_only=False)
    with set_request_context(ctx_writable):
        require_writable()

    # 3. Context with read_only=True -> raises ReadOnlySessionError
    ctx_readonly = RequestContext(user_id="alice@example.com", read_only=True)
    with set_request_context(ctx_readonly):
        with pytest.raises(ReadOnlySessionError) as exc_info:
            require_writable()
        assert "Operation not permitted: this session is read-only." in str(exc_info.value)

    # 4. Context with non-False value -> raises ReadOnlySessionError (fail closed)
    ctx_invalid = RequestContext(user_id="alice@example.com", read_only="banana")
    with set_request_context(ctx_invalid):
        with pytest.raises(ReadOnlySessionError):
            require_writable()


def test_readonly_transport_blocks_writes_before_httpx_is_called():
    ctx_ro = RequestContext(
        user_id="bob@example.com",
        read_only=True,
        credentials=Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s"),
    )

    client = ERPNextClient()

    with set_request_context(ctx_ro):
        # 1. GET is permitted and calls httpx.Client
        with patch("httpx.Client") as mock_httpx:
            mock_inst = mock_httpx.return_value.__enter__.return_value
            mock_inst.get.return_value = MagicMock(status_code=200, is_success=True)
            client.get("/api/resource/Customer")
            assert mock_httpx.called

        # 2. POST to non-allowlisted endpoint raises before httpx is called
        with patch("httpx.Client") as mock_httpx:
            with pytest.raises(ReadOnlySessionError):
                client.post("/api/resource/Customer", json={"customer_name": "Test"})
            assert not mock_httpx.called, "httpx.Client must NOT be called for mutating POST"

        # 3. PUT raises before httpx is called
        with patch("httpx.Client") as mock_httpx:
            with pytest.raises(ReadOnlySessionError):
                client.put("/api/resource/Customer/Cust-1", json={"customer_name": "New"})
            assert not mock_httpx.called, "httpx.Client must NOT be called for PUT"

        # 4. DELETE raises before httpx is called
        with patch("httpx.Client") as mock_httpx:
            with pytest.raises(ReadOnlySessionError):
                client.delete("/api/resource/Customer/Cust-1")
            assert not mock_httpx.called, "httpx.Client must NOT be called for DELETE"


def test_readonly_transport_allows_allowlisted_post_and_denies_traversals(monkeypatch):
    ctx_ro = RequestContext(
        user_id="bob@example.com",
        read_only=True,
        credentials=Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s"),
    )

    client = ERPNextClient()

    # Monkeypatch the allowlist with a read endpoint
    monkeypatch.setattr(
        "mcp_erpnext.client.READ_ONLY_POST_ALLOWLIST",
        {"/api/method/frappe.client.get_list"},
    )

    with set_request_context(ctx_ro):
        # 1. Exact match to allowlisted path succeeds
        with patch("httpx.Client") as mock_httpx:
            mock_inst = mock_httpx.return_value.__enter__.return_value
            mock_inst.post.return_value = MagicMock(status_code=200, is_success=True)
            client.post("/api/method/frappe.client.get_list", json={"doctype": "Item"})
            assert mock_httpx.called

        # 2. Path with trailing slash normalizes and succeeds
        with patch("httpx.Client") as mock_httpx:
            mock_inst = mock_httpx.return_value.__enter__.return_value
            mock_inst.post.return_value = MagicMock(status_code=200, is_success=True)
            client.post("/api/method/frappe.client.get_list/", json={"doctype": "Item"})
            assert mock_httpx.called

        # 3. Traversal variants are denied before httpx call
        traversal_attempts = [
            "/api/method/frappe.client.get_list//",
            "/api/method/frappe.client.get_list/..",
            "/api/method/../frappe.client.get_list",
            "/api/method/frappe.client%2fget_list",
            "/api/method/frappe.client%2Fget_list",
            "/api/method/frappe.client%2eget_list",
            "/api/method/frappe.client%2Eget_list",
        ]
        for bad_path in traversal_attempts:
            with patch("httpx.Client") as mock_httpx:
                with pytest.raises(ReadOnlySessionError):
                    client.post(bad_path, json={"doctype": "Item"})
                assert not mock_httpx.called, f"httpx.Client must NOT be called for traversal: {bad_path}"


def test_explicit_credentials_client_still_denied_when_context_is_readonly():
    ctx_ro = RequestContext(
        user_id="bob@example.com",
        read_only=True,
    )

    explicit_creds = Credentials(base_url="http://localhost:8080", api_key="admin_k", api_secret="admin_s")
    client = ERPNextClient(credentials=explicit_creds)

    with set_request_context(ctx_ro):
        with patch("httpx.Client") as mock_httpx:
            with pytest.raises(ReadOnlySessionError):
                client.post("/api/resource/Customer", json={})
            assert not mock_httpx.called


def test_writable_context_allows_all_methods():
    ctx_writable = RequestContext(
        user_id="bob@example.com",
        read_only=False,
        credentials=Credentials(base_url="http://localhost:8080", api_key="k", api_secret="s"),
    )

    client = ERPNextClient()

    with set_request_context(ctx_writable):
        with patch("httpx.Client") as mock_httpx:
            mock_inst = mock_httpx.return_value.__enter__.return_value
            mock_inst.get.return_value = MagicMock(status_code=200, is_success=True)
            mock_inst.post.return_value = MagicMock(status_code=200, is_success=True)
            mock_inst.put.return_value = MagicMock(status_code=200, is_success=True)
            mock_inst.delete.return_value = MagicMock(status_code=200, is_success=True)

            client.get("/api/resource/Customer")
            client.post("/api/resource/Customer", json={})
            client.put("/api/resource/Customer/1", json={})
            client.delete("/api/resource/Customer/1")
            assert mock_inst.get.called
            assert mock_inst.post.called
            assert mock_inst.put.called
            assert mock_inst.delete.called
