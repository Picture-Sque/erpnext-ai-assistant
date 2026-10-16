"""
agent/tests/test_conversations_api.py

The chat history endpoints used by the widget sidebar: list, load and delete a
user's conversations. Every endpoint is scoped to the JWT 'sub'.
"""

import os
import sys
import time

import jwt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_store
import main

SECRET = "conversations-api-test-secret-0123456789abcdef"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    with TestClient(main.app) as c:
        yield c


def _auth(user):
    token = jwt.encode({"sub": user, "full_name": user, "roles": ["System Manager"],
                        "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _seed(conversation_id, owner, *messages):
    chat_store.verify_conversation_owner(conversation_id, owner, user_id=owner)
    for role, content in messages:
        chat_store.save_message(conversation_id, role, content)


def test_list_returns_only_own_conversations_newest_first(client):
    _seed("a1", "alice", ("user", "check stock for MS-001"), ("assistant", "42 in Stores"))
    _seed("a2", "alice", ("user", "list purchase orders"))
    _seed("b1", "bob", ("user", "bob's chat"))
    _seed("a-empty", "alice")  # created but never used: not listed

    resp = client.get("/conversations", headers=_auth("alice"))

    assert resp.status_code == 200
    convs = resp.json()["conversations"]
    assert [c["id"] for c in convs] == ["a2", "a1"]
    assert convs[1]["title"] == "check stock for MS-001"
    assert convs[1]["message_count"] == 2


def test_long_first_message_is_shortened_for_the_title(client):
    _seed("a1", "alice", ("user", "create a purchase order " * 10))

    title = client.get("/conversations", headers=_auth("alice")).json()["conversations"][0]["title"]

    assert len(title) <= chat_store.CONVERSATION_TITLE_MAX_CHARS + 1
    assert title.endswith("…")


def test_messages_load_in_order(client):
    _seed("a1", "alice", ("user", "hi"), ("assistant", "hello"), ("user", "check stock"))

    resp = client.get("/conversations/a1/messages", headers=_auth("alice"))

    assert resp.status_code == 200
    assert [(m["role"], m["content"]) for m in resp.json()["messages"]] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "check stock")]


def test_unknown_conversation_has_no_messages(client):
    resp = client.get("/conversations/new-one/messages", headers=_auth("alice"))

    assert resp.status_code == 200
    assert resp.json()["messages"] == []


def test_other_users_conversation_cannot_be_read_or_deleted(client):
    _seed("b1", "bob", ("user", "bob's chat"))

    assert client.get("/conversations/b1/messages", headers=_auth("alice")).status_code == 403
    assert client.delete("/conversations/b1", headers=_auth("alice")).status_code == 403
    assert client.get("/conversations/b1/messages", headers=_auth("bob")).json()["messages"]


def test_delete_removes_conversation_and_state(client):
    _seed("a1", "alice", ("user", "hi"))
    chat_store.update_session_state("a1", detected_intent="check-inventory", extra_state={"x": 1})

    assert client.delete("/conversations/a1", headers=_auth("alice")).status_code == 200

    assert client.get("/conversations", headers=_auth("alice")).json()["conversations"] == []
    assert client.delete("/conversations/a1", headers=_auth("alice")).status_code == 404
    # The id can be reused as a fresh conversation afterwards.
    assert chat_store.load_session_state("a1")["detected_intent"] == ""


def test_endpoints_require_a_token(client):
    assert client.get("/conversations").status_code in (401, 403)
    assert client.get("/conversations/a1/messages").status_code in (401, 403)
    assert client.delete("/conversations/a1").status_code in (401, 403)


def test_rename_sets_title_without_reordering(client):
    _seed("a1", "alice", ("user", "check stock for MS-001"))
    _seed("a2", "alice", ("user", "list purchase orders"))

    resp = client.patch("/conversations/a1", json={"title": "  Steel   stock  "}, headers=_auth("alice"))

    assert resp.status_code == 200
    assert resp.json()["title"] == "Steel stock"
    convs = client.get("/conversations", headers=_auth("alice")).json()["conversations"]
    assert [(c["id"], c["title"]) for c in convs] == [("a2", "list purchase orders"), ("a1", "Steel stock")]


def test_rename_rejects_empty_or_too_long_titles(client):
    _seed("a1", "alice", ("user", "hi"))

    assert client.patch("/conversations/a1", json={"title": "   "}, headers=_auth("alice")).status_code == 422
    too_long = "x" * (chat_store.CUSTOM_TITLE_MAX_CHARS + 1)
    assert client.patch("/conversations/a1", json={"title": too_long}, headers=_auth("alice")).status_code == 422


def test_rename_is_owner_only_and_404_for_unknown(client):
    _seed("b1", "bob", ("user", "bob's chat"))

    assert client.patch("/conversations/b1", json={"title": "mine"}, headers=_auth("alice")).status_code == 403
    assert client.patch("/conversations/nope", json={"title": "x"}, headers=_auth("alice")).status_code == 404
    assert client.get("/conversations", headers=_auth("bob")).json()["conversations"][0]["title"] == "bob's chat"
