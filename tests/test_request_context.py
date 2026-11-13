import asyncio
import threading
import pytest
from dataclasses import FrozenInstanceError

from mcp_erpnext.auth import (
    RequestContext,
    get_request_context,
    set_request_context,
    current_request_context,
)
from mcp_erpnext.client import Credentials, ERPNextClient


def test_default_context_is_none():
    assert get_request_context() is None
    assert current_request_context.get() is None


def test_request_context_creation_and_defaults():
    ctx = RequestContext(user_id="user@example.com")
    assert ctx.user_id == "user@example.com"
    assert ctx.roles == ()
    assert ctx.credentials is None
    assert ctx.read_only is True
    assert ctx.request_id is None


def test_request_context_roles_normalization():
    ctx = RequestContext(user_id="user@example.com", roles=["System Manager", 123])
    assert ctx.roles == ("System Manager", "123")
    assert isinstance(ctx.roles, tuple)


def test_request_context_frozen_immutability():
    ctx = RequestContext(user_id="user@example.com")
    with pytest.raises(FrozenInstanceError):
        ctx.user_id = "other@example.com"


def test_request_context_repr_never_reveals_credentials():
    creds = Credentials(
        base_url="http://localhost:8000",
        api_key="super_secret_key",
        api_secret="super_secret_secret",
        auth_headers={"Authorization": "Bearer sensitive_token"},
    )
    ctx = RequestContext(
        user_id="user@example.com",
        roles=("Desk User",),
        credentials=creds,
        read_only=True,
        request_id="req-123",
    )
    repr_str = repr(ctx)
    assert "super_secret_key" not in repr_str
    assert "super_secret_secret" not in repr_str
    assert "sensitive_token" not in repr_str
    assert "credentials='[REDACTED]'" in repr_str
    assert "user@example.com" in repr_str


def test_set_request_context_and_reset():
    ctx = RequestContext(user_id="alice@example.com", roles=("Sales User",))
    assert get_request_context() is None

    with set_request_context(ctx) as active_ctx:
        assert active_ctx == ctx
        assert get_request_context() == ctx

    assert get_request_context() is None


def test_nested_contexts():
    ctx1 = RequestContext(user_id="alice@example.com", request_id="req-1")
    ctx2 = RequestContext(user_id="bob@example.com", request_id="req-2")

    with set_request_context(ctx1):
        assert get_request_context() == ctx1
        with set_request_context(ctx2):
            assert get_request_context() == ctx2
        assert get_request_context() == ctx1

    assert get_request_context() is None


def test_reset_after_exception():
    ctx = RequestContext(user_id="alice@example.com")
    with pytest.raises(ValueError, match="boom"):
        with set_request_context(ctx):
            assert get_request_context() == ctx
            raise ValueError("boom")

    assert get_request_context() is None


def test_thread_isolation():
    results = {}

    def worker(user_id: str, barrier: threading.Barrier):
        ctx = RequestContext(user_id=user_id)
        with set_request_context(ctx):
            barrier.wait()
            results[user_id] = get_request_context()
        results[f"{user_id}_after"] = get_request_context()

    barrier = threading.Barrier(2)
    t1 = threading.Thread(target=worker, args=("worker1", barrier))
    t2 = threading.Thread(target=worker, args=("worker2", barrier))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["worker1"].user_id == "worker1"
    assert results["worker2"].user_id == "worker2"
    assert results["worker1_after"] is None
    assert results["worker2_after"] is None


def test_asyncio_task_isolation():
    results = {}

    async def async_worker(user_id: str, delay: float):
        ctx = RequestContext(user_id=user_id)
        with set_request_context(ctx):
            await asyncio.sleep(delay)
            results[user_id] = get_request_context()
        results[f"{user_id}_after"] = get_request_context()

    async def main():
        await asyncio.gather(
            async_worker("task1", 0.02),
            async_worker("task2", 0.01),
        )

    asyncio.run(main())

    assert results["task1"].user_id == "task1"
    assert results["task2"].user_id == "task2"
    assert results["task1_after"] is None
    assert results["task2_after"] is None


def test_client_credentials_fallback_when_context_is_none(monkeypatch):
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_secret")

    client = ERPNextClient()
    creds = client.get_credentials()
    assert creds.api_key == "env_key"
    assert creds.api_secret == "env_secret"


def test_client_credentials_fallback_when_context_credentials_none(monkeypatch):
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_secret")

    ctx = RequestContext(user_id="user@example.com", credentials=None)
    with set_request_context(ctx):
        client = ERPNextClient()
        creds = client.get_credentials()
        assert creds.api_key == "env_key"
        assert creds.api_secret == "env_secret"


def test_client_credentials_precedence(monkeypatch):
    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_secret")

    ctx_creds = Credentials(base_url="http://ctx-host:8000", api_key="ctx_key", api_secret="ctx_sec")
    explicit_creds = Credentials(base_url="http://exp-host:8000", api_key="exp_key", api_secret="exp_sec")

    ctx = RequestContext(user_id="user@example.com", credentials=ctx_creds)

    # Context credentials win over env
    with set_request_context(ctx):
        client = ERPNextClient()
        creds = client.get_credentials()
        assert creds == ctx_creds
        assert creds.api_key == "ctx_key"

        # Explicit constructor credentials win over context
        explicit_client = ERPNextClient(credentials=explicit_creds)
        assert explicit_client.get_credentials() == explicit_creds
        assert explicit_client.get_credentials().api_key == "exp_key"

    # Outside context, env wins
    client = ERPNextClient()
    assert client.get_credentials().api_key == "env_key"


def test_resolver_cache_keyed_per_credential(monkeypatch):
    from unittest.mock import MagicMock, patch
    from mcp_erpnext.defaults import get_default_company, reset_resolver_caches

    monkeypatch.setenv("ERPNEXT_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("ERPNEXT_API_KEY", "env_key")
    monkeypatch.setenv("ERPNEXT_API_SECRET", "env_secret")
    monkeypatch.delenv("DEFAULT_COMPANY", raising=False)

    reset_resolver_caches()

    creds_a = Credentials(base_url="http://localhost:8000", api_key="user_a_key", api_secret="user_a_sec")
    creds_b = Credentials(base_url="http://localhost:8000", api_key="user_b_key", api_secret="user_b_sec")

    ctx_a = RequestContext(user_id="user_a@example.com", credentials=creds_a)
    ctx_b = RequestContext(user_id="user_b@example.com", credentials=creds_b)

    with patch("mcp_erpnext.client.httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http

        # Return Company A for request A, Company B for request B
        resp_a = MagicMock()
        resp_a.status_code = 200
        resp_a.is_success = True
        resp_a.json.return_value = {"data": [{"name": "Company A"}]}

        resp_b = MagicMock()
        resp_b.status_code = 200
        resp_b.is_success = True
        resp_b.json.return_value = {"data": [{"name": "Company B"}]}

        mock_http.get.side_effect = [resp_a, resp_b]

        # First call with user A context
        with set_request_context(ctx_a):
            comp_a = get_default_company()
            assert comp_a == "Company A"

            # Cached for user A
            comp_a_again = get_default_company()
            assert comp_a_again == "Company A"

        # mock_http.get should have been called only once for user A
        assert mock_http.get.call_count == 1

        # Second call with user B context must NOT share user A's cached default
        with set_request_context(ctx_b):
            comp_b = get_default_company()
            assert comp_b == "Company B"

        # mock_http.get must have been called again (call_count = 2)
        assert mock_http.get.call_count == 2

