"""
Optimistic concurrency for writes: the `modified` value the caller last read is sent with
update, submit and cancel, and a refusal because the document changed is reported as a
conflict, not as an ordinary failure.
"""

from unittest.mock import MagicMock, patch

import pytest

from mcp_erpnext import crud
from mcp_erpnext.auth import RequestContext, set_request_context
from mcp_erpnext.write_registry import send_modified_enabled

pytestmark = pytest.mark.real_write_registry

MODIFIED = "2026-11-20 10:15:30.123456"


def manager():
    return RequestContext(user_id="m@example.com", roles=("Sales Manager",), read_only=False)


def purchaser():
    return RequestContext(user_id="p@example.com", roles=("Purchase User",), read_only=False)


def ok_response():
    resp = MagicMock()
    resp.is_success = True
    resp.status_code = 200
    resp.json.return_value = {"data": {"name": "SO-1"}}
    return resp


def error_response(status, body):
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    return resp


def client_returning(response):
    client = MagicMock()
    client.put.return_value = response
    return client


class TestModifiedIsSent:
    def test_submit_sends_modified(self):
        client = client_returning(ok_response())
        with set_request_context(manager()):
            crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert client.put.call_args.kwargs["json"] == {"docstatus": 1, "modified": MODIFIED}

    def test_cancel_sends_modified(self):
        client = client_returning(ok_response())
        with set_request_context(manager()):
            crud.cancel_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert client.put.call_args.kwargs["json"] == {"docstatus": 2, "modified": MODIFIED}

    def test_update_sends_modified_with_the_fields(self):
        client = client_returning(ok_response())
        with set_request_context(purchaser()):
            crud.update_document(
                "Purchase Order", "PO-1", {"schedule_date": "2026-12-01"}, client=client, expected_modified=MODIFIED
            )
        assert client.put.call_args.kwargs["json"] == {"schedule_date": "2026-12-01", "modified": MODIFIED}

    def test_nothing_is_added_when_not_given(self):
        client = client_returning(ok_response())
        with set_request_context(manager()):
            crud.submit_document("Sales Order", "SO-1", client=client)
        assert client.put.call_args.kwargs["json"] == {"docstatus": 1}

    @pytest.mark.parametrize("bad", [123, ["x"], {"a": 1}, 1.5])
    def test_invalid_value_is_refused_before_any_call(self, bad):
        client = client_returning(ok_response())
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=bad)
        assert res["status"] == "system_error"
        client.put.assert_not_called()

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_value_is_refused_not_silently_dropped(self, blank):
        # A blank value means the caller lost the timestamp. Sending the write without the
        # protection would hide that, so crud refuses it.
        client = client_returning(ok_response())
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=blank)
        assert res["status"] == "system_error"
        client.put.assert_not_called()


class TestConflictReporting:
    def test_timestamp_mismatch_is_reported_as_a_conflict(self):
        body = {"exc_type": "TimestampMismatchError", "exception": "frappe.exceptions.TimestampMismatchError: x"}
        client = client_returning(error_response(417, body))
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert res["status"] == "system_error"
        assert crud.is_conflict_error(res["error"])
        assert "changed by someone else" in res["error"]
        assert res["success"] is False

    def test_standard_message_wording_is_recognised(self):
        body = {
            "_server_messages": '["Document has been modified after you have opened it (a), please refresh and try again."]'
        }
        client = client_returning(error_response(409, body))
        with set_request_context(manager()):
            res = crud.cancel_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert crud.is_conflict_error(res["error"])

    def test_other_validation_errors_are_not_conflicts(self):
        body = {"exc_type": "ValidationError", "exception": "frappe.exceptions.ValidationError: Customer is required"}
        client = client_returning(error_response(417, body))
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert res["status"] == "system_error"
        assert not crud.is_conflict_error(res["error"])

    def test_permission_errors_stay_permission_errors(self):
        client = client_returning(error_response(403, {"exc_type": "PermissionError", "message": "No permission"}))
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client, expected_modified=MODIFIED)
        assert res["status"] == "permission_denied"

    def test_without_a_sent_value_a_mismatch_is_not_relabelled(self):
        client = client_returning(error_response(417, {"exc_type": "TimestampMismatchError"}))
        with set_request_context(manager()):
            res = crud.submit_document("Sales Order", "SO-1", client=client)
        assert not crud.is_conflict_error(res["error"])

    def test_is_conflict_error_only_matches_the_prefix(self):
        assert crud.is_conflict_error("Conflict: x")
        assert not crud.is_conflict_error("Operation failed: Conflict: x")
        assert not crud.is_conflict_error(None)
        assert not crud.is_conflict_error(5)


class TestSwitch:
    def test_default_is_on(self, monkeypatch):
        monkeypatch.delenv("ERPNEXT_SEND_MODIFIED", raising=False)
        assert send_modified_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
    def test_can_be_turned_off(self, monkeypatch, value):
        monkeypatch.setenv("ERPNEXT_SEND_MODIFIED", value)
        assert send_modified_enabled() is False


class TestAdapterPassThrough:
    def test_value_reaches_generic_tools_only_when_given(self):
        from agent.tools import generic_tools
        from agent.tools.adapter import ToolAdapter

        adapter = ToolAdapter(backend="direct")
        with patch.object(generic_tools, "submit_document", return_value={"status": "success"}) as sub:
            adapter.submit_document("Sales Order", "SO-1")
            sub.assert_called_with(doctype="Sales Order", name="SO-1")
            adapter.submit_document("Sales Order", "SO-1", expected_modified=MODIFIED)
            sub.assert_called_with(doctype="Sales Order", name="SO-1", expected_modified=MODIFIED)
        with patch.object(generic_tools, "update_document", return_value={"status": "success"}) as upd:
            adapter.update_document("Sales Order", "SO-1", {"delivery_date": "2026-12-01"}, expected_modified=MODIFIED)
            upd.assert_called_with(
                doctype="Sales Order", name="SO-1", fields={"delivery_date": "2026-12-01"}, expected_modified=MODIFIED
            )
        with patch.object(generic_tools, "cancel_document", return_value={"status": "success"}) as can:
            adapter.cancel_document("Sales Order", "SO-1", expected_modified=MODIFIED)
            can.assert_called_with(doctype="Sales Order", name="SO-1", expected_modified=MODIFIED)
