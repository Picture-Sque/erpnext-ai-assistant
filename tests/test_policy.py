"""
Tests for shared security policy mcp_erpnext/policy.py.

All tests configure their own environments and do not depend on a developer .env.
Verifies isolation, fail-closed authorization, input validation, and parity.
"""

import ast
import inspect
import os
import unittest
from typing import Any, Dict, List, Optional

from mcp_erpnext import crud, reports
from mcp_erpnext.policy import (
    ALL_TOOLS,
    CHAT_ONLY_WRITE_TOOLS,
    READ_TOOLS,
    RESERVED_IDENTITY_KEYS,
    WRITE_TOOLS,
    PolicyDecision,
    RequestContext,
    authorize_tool_call,
    check_doctype_allowed,
    check_report_access,
    check_role_access,
    check_tool_allowed,
    denied_envelope,
    validate_identifier,
    validate_order_by,
    validate_sort,
)


class TestPolicyDecision(unittest.TestCase):
    """Verify PolicyDecision structure, truthiness, and envelope generation."""

    def test_allow_decision(self):
        dec = PolicyDecision.allow(spec={"test": 123}, reason="ok")
        self.assertTrue(dec)
        self.assertTrue(dec.allowed)
        self.assertEqual(dec.reason, "ok")
        self.assertEqual(dec.spec, {"test": 123})
        self.assertEqual(dec.public_message, "")

    def test_deny_decision(self):
        dec = PolicyDecision.deny(reason="Detailed internal reason", public_message="Safe user error")
        self.assertFalse(dec)
        self.assertFalse(dec.allowed)
        self.assertEqual(dec.reason, "Detailed internal reason")
        self.assertEqual(dec.public_message, "Safe user error")
        self.assertIsNone(dec.spec)

    def test_denied_envelope_shape(self):
        dec = PolicyDecision.deny(reason="Secret error", public_message="Report 'X' is not available.")
        env = denied_envelope(dec)
        self.assertEqual(
            env,
            {
                "status": "permission_denied",
                "data": None,
                "error": "Report 'X' is not available.",
                "has_more": False,
                "success": False,
            },
        )
        # Matches reports._denied() exactly
        self.assertEqual(env, reports._denied("X"))


class TestRequestContext(unittest.TestCase):
    """Verify RequestContext validation, fail-closed inputs, and credential secrecy."""

    def test_defaults(self):
        ctx = RequestContext()
        self.assertIsNone(ctx.user_id)
        self.assertEqual(ctx.roles, ())
        self.assertIsNone(ctx.credential)
        self.assertTrue(ctx.read_only)
        self.assertIsNone(ctx.request_id)

    def test_valid_roles_coercion(self):
        ctx_list = RequestContext(roles=["Sales Manager", "Sales User"])
        self.assertEqual(ctx_list.roles, ("Sales Manager", "Sales User"))

        ctx_tuple = RequestContext(roles=("Accounts User",))
        self.assertEqual(ctx_tuple.roles, ("Accounts User",))

        ctx_set = RequestContext(roles={"Stock User"})
        self.assertEqual(ctx_set.roles, ("Stock User",))

    def test_malformed_roles_fail_closed(self):
        # Bare string must become ()
        self.assertEqual(RequestContext(roles="Sales User").roles, ())
        # Bytes must become ()
        self.assertEqual(RequestContext(roles=b"Sales User").roles, ())
        # None must become ()
        self.assertEqual(RequestContext(roles=None).roles, ())
        # Non-iterable must become ()
        self.assertEqual(RequestContext(roles=12345).roles, ())
        # List containing non-string or empty string must fail closed to ()
        self.assertEqual(RequestContext(roles=["Sales User", 123]).roles, ())
        self.assertEqual(RequestContext(roles=["Sales User", ""]).roles, ())
        self.assertEqual(RequestContext(roles=["Sales User", None]).roles, ())

    def test_malformed_read_only_fails_to_true(self):
        # Non-bool values default to True for safety
        self.assertTrue(RequestContext(read_only="false").read_only)
        self.assertTrue(RequestContext(read_only=0).read_only)
        self.assertTrue(RequestContext(read_only=None).read_only)
        self.assertTrue(RequestContext(read_only=[]).read_only)
        # Valid False remains False
        self.assertFalse(RequestContext(read_only=False).read_only)

    def test_credential_never_printed_in_repr_or_str(self):
        secret = "secret-token-super-confidential-999"
        ctx = RequestContext(user_id="user1", credential=secret, read_only=False)
        self.assertNotIn(secret, repr(ctx))
        self.assertNotIn(secret, str(ctx))


class TestToolClassification(unittest.TestCase):
    """Verify READ_TOOLS and WRITE_TOOLS completeness against crud.py and reports.py."""

    def test_tools_are_disjoint(self):
        self.assertTrue(READ_TOOLS.isdisjoint(WRITE_TOOLS))
        self.assertEqual(ALL_TOOLS, READ_TOOLS | WRITE_TOOLS)
        # Chat-only write tools are a separate category, outside what the MCP server offers.
        self.assertTrue(CHAT_ONLY_WRITE_TOOLS.isdisjoint(ALL_TOOLS))

    def test_chat_only_write_tools_are_unreachable_over_mcp(self):
        from mcp_erpnext import server

        self.assertEqual(CHAT_ONLY_WRITE_TOOLS, frozenset({"convert_document"}))
        for tool in CHAT_ONLY_WRITE_TOOLS:
            ctx = RequestContext(user_id="u", roles=("System Manager",), read_only=False)
            decision = check_tool_allowed(ctx, tool)
            self.assertFalse(decision)
            self.assertIn("not available", decision.public_message)
            self.assertFalse(authorize_tool_call(ctx, tool, {"doctype": "Sales Order"}))
            self.assertNotIn(tool, server.TOOL_METADATA)
            result = server.dispatch_tool(tool, {"target_doctype": "Sales Order"}, ctx)
            self.assertFalse(result.get("success", False))
            self.assertNotEqual(result.get("status"), "success")

    def test_all_public_tool_functions_classified(self):
        """Every tool function with a 'client' parameter in crud and reports must be in exactly one set."""
        discovered_tools = set()
        for mod in (crud, reports):
            for name, fn in inspect.getmembers(mod, inspect.isfunction):
                if not name.startswith("_") and fn.__module__ == mod.__name__:
                    sig = inspect.signature(fn)
                    if "client" in sig.parameters:
                        discovered_tools.add(name)

        classified = ALL_TOOLS | CHAT_ONLY_WRITE_TOOLS
        self.assertEqual(
            discovered_tools,
            classified,
            f"Tool registry mismatch. Unclassified or missing: {discovered_tools ^ classified}",
        )

    def test_write_tools_match_graph_ast(self):
        expected_write_tools = {
            "create_document",
            "update_document",
            "delete_document",
            "cancel_document",
            "submit_document",
        }
        self.assertEqual(WRITE_TOOLS, expected_write_tools)

        # Inspect agent/workflow/graph.py via AST to verify _WRITE_TOOLS definition matches
        graph_path = os.path.join(os.path.dirname(__file__), "..", "agent", "workflow", "graph.py")
        if os.path.exists(graph_path):
            with open(graph_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
            graph_write_tools = None
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "_WRITE_TOOLS":
                            if isinstance(node.value, ast.Call):
                                # frozenset({...})
                                arg = node.value.args[0]
                                if isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
                                    graph_write_tools = {
                                        elt.value for elt in arg.elts if isinstance(elt, ast.Constant)
                                    }
            # The graph's write tools are the MCP write tools plus the chat-only ones.
            self.assertEqual(WRITE_TOOLS | CHAT_ONLY_WRITE_TOOLS, graph_write_tools)


class TestCheckToolAllowed(unittest.TestCase):
    """Verify tool authorization against read_only context."""

    def test_unknown_or_invalid_tool_denied(self):
        ctx = RequestContext(read_only=False)
        self.assertFalse(check_tool_allowed(ctx, "drop_table"))
        self.assertFalse(check_tool_allowed(ctx, ""))
        self.assertFalse(check_tool_allowed(ctx, None))
        self.assertFalse(check_tool_allowed(ctx, 123))

    def test_read_tools_allowed_in_both_modes(self):
        for tool in READ_TOOLS:
            self.assertTrue(check_tool_allowed(RequestContext(read_only=True), tool))
            self.assertTrue(check_tool_allowed(RequestContext(read_only=False), tool))

    def test_write_tools_denied_in_read_only_mode(self):
        for tool in WRITE_TOOLS:
            dec_ro = check_tool_allowed(RequestContext(read_only=True), tool)
            self.assertFalse(dec_ro)
            self.assertIn("read-only mode", dec_ro.public_message)

            dec_rw = check_tool_allowed(RequestContext(read_only=False), tool)
            self.assertTrue(dec_rw)


class TestCheckRoleAccess(unittest.TestCase):
    """Verify strict fail-closed role matching."""

    def test_user_roles_fail_closed(self):
        allowed = ["Sales Manager"]
        self.assertFalse(check_role_access([], allowed))
        self.assertFalse(check_role_access(None, allowed))
        self.assertFalse(check_role_access("Sales Manager", allowed))  # Bare string denies
        self.assertFalse(check_role_access(b"Sales Manager", allowed))  # Bytes denies
        self.assertFalse(check_role_access(12345, allowed))

    def test_allowed_roles_fail_closed(self):
        user = ["Sales Manager"]
        self.assertFalse(check_role_access(user, []))
        self.assertFalse(check_role_access(user, None))
        self.assertFalse(check_role_access(user, "Sales Manager"))  # Bare string denies
        self.assertFalse(check_role_access(user, ["", None]))

    def test_case_sensitivity(self):
        self.assertFalse(check_role_access(["sales manager"], ["Sales Manager"]))
        self.assertTrue(check_role_access(["Sales Manager"], ["Sales Manager"]))

    def test_partial_and_whitespace_elements(self):
        # Non-string/empty entries in user roles are ignored
        self.assertTrue(check_role_access(["Sales Manager", None, 123, ""], ["Sales Manager"]))
        self.assertFalse(check_role_access(["", None], ["Sales Manager"]))

    def test_decision_type_and_reason_wording(self):
        dec = check_role_access(["Stock User"], ["Accounts User", "Sales User"])
        self.assertIsInstance(dec, PolicyDecision)
        self.assertFalse(dec)
        self.assertIn("Your role(s) (Stock User) do not include any of the required roles", dec.reason)
        self.assertEqual(dec.public_message, "Permission denied.")


class TestCheckDoctypeAllowed(unittest.TestCase):
    """Verify doctype allowlist checks and parity with crud.is_doctype_allowed."""

    def test_parity_across_matrix(self):
        allowed_list = crud.get_allowed_doctypes()
        first_allowed = next(iter(allowed_list))

        test_cases = [
            first_allowed,
            first_allowed.lower(),
            first_allowed.upper(),
            f"  {first_allowed}  ",
            "",
            "   ",
            None,
            "NonExistentDocType12345",
        ]

        for dt in test_cases:
            crud_result = crud.is_doctype_allowed(dt)
            policy_dec = check_doctype_allowed(dt)
            self.assertIsInstance(policy_dec, PolicyDecision)
            self.assertEqual(
                crud_result,
                bool(policy_dec),
                f"Mismatch for doctype '{dt}': crud={crud_result}, policy={bool(policy_dec)}",
            )

        # Non-string types safely fail closed in policy (crud.is_doctype_allowed would raise AttributeError)
        self.assertFalse(check_doctype_allowed(123))
        self.assertFalse(check_doctype_allowed(["Some Doc"]))
        self.assertFalse(check_doctype_allowed(True))

    def test_env_override_parity(self):
        old_env = os.environ.get("DOCTYPE_WHITELIST")
        try:
            os.environ["DOCTYPE_WHITELIST"] = "MockInvoice,MockPayment"
            self.assertTrue(check_doctype_allowed("MockInvoice"))
            self.assertTrue(check_doctype_allowed("mockinvoice"))
            self.assertEqual(crud.is_doctype_allowed("MockInvoice"), bool(check_doctype_allowed("MockInvoice")))
            self.assertFalse(check_doctype_allowed("OtherDoc"))
        finally:
            if old_env is not None:
                os.environ["DOCTYPE_WHITELIST"] = old_env
            else:
                os.environ.pop("DOCTYPE_WHITELIST", None)


class TestValidateOrderByAndIdentifier(unittest.TestCase):
    """Verify SQL injection prevention and regex fullmatch enforcement."""

    def test_order_by_valid(self):
        self.assertTrue(validate_order_by(None))
        self.assertTrue(validate_order_by("name asc"))
        self.assertTrue(validate_order_by("creation desc, modified asc"))

    def test_order_by_invalid_and_injection(self):
        self.assertFalse(validate_order_by(""))
        self.assertFalse(validate_order_by("   "))
        self.assertFalse(validate_order_by(123))
        self.assertFalse(validate_order_by(True))
        self.assertFalse(validate_order_by("name desc; DROP TABLE tabCustomer"))
        self.assertFalse(validate_order_by("name asc -- comment"))

    def test_order_by_trailing_newline_rejected(self):
        # policy uses fullmatch and rejects newlines
        val = "creation desc\n"
        self.assertFalse(validate_order_by(val))

    def test_identifier_valid(self):
        self.assertTrue(validate_identifier("field_name"))
        self.assertTrue(validate_identifier("field123"))

    def test_identifier_invalid_and_injection(self):
        self.assertFalse(validate_identifier(""))
        self.assertFalse(validate_identifier(None))
        self.assertFalse(validate_identifier(True))
        self.assertFalse(validate_identifier(123))
        self.assertFalse(validate_identifier("1field"))
        self.assertFalse(validate_identifier("field; DROP"))

    def test_identifier_trailing_newline_rejected(self):
        # crud's regex IDENTIFIER_PATTERN.match("name\n") would accept "name\n" because $ matches before \n.
        # policy uses fullmatch() which strictly rejects trailing newlines.
        val = "identifier\n"
        self.assertTrue(crud.IDENTIFIER_PATTERN.match(val) is not None)
        self.assertFalse(validate_identifier(val))

    def test_sort_valid(self):
        self.assertTrue(validate_sort(None))
        self.assertTrue(validate_sort("name"))
        self.assertTrue(validate_sort("name asc"))
        self.assertTrue(validate_sort("name DESC"))

    def test_sort_with_allowed_fields(self):
        allowed = {"customer_group", "grand_total"}
        self.assertTrue(validate_sort("grand_total desc", allowed_fields=allowed))
        self.assertTrue(validate_sort("customer_group", allowed_fields=allowed))
        # Field not in allowed set is denied
        dec = validate_sort("unrelated_field desc", allowed_fields=allowed)
        self.assertFalse(dec)
        self.assertIn("not in allowed fields", dec.reason)

    def test_sort_invalid_and_injection(self):
        self.assertFalse(validate_sort(""))
        self.assertFalse(validate_sort("   "))
        self.assertFalse(validate_sort(123))
        self.assertFalse(validate_sort(True))
        self.assertFalse(validate_sort("status invalid_direction"))
        self.assertFalse(validate_sort("status asc; DROP TABLE tabCustomer"))
        self.assertFalse(validate_sort("1status asc"))

    def test_sort_trailing_newline_rejected(self):
        self.assertFalse(validate_sort("status asc\n"))
        self.assertFalse(validate_sort("status\n desc"))


class TestCheckReportAccess(unittest.TestCase):
    """Verify report authorization parity with reports._authorize and identical denial message format."""

    def test_every_configured_report_authorization_matrix(self):
        """Test EVERY configured report against permitted, unrelated, empty, missing, and malformed roles."""
        allowlist = reports.get_report_allowlist()
        self.assertTrue(len(allowlist) >= 3, "Expected at least 3 configured reports in allowlist")

        for rep_name, spec in allowlist.items():
            allowed_roles = list(spec.get("allowed_roles", []))
            self.assertTrue(len(allowed_roles) > 0, f"Report {rep_name} must have declared allowed_roles")

            # 1. Permitted roles: each role individually
            for single_role in allowed_roles:
                ctx = RequestContext(roles=(single_role,))
                dec = check_report_access(ctx, rep_name)
                self.assertTrue(dec, f"Expected {rep_name} allowed for {single_role}")
                self.assertEqual(dec.spec, spec)
                self.assertIsNotNone(reports._authorize(rep_name, [single_role]))

            # 1b. Permitted roles: all together
            ctx_all = RequestContext(roles=tuple(allowed_roles))
            dec_all = check_report_access(ctx_all, rep_name)
            self.assertTrue(dec_all)
            self.assertEqual(dec_all.spec, spec)

            # 2. Unrelated roles
            unrelated = ["NonExistentRoleXYZ", "Customer"]
            ctx_unrelated = RequestContext(roles=tuple(unrelated))
            dec_unrelated = check_report_access(ctx_unrelated, rep_name)
            self.assertFalse(dec_unrelated)
            self.assertIsNone(reports._authorize(rep_name, unrelated))
            self.assertEqual(dec_unrelated.public_message, f"Report '{rep_name}' is not available.")
            self.assertEqual(denied_envelope(dec_unrelated), reports._denied(rep_name))

            # 3. Empty roles
            ctx_empty = RequestContext(roles=())
            dec_empty = check_report_access(ctx_empty, rep_name)
            self.assertFalse(dec_empty)
            self.assertIsNone(reports._authorize(rep_name, []))
            self.assertEqual(denied_envelope(dec_empty), reports._denied(rep_name))

            # 4. Missing roles (default RequestContext)
            ctx_missing = RequestContext()
            dec_missing = check_report_access(ctx_missing, rep_name)
            self.assertFalse(dec_missing)
            self.assertIsNone(reports._authorize(rep_name, None))
            self.assertEqual(denied_envelope(dec_missing), reports._denied(rep_name))

            # 5. Malformed role inputs
            for malformed in ("Stock User", b"Stock User", 123, ["InvalidRole", 456]):
                ctx_bad = RequestContext(roles=malformed)
                dec_bad = check_report_access(ctx_bad, rep_name)
                self.assertFalse(dec_bad)
                self.assertEqual(denied_envelope(dec_bad), reports._denied(rep_name))
                try:
                    self.assertIsNone(reports._authorize(rep_name, malformed))
                except TypeError:
                    # reports._authorize raises TypeError for non-iterable types like int;
                    # RequestContext / policy.py safely fails closed without crashing.
                    pass

            # Mixed-type list containing valid role but also invalid entry fails closed in RequestContext
            ctx_mixed = RequestContext(roles=["Stock User", 456])
            self.assertEqual(ctx_mixed.roles, ())  # Failed closed to ()
            self.assertFalse(check_report_access(ctx_mixed, rep_name))

    def test_unknown_and_unlisted_reports_denied(self):
        """Test unknown, unlisted, and invalid report names fail closed with reports._denied format."""
        roles = ("System Manager", "Stock User", "Sales User")
        ctx = RequestContext(roles=roles)

        unknown_reports = ["Unknown Report 999", "General Ledger", "", None, 123, True]
        for rep in unknown_reports:
            dec = check_report_access(ctx, rep)
            self.assertFalse(dec)
            self.assertEqual(dec.public_message, f"Report '{rep}' is not available.")
            self.assertEqual(denied_envelope(dec), reports._denied(rep))
            self.assertIsNone(reports._authorize(rep, list(roles)))

    def test_public_message_format_matches_reports_denied_for_unknown_unlisted_and_role_denied(self):
        """Verify denial message format f"Report '{report_name}' is not available." across denial scenarios."""
        # Unknown report
        dec1 = check_report_access(RequestContext(roles=("Accounts Manager",)), "Unknown Report 999")
        # Unlisted / non-string
        dec2 = check_report_access(RequestContext(roles=("Accounts Manager",)), 404)
        # Role-denied
        sample_report = next(iter(reports.get_report_allowlist().keys()))
        dec3 = check_report_access(RequestContext(roles=("UnauthorizedRole",)), sample_report)

        self.assertEqual(dec1.public_message, "Report 'Unknown Report 999' is not available.")
        self.assertEqual(dec2.public_message, "Report '404' is not available.")
        self.assertEqual(dec3.public_message, f"Report '{sample_report}' is not available.")

        # Confirm shape matches reports._denied
        self.assertEqual(denied_envelope(dec1), reports._denied("Unknown Report 999"))
        self.assertEqual(denied_envelope(dec2), reports._denied(404))
        self.assertEqual(denied_envelope(dec3), reports._denied(sample_report))


class TestAuthorizeToolCall(unittest.TestCase):
    """Verify single entry point for MCP tool authorization."""

    def setUp(self):
        self.allowed_dt = next(iter(crud.get_allowed_doctypes()))

    def test_reserved_identity_keys_denied(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        for reserved_key in RESERVED_IDENTITY_KEYS:
            args = {"doctype": self.allowed_dt, reserved_key: "malicious_injected_value"}
            dec = authorize_tool_call(ctx, "get_list", args)
            self.assertFalse(dec, f"Expected denial when reserved key '{reserved_key}' is present")
            self.assertIn("reserved parameter", dec.public_message)

    def test_unknown_tool_denied(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        dec = authorize_tool_call(ctx, "unknown_tool", {})
        self.assertFalse(dec)

    def test_read_tool_allowed(self):
        ctx = RequestContext(roles=("System Manager",), read_only=True)
        dec = authorize_tool_call(ctx, "get_list", {"doctype": self.allowed_dt, "order_by": "name desc"})
        self.assertTrue(dec)

    def test_write_tool_denied_in_read_only(self):
        ctx = RequestContext(roles=("System Manager",), read_only=True)
        dec = authorize_tool_call(ctx, "create_document", {"doctype": self.allowed_dt})
        self.assertFalse(dec)
        self.assertIn("read-only mode", dec.public_message)

    def test_write_tool_allowed_when_not_read_only(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        dec = authorize_tool_call(ctx, "create_document", {"doctype": self.allowed_dt})
        self.assertTrue(dec)

    def test_doctype_missing_or_invalid(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        self.assertFalse(authorize_tool_call(ctx, "get_list", {}))
        self.assertFalse(authorize_tool_call(ctx, "get_list", {"doctype": "NonExistentDoc"}))

    def test_parent_doctype_validated_if_present(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        # Valid parent doctype
        self.assertTrue(authorize_tool_call(ctx, "get_list", {"doctype": self.allowed_dt, "parent_doctype": self.allowed_dt}))
        # Invalid parent doctype
        self.assertFalse(authorize_tool_call(ctx, "get_list", {"doctype": self.allowed_dt, "parent_doctype": "FakeDocType"}))

    def test_parent_doctype_none_fails_closed(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        # parent_doctype present with None must fail closed
        dec = authorize_tool_call(ctx, "get_list", {"doctype": self.allowed_dt, "parent_doctype": None})
        self.assertFalse(dec)
        self.assertIn("DocType must be a non-empty string", dec.reason)

    def test_group_by_and_metric_validation(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        # Valid identifiers
        self.assertTrue(authorize_tool_call(ctx, "aggregate", {"doctype": self.allowed_dt, "group_by": "status", "metric": "total"}))
        # Invalid group_by
        self.assertFalse(authorize_tool_call(ctx, "aggregate", {"doctype": self.allowed_dt, "group_by": "status; DROP"}))
        # Invalid metric
        self.assertFalse(authorize_tool_call(ctx, "aggregate", {"doctype": self.allowed_dt, "metric": "1bad"}))

    def test_group_by_and_metric_none_fails_closed(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        # group_by present with None must fail closed
        dec_gb = authorize_tool_call(ctx, "aggregate", {"doctype": self.allowed_dt, "group_by": None, "metric": "total"})
        self.assertFalse(dec_gb)
        self.assertIn("group_by must be a non-empty string", dec_gb.reason)

        # metric present with None must fail closed
        dec_m = authorize_tool_call(ctx, "aggregate", {"doctype": self.allowed_dt, "group_by": "status", "metric": None})
        self.assertFalse(dec_m)
        self.assertIn("metric must be a non-empty string", dec_m.reason)

    def test_run_report_authorization(self):
        sample_report = next(iter(reports.get_report_allowlist().keys()))
        allowed_roles = reports.get_report_allowlist()[sample_report].get("allowed_roles", [])
        self.assertTrue(len(allowed_roles) > 0)

        # Authorized user
        ctx_auth = RequestContext(roles=tuple(allowed_roles), read_only=True)
        dec_auth = authorize_tool_call(ctx_auth, "run_report", {"report_name": sample_report})
        self.assertTrue(dec_auth)
        self.assertIsNotNone(dec_auth.spec)

        # Unauthorized user
        ctx_unauth = RequestContext(roles=("Guest",), read_only=True)
        dec_unauth = authorize_tool_call(ctx_unauth, "run_report", {"report_name": sample_report})
        self.assertFalse(dec_unauth)
        self.assertEqual(dec_unauth.public_message, f"Report '{sample_report}' is not available.")

    def test_aggregate_sort_validation(self):
        ctx = RequestContext(roles=("System Manager",), read_only=False)
        # Sort matching metric
        dec = authorize_tool_call(
            ctx,
            "aggregate",
            {"doctype": self.allowed_dt, "group_by": "status", "metric": "total", "sort": "total desc"},
        )
        self.assertTrue(dec)

        # Sort matching group_by
        dec_gb = authorize_tool_call(
            ctx,
            "aggregate",
            {"doctype": self.allowed_dt, "group_by": "status", "metric": "total", "sort": "status asc"},
        )
        self.assertTrue(dec_gb)

        # Sort with field not in (group_by, metric) must fail
        dec_invalid_field = authorize_tool_call(
            ctx,
            "aggregate",
            {"doctype": self.allowed_dt, "group_by": "status", "metric": "total", "sort": "unrelated desc"},
        )
        self.assertFalse(dec_invalid_field)
        self.assertIn("not in allowed fields", dec_invalid_field.reason)

        # Malformed / injection in sort must fail
        dec_inject = authorize_tool_call(
            ctx,
            "aggregate",
            {"doctype": self.allowed_dt, "group_by": "status", "metric": "total", "sort": "total; DROP"},
        )
        self.assertFalse(dec_inject)

    def test_run_report_sort_validation(self):
        sample_report = next(iter(reports.get_report_allowlist().keys()))
        allowed_roles = reports.get_report_allowlist()[sample_report].get("allowed_roles", [])
        ctx = RequestContext(roles=tuple(allowed_roles), read_only=True)

        # Valid sort
        dec = authorize_tool_call(
            ctx, "run_report", {"report_name": sample_report, "sort": "item_code asc"}
        )
        self.assertTrue(dec)

        # Invalid sort format
        dec_bad = authorize_tool_call(
            ctx, "run_report", {"report_name": sample_report, "sort": "item_code invalid_dir"}
        )
        self.assertFalse(dec_bad)


class TestPublicFunctionsReturnTypePolicyDecision(unittest.TestCase):
    """Ensure all public policy check functions return PolicyDecision instances (never tuples or bools)."""

    def test_all_checks_return_policy_decision(self):
        ctx_ro = RequestContext(roles=("System Manager",), read_only=True)

        sample_dt = next(iter(crud.get_allowed_doctypes()))
        sample_rep = next(iter(reports.get_report_allowlist().keys()))

        checks = [
            ("check_tool_allowed_allowed", check_tool_allowed(ctx_ro, "get_list")),
            ("check_tool_allowed_denied", check_tool_allowed(ctx_ro, "create_document")),
            ("check_doctype_allowed_allowed", check_doctype_allowed(sample_dt)),
            ("check_doctype_allowed_denied", check_doctype_allowed("NonExistentDoc")),
            ("validate_order_by_allowed", validate_order_by("name asc")),
            ("validate_order_by_denied", validate_order_by("invalid;--")),
            ("validate_identifier_allowed", validate_identifier("col_name")),
            ("validate_identifier_denied", validate_identifier("col;--")),
            ("validate_sort_allowed", validate_sort("name asc")),
            ("validate_sort_denied", validate_sort("invalid;--")),
            ("check_role_access_allowed", check_role_access(["Sales User"], ["Sales User"])),
            ("check_role_access_denied", check_role_access(["Guest"], ["Sales User"])),
            ("check_report_access_allowed", check_report_access(ctx_ro, sample_rep)),
            ("check_report_access_denied", check_report_access(ctx_ro, "Unknown Report")),
            ("authorize_tool_call_allowed", authorize_tool_call(ctx_ro, "get_list", {"doctype": sample_dt})),
            ("authorize_tool_call_denied", authorize_tool_call(ctx_ro, "create_document", {"doctype": sample_dt})),
        ]

        for name, result in checks:
            self.assertIs(
                type(result),
                PolicyDecision,
                f"Check '{name}' did not return PolicyDecision, got {type(result)}: {result!r}",
            )
            self.assertNotIsInstance(result, tuple)
            self.assertNotIsInstance(result, bool)


class TestImportHygieneAndIsolation(unittest.TestCase):
    """Verify strict package isolation and prevent circular imports."""

    def test_policy_has_no_forbidden_imports(self):
        forbidden_roots = {"agent", "tools", "workflow", "ai_assistant", "main", "dotenv"}
        policy_path = os.path.join(os.path.dirname(__file__), "..", "mcp_erpnext", "policy.py")
        with open(policy_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=policy_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_pkg = alias.name.split(".")[0]
                    self.assertNotIn(top_pkg, forbidden_roots)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_pkg = node.module.split(".")[0]
                    self.assertNotIn(top_pkg, forbidden_roots)

    def test_crud_and_reports_do_not_import_policy(self):
        """Ensure no circular import is created in this phase."""
        for mod_name in ("crud.py", "reports.py"):
            path = os.path.join(os.path.dirname(__file__), "..", "mcp_erpnext", mod_name)
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("policy", alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.assertNotIn("policy", node.module)

    def test_no_hardcoded_doctype_literals_in_policy(self):
        """Ensure policy.py contains no hard-coded business doctype literals."""
        denylist = ["Sales Order", "Customer", "Item", "Bin", "Sales Invoice", "Purchase Order"]
        policy_path = os.path.join(os.path.dirname(__file__), "..", "mcp_erpnext", "policy.py")
        with open(policy_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for denied in denylist:
                    self.assertNotEqual(
                        node.value.lower(),
                        denied.lower(),
                        f"Found hardcoded doctype '{denied}' in policy.py line {node.lineno}",
                    )


if __name__ == "__main__":
    unittest.main()
