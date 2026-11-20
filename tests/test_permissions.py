"""
The ERPNext permission preflight (mcp_erpnext/permissions.py).

Only a literal true allows and only a literal false denies. Everything else is "unknown",
which never blocks a write: ERPNext still refuses an unauthorised write itself.
"""

from unittest.mock import MagicMock

import pytest

from mcp_erpnext import permissions as perm
from mcp_erpnext.auth import RequestContext, set_request_context
from mcp_erpnext.errors import ERPNextConfigError, ERPNextTransportError


def answer(body, status=200):
    resp = MagicMock()
    resp.is_success = 200 <= status < 300
    resp.status_code = status
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    return resp


def client_answering(body, status=200):
    client = MagicMock()
    client.get.return_value = answer(body, status)
    return client


def has(value):
    return {"message": {"has_permission": value}}


class TestOutcomes:
    def test_true_allows(self):
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client_answering(has(True)))
        assert res.outcome == perm.ALLOWED and res.allowed and not res.denied

    def test_false_denies(self):
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client_answering(has(False)))
        assert res.outcome == perm.DENIED and res.denied and not res.allowed
        assert "write" in res.detail and "Sales Order" in res.detail

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"message": None},
            {"message": {}},
            {"message": {"has_permission": None}},
            {"message": {"has_permission": "true"}},
            {"message": {"has_permission": "false"}},
            {"message": {"has_permission": 1}},
            {"message": {"has_permission": 0}},
            {"message": True},
            {"message": [True]},
            [True],
            "yes",
            None,
        ],
    )
    def test_anything_but_a_plain_boolean_is_unknown(self, body):
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client_answering(body))
        assert res.outcome == perm.UNKNOWN

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 417, 500, 503])
    def test_http_errors_are_unknown_never_a_denial(self, status):
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client_answering(has(False), status))
        assert res.outcome == perm.UNKNOWN

    def test_a_body_that_is_not_json_is_unknown(self):
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client_answering(ValueError("not json")))
        assert res.outcome == perm.UNKNOWN

    @pytest.mark.parametrize("exc", [ERPNextTransportError("down"), ERPNextConfigError("no creds"), RuntimeError("boom"), TimeoutError()])
    def test_a_failing_call_is_unknown_and_never_raises(self, exc):
        client = MagicMock()
        client.get.side_effect = exc
        assert perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client).outcome == perm.UNKNOWN


class TestWhatIsAsked:
    def test_document_level_check_sends_doctype_name_and_permission_type(self):
        client = client_answering(has(True))
        perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client)
        (args, kwargs) = client.get.call_args
        assert args[0] == "/api/method/frappe.client.has_permission"
        assert kwargs["params"] == {"doctype": "Sales Order", "docname": "SO-1", "perm_type": "write"}

    @pytest.mark.parametrize(
        "operation,expected",
        [("create", "create"), ("update", "write"), ("submit", "submit"), ("cancel", "cancel"), ("delete", "delete")],
    )
    def test_every_operation_maps_to_an_erpnext_permission_type(self, operation, expected):
        client = client_answering(has(True))
        perm.check_erpnext_permission(operation, "Sales Order", "SO-1", client=client)
        assert client.get.call_args.kwargs["params"]["perm_type"] == expected

    def test_a_create_asks_at_doctype_level_with_an_empty_name(self):
        client = client_answering(has(True))
        perm.check_erpnext_permission("create", "Sales Order", None, client=client)
        assert client.get.call_args.kwargs["params"]["docname"] == ""

    def test_the_name_is_trimmed(self):
        client = client_answering(has(True))
        perm.check_erpnext_permission("submit", " Sales Order ", " SO-1 ", client=client)
        assert client.get.call_args.kwargs["params"] == {"doctype": "Sales Order", "docname": "SO-1", "perm_type": "submit"}

    def test_it_is_a_read_so_it_works_in_a_read_only_session(self):
        # A GET is allowed by the read-only transport backstop, so the check never needs a write.
        client = client_answering(has(True))
        perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client)
        client.post.assert_not_called()
        client.put.assert_not_called()
        client.delete.assert_not_called()


class TestNotAsked:
    @pytest.mark.parametrize("operation", ["drop", "read", "", None, 5])
    def test_unsupported_operations_are_unknown_without_a_call(self, operation):
        client = client_answering(has(False))
        assert perm.check_erpnext_permission(operation, "Sales Order", "SO-1", client=client).outcome == perm.UNKNOWN
        client.get.assert_not_called()

    @pytest.mark.parametrize("doctype", ["", "   ", None, 5])
    def test_no_doctype_is_unknown_without_a_call(self, doctype):
        client = client_answering(has(False))
        assert perm.check_erpnext_permission("update", doctype, "SO-1", client=client).outcome == perm.UNKNOWN
        client.get.assert_not_called()

    @pytest.mark.parametrize("value", ["false", "0", "no", "FALSE"])
    def test_the_switch_turns_the_check_off(self, monkeypatch, value):
        monkeypatch.setenv("ERPNEXT_PERMISSION_PREFLIGHT", value)
        client = client_answering(has(False))
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1", client=client)
        assert res.outcome == perm.UNKNOWN and "switched off" in res.detail
        client.get.assert_not_called()

    def test_default_is_on(self, monkeypatch):
        monkeypatch.delenv("ERPNEXT_PERMISSION_PREFLIGHT", raising=False)
        assert perm.preflight_enabled() is True

    def test_without_a_request_context_there_is_no_user_to_ask_about(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(perm, "ERPNextClient", called)
        res = perm.check_erpnext_permission("update", "Sales Order", "SO-1")
        assert res.outcome == perm.UNKNOWN and "no request context" in res.detail
        called.assert_not_called()

    def test_with_a_request_context_the_users_own_client_is_used(self, monkeypatch):
        client = client_answering(has(False))
        factory = MagicMock(return_value=client)
        monkeypatch.setattr(perm, "ERPNextClient", factory)
        ctx = RequestContext(user_id="sandra@example.com", roles=("Sales User",), read_only=False)
        with set_request_context(ctx):
            res = perm.check_erpnext_permission("update", "Sales Order", "SO-1")
        assert res.denied
        factory.assert_called_once_with()  # no explicit credentials: the context supplies the user's session


class TestPlumbing:
    def test_the_adapter_passes_the_question_through(self, monkeypatch):
        from agent.tools import generic_tools
        from agent.tools.adapter import ToolAdapter

        sentinel = perm.PermissionCheck(perm.DENIED, "x")
        seen = {}

        def fake(operation, doctype, name=None):
            seen["args"] = (operation, doctype, name)
            return sentinel

        monkeypatch.setattr(generic_tools, "check_permission", fake)
        assert ToolAdapter(backend="direct").check_permission("submit", "Sales Order", "SO-1") is sentinel
        assert seen["args"] == ("submit", "Sales Order", "SO-1")
        assert ToolAdapter(backend="mcp").check_permission("create", "Sales Order") is sentinel
        assert seen["args"] == ("create", "Sales Order", None)

    def test_the_module_stays_free_of_agent_imports(self):
        import inspect

        source = inspect.getsource(perm)
        for forbidden in ("from agent", "import agent", "from tools", "from workflow", "dotenv"):
            assert forbidden not in source
