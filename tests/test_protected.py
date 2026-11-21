"""
The agent must never touch ERPNext's scripts, settings or logic (CEO requirement), whatever the whitelist,
the write registry, a skill, an environment variable or a model says. Enforced in ERPNextClient._send.
"""

import pytest

from mcp_erpnext import crud, protected, write_registry as wr
from mcp_erpnext.auth import RequestContext, set_request_context
from mcp_erpnext.client import Credentials, ERPNextClient
from mcp_erpnext.protected import ProtectedResourceError, check_request, is_protected_doctype, validate_configuration

CORE = ["Server Script", "Client Script", "Custom Script", "Custom Field", "Property Setter", "DocType", "Workflow",
        "Report", "Print Format", "Notification", "Webhook", "Scheduled Job Type", "System Settings", "Role", "Custom DocPerm",
        "User Permission", "Patch Log", "Customize Form", "DocField", "Workflow Transition"]


class FakeHttp:
    """Stands in for httpx.Client so that a request that gets past the guard is visible."""
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _resp(self):
        from unittest.mock import MagicMock
        r = MagicMock(status_code=200, is_success=True)
        r.json.return_value = {"data": []}
        return r

    def get(self, url, **k):
        FakeHttp.sent.append(("GET", url)); return self._resp()

    def post(self, url, **k):
        FakeHttp.sent.append(("POST", url)); return self._resp()

    def put(self, url, **k):
        FakeHttp.sent.append(("PUT", url)); return self._resp()

    def delete(self, url, **k):
        FakeHttp.sent.append(("DELETE", url)); return self._resp()

    def request(self, method, url, **k):
        FakeHttp.sent.append((method, url)); return self._resp()


@pytest.fixture
def client(monkeypatch):
    import mcp_erpnext.client as client_module
    FakeHttp.sent = []
    monkeypatch.setattr(client_module.httpx, "Client", FakeHttp)
    return ERPNextClient(credentials=Credentials("http://erp.test", "k", "s"))


@pytest.mark.parametrize("doctype", CORE)
@pytest.mark.parametrize("verb", ["get", "post", "put", "delete"])
def test_a_protected_doctype_is_refused_for_every_verb_and_nothing_is_sent(client, doctype, verb):
    path = f"/api/resource/{doctype}" + ("/SOME-NAME" if verb in ("put", "delete") else "")
    with pytest.raises(ProtectedResourceError):
        getattr(client, verb)(path)
    assert FakeHttp.sent == []


@pytest.mark.parametrize("path", [
    "/api/resource/server script", "/api/resource/SERVER SCRIPT", "/api/resource/Server%20Script", "/api/resource/Server%2520Script",
    "/api/resource/Server Script/", "/api/resource/%53erver%20Script", "/api/resource/Server Script?fields=%5B%22script%22%5D",
    "/api/resource/Server  Script", "/api/resource/Client%20Script/x#frag", "/api/resource/ Custom Field",
])
def test_spelling_tricks_do_not_get_past_the_guard(client, path):
    with pytest.raises(ProtectedResourceError):
        client.get(path)
    assert FakeHttp.sent == []


@pytest.mark.parametrize("path", [
    "/api/method/frappe.client.set_value", "/api/method/frappe.client.insert", "/api/method/frappe.client.save",
    "/api/method/frappe.client.delete", "/api/method/frappe.client.submit", "/api/method/frappe.client.cancel",
    "/api/method/frappe.desk.form.save.savedocs", "/api/method/run_doc_method", "/api/method/frappe.handler.uploadfile",
    "/api/method/frappe.core.doctype.server_script.server_script.setup_scheduler_events",
    "/api/method/frappe.desk.form.load.getdoctype", "/api/method/frappe.client.get_count/extra", "/api/method/",
    "/api/method/frappe.client.get_count%2e", "/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice2",
    "/api/method/erpnext.accounts.doctype.journal_entry.journal_entry.make_journal_entry",
])
def test_an_unapproved_method_is_refused(client, path):
    with pytest.raises(ProtectedResourceError):
        client.post(path, json={})
    assert FakeHttp.sent == []


@pytest.mark.parametrize("path", ["/api/v2/document/Server Script", "/app/server-script", "/files/x.js", "/api/doctype/Server Script",
                                  "/", "", "/api/resource", "/api/resource/", "/api/resource//Customer",
                                  "/api/resource/Customer/../Server Script", "/api/resource/Customer\\..\\Server Script"])
def test_other_paths_and_malformed_paths_are_refused(client, path):
    with pytest.raises(ProtectedResourceError):
        client.get(path)
    assert FakeHttp.sent == []


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/resource/Sales Order"), ("get", "/api/resource/Sales Order/SAL-ORD-1"), ("post", "/api/resource/Customer"),
    ("put", "/api/resource/Item/ITEM-1"), ("get", "/api/resource/Stock Settings/Stock Settings"),
    ("get", "/api/method/frappe.client.get_count"), ("get", "/api/method/frappe.client.has_permission"),
    ("get", "/api/method/frappe.desk.query_report.run"),
    ("get", "/api/method/erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"),
    ("get", "/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice"),
    ("get", "/api/method/erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice"),
])
def test_the_calls_the_agent_is_built_to_make_still_go_through(client, verb, path):
    getattr(client, verb)(path)
    assert len(FakeHttp.sent) == 1


def test_every_conversion_method_in_the_registry_is_callable_and_nothing_else_is_added():
    methods = protected.allowed_methods()
    for doctype in wr.WRITE_REGISTRY:
        for source in wr.get_convert_sources(doctype):
            assert wr.get_convert_spec(doctype, source)["method"] in methods
    assert protected.FIXED_ALLOWED_METHODS <= methods
    assert all(m.startswith(("erpnext.", "frappe.client.", "frappe.desk.query_report")) for m in methods)


def test_the_guard_applies_in_a_writable_session_and_with_service_credentials(client):
    ctx = RequestContext(user_id="Administrator", roles=("System Manager",), read_only=False)
    with set_request_context(ctx):
        with pytest.raises(ProtectedResourceError):
            client.put("/api/resource/Server Script/x", json={"script": "frappe.throw('x')"})
    assert FakeHttp.sent == []


def test_the_crud_layer_never_reaches_the_client_for_a_protected_doctype(monkeypatch):
    # Even with a whitelist that names it, the doctype is not allowed and no request is made.
    monkeypatch.setenv("DOCTYPE_WHITELIST", "Server Script,Client Script,Customer")
    assert not crud.is_doctype_allowed("Server Script") and not crud.is_doctype_allowed("client script")
    assert crud.is_doctype_allowed("Customer")
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    for res in (crud.get_list("Server Script", client=mock_client), crud.create_document("Server Script", {"script": "x"}, client=mock_client),
                crud.update_document("Server Script", "x", {"script": "x"}, client=mock_client), crud.delete_document("Server Script", "x", client=mock_client)):
        assert res["status"] == "permission_denied"
    assert not [c for c in mock_client.mock_calls if c[0] in ("get", "post", "put", "delete")]


def test_startup_refuses_a_whitelist_that_names_a_protected_doctype(monkeypatch):
    monkeypatch.setenv("DOCTYPE_WHITELIST", "Customer,Server Script")
    with pytest.raises(ProtectedResourceError, match="Server Script"):
        validate_configuration()
    monkeypatch.setenv("DOCTYPE_WHITELIST", "Customer,Item")
    assert validate_configuration() == []


def test_startup_refuses_a_registry_entry_for_a_protected_doctype(monkeypatch):
    monkeypatch.setitem(wr.WRITE_REGISTRY, "Client Script", {"operations": {"update": {"roles": frozenset({"System Manager"})}}})
    with pytest.raises(ProtectedResourceError, match="Client Script"):
        validate_configuration()


def test_the_shipped_configuration_is_clean():
    assert validate_configuration() == []


def test_no_doctype_the_agent_reads_or_writes_by_default_is_protected():
    for name in crud.DEFAULT_WHITELIST + list(wr.WRITE_REGISTRY):
        assert not is_protected_doctype(name), name


def test_the_blocked_message_does_not_help_an_attacker_and_is_logged(client, caplog):
    with caplog.at_level("ERROR", logger="mcp_erpnext.protected"):
        with pytest.raises(ProtectedResourceError) as err:
            client.get("/api/resource/Server Script")
    assert "scripts, settings or logic" in str(err.value) and "Server Script" not in str(err.value)
    assert any("BLOCKED" in r.message for r in caplog.records)
