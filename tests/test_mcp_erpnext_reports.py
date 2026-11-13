import copy
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import httpx

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from mcp_erpnext import reports
from mcp_erpnext.reports import HARD_MAX_ROWS, REPORT_ALLOWLIST, run_report

STOCK_ROLES = ["Stock User"]
SALES_FILTERS = {
    "company": "Acme Co", "tree_type": "Customer", "doc_type": "Sales Order",
    "value_quantity": "Value", "from_date": "2026-01-01", "to_date": "2026-12-31", "range": "Monthly",
}
LEDGER_FILTERS = {"company": "Acme Co", "from_date": "2026-01-01", "to_date": "2026-12-31"}

COLUMNS = [
    {"label": "Item", "fieldname": "item_code", "fieldtype": "Link"},
    {"label": "Warehouse", "fieldname": "warehouse", "fieldtype": "Link"},
    {"label": "Qty", "fieldname": "qty", "fieldtype": "Float"},
]
ROWS = [
    {"item_code": "A", "warehouse": "W1", "qty": 5.0},
    {"item_code": "B", "warehouse": "W1", "qty": 20.0},
    {"item_code": "C", "warehouse": "W2", "qty": 12.5},
    {"item_code": "A", "warehouse": "W2", "qty": 3.0},
]


def _response(status_code=200, message=None, text=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text or json.dumps({"message": message})
    if message is not None or status_code == 200:
        resp.json.return_value = {"message": message}
    else:
        resp.json.side_effect = ValueError("Non-JSON")
    return resp


def _report(rows=None, columns=None, add_total_row=False, **extra):
    msg = {"columns": copy.deepcopy(columns or COLUMNS), "result": copy.deepcopy(rows if rows is not None else ROWS),
           "add_total_row": add_total_row}
    msg.update(extra)
    return msg


class ReportsTestBase(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.get.return_value = _response(200, _report())

    def run_ok(self, **kwargs):
        kwargs.setdefault("user_roles", STOCK_ROLES)
        kwargs.setdefault("filters", {"company": "Acme Co"})
        return run_report("Stock Projected Qty", client=self.client, **kwargs)


class TestAuthorization(ReportsTestBase):
    def assert_denied_without_call(self, res):
        self.assertEqual(res["status"], "permission_denied")
        self.assertFalse(res["success"])
        self.client.get.assert_not_called()

    def test_allowed_role_runs(self):
        res = self.run_ok()
        self.assertEqual(res["status"], "success")
        self.client.get.assert_called_once()

    def test_missing_roles_deny(self):
        self.assert_denied_without_call(self.run_ok(user_roles=None))

    def test_empty_roles_deny(self):
        self.assert_denied_without_call(self.run_ok(user_roles=[]))

    def test_a_single_string_is_not_a_role_list(self):
        # "Stock User" iterated as characters must never be read as roles.
        self.assert_denied_without_call(self.run_ok(user_roles="Stock User"))

    def test_wrong_role_denied(self):
        self.assert_denied_without_call(self.run_ok(user_roles=["Sales User", "HR User"]))

    def test_non_string_roles_are_ignored(self):
        self.assert_denied_without_call(self.run_ok(user_roles=[None, 5, ["Stock User"]]))

    def test_roles_are_required_keyword(self):
        with self.assertRaises(TypeError):
            run_report("Stock Projected Qty", {"company": "Acme Co"}, client=self.client)

    def test_unknown_and_unlisted_reports_give_the_same_answer(self):
        unknown = run_report("No Such Report", {}, user_roles=["System Manager"], client=self.client)
        unlisted = run_report("Accounts Receivable", {}, user_roles=["System Manager"], client=self.client)
        self.assertEqual(unknown["status"], "permission_denied")
        self.assertEqual(unlisted["status"], "permission_denied")
        self.assertEqual(unknown["error"].replace("No Such Report", "X"), unlisted["error"].replace("Accounts Receivable", "X"))
        self.client.get.assert_not_called()

    def test_report_name_must_be_a_string(self):
        for bad in (None, 5, ["Stock Projected Qty"], {"a": 1}):
            self.assert_denied_without_call(run_report(bad, {}, user_roles=["System Manager"], client=self.client))

    def test_empty_allowed_roles_denies_everyone(self):
        entry = copy.deepcopy(REPORT_ALLOWLIST["Stock Projected Qty"])
        entry["allowed_roles"] = []
        with patch.dict(REPORT_ALLOWLIST, {"Stock Projected Qty": entry}):
            self.assert_denied_without_call(self.run_ok(user_roles=["System Manager", "Stock User"]))

    def test_missing_allowed_roles_key_denies_everyone(self):
        entry = copy.deepcopy(REPORT_ALLOWLIST["Stock Projected Qty"])
        del entry["allowed_roles"]
        with patch.dict(REPORT_ALLOWLIST, {"Stock Projected Qty": entry}):
            self.assert_denied_without_call(self.run_ok(user_roles=["System Manager"]))

    def test_role_check_precedes_filter_validation(self):
        res = run_report("Stock Projected Qty", {"nope": 1}, user_roles=["HR User"], client=self.client)
        self.assertEqual(res["status"], "permission_denied")

    def test_denied_result_has_the_base_envelope(self):
        res = self.run_ok(user_roles=None)
        self.assertEqual(set(res), {"status", "data", "error", "has_more", "success"})


class TestFilterValidation(ReportsTestBase):
    def assert_rejected(self, filters, text=None, report="Stock Projected Qty", roles=STOCK_ROLES):
        res = run_report(report, filters, user_roles=roles, client=self.client)
        self.assertEqual(res["status"], "system_error")
        if text:
            self.assertIn(text, res["error"])
        self.client.get.assert_not_called()

    def test_unknown_filter_rejected_not_passed_through(self):
        self.assert_rejected({"company": "Acme Co", "ignore_prepared": 1}, "Unsupported filter")

    def test_required_filter_missing(self):
        self.assert_rejected({}, "Missing required filter: company")
        self.assert_rejected({"company": ""}, "Missing required filter: company")
        self.assert_rejected(None, "Missing required filter: company")

    def test_filters_must_be_an_object(self):
        self.assert_rejected(["company"], "must be an object")

    def test_string_filter_rules(self):
        self.assert_rejected({"company": 5}, "Invalid value for filter company")
        self.assert_rejected({"company": "x" * 141}, "Invalid value for filter company")
        self.assert_rejected({"company": "bad\nvalue"}, "Invalid value for filter company")
        self.assert_rejected({"company": "   "}, "Invalid value for filter company")

    def test_date_filter_rules(self):
        for bad in ("2026-13-01", "01/02/2026", "2026-1-1x", 20260101, "' OR 1=1"):
            self.assert_rejected({**LEDGER_FILTERS, "from_date": bad}, "Invalid date", report="Stock Ledger")

    def test_choice_filter_rules(self):
        self.assert_rejected({**SALES_FILTERS, "tree_type": "Everything"}, "Invalid value for filter tree_type",
                             report="Sales Analytics", roles=["Sales User"])

    def test_clean_filters_are_sent_as_json(self):
        run_report("Stock Ledger", {**LEDGER_FILTERS, "item_code": "  ITEM-1  "}, user_roles=STOCK_ROLES, client=self.client)
        args, kwargs = self.client.get.call_args
        self.assertEqual(args[0], "/api/method/frappe.desk.query_report.run")
        self.assertEqual(kwargs["params"]["report_name"], "Stock Ledger")
        self.assertEqual(json.loads(kwargs["params"]["filters"]), {**LEDGER_FILTERS, "item_code": "ITEM-1"})
        self.assertEqual(kwargs["timeout"], reports.REPORT_TIMEOUT_SECONDS)

    def test_optional_filter_may_be_omitted(self):
        self.assertEqual(self.run_ok()["status"], "success")


class TestResponseHandling(ReportsTestBase):
    def test_success_shape(self):
        res = self.run_ok()
        self.assertEqual(set(res), {"status", "data", "error", "has_more", "success",
                                    "total_count", "offset", "limit", "truncated"})
        data = res["data"]
        self.assertEqual(data["report"], "Stock Projected Qty")
        self.assertEqual([c["fieldname"] for c in data["columns"]], ["item_code", "warehouse", "qty"])
        self.assertEqual(len(data["rows"]), 4)
        self.assertIsNone(data["totals"])
        self.assertEqual(res["total_count"], 4)
        self.assertFalse(res["truncated"])

    def test_total_row_is_separated_from_data(self):
        rows = copy.deepcopy(ROWS) + [["Total", "", 40.5]]
        self.client.get.return_value = _response(200, _report(rows=rows, add_total_row=True))
        res = self.run_ok()
        self.assertEqual(len(res["data"]["rows"]), 4)
        self.assertEqual(res["data"]["totals"], {"item_code": "Total", "warehouse": "", "qty": 40.5})
        self.assertEqual(res["total_count"], 4)

    def test_total_row_never_feeds_an_aggregation(self):
        rows = copy.deepcopy(ROWS) + [["Total", "", 40.5]]
        self.client.get.return_value = _response(200, _report(rows=rows, add_total_row=True))
        res = self.run_ok(group_by="item_code", metric="qty", aggregation_fn="sum")
        self.assertEqual({r["item_code"]: r["qty"] for r in res["data"]["rows"]}, {"A": 8.0, "B": 20.0, "C": 12.5})

    def test_list_rows_are_zipped_to_columns_when_no_total_flag(self):
        self.client.get.return_value = _response(200, _report(rows=[["A", "W1", 5.0], {"item_code": "B", "warehouse": "W1", "qty": 1}]))
        res = self.run_ok()
        self.assertEqual(res["data"]["rows"][0], {"item_code": "A", "warehouse": "W1", "qty": 5.0})
        self.assertEqual(len(res["data"]["rows"]), 2)

    def test_string_column_definitions_are_supported(self):
        cols = ["Item:Link/Item:100", "Qty:Float:80"]
        self.client.get.return_value = _response(200, _report(rows=[["A", 5.0]], columns=cols))
        res = self.run_ok()
        self.assertEqual(res["data"]["rows"], [{"Item": "A", "Qty": 5.0}])

    def test_empty_report(self):
        self.client.get.return_value = _response(200, _report(rows=[]))
        res = self.run_ok()
        self.assertEqual(res["status"], "empty")
        self.assertTrue(res["success"])
        self.assertEqual(res["data"]["rows"], [])

    def test_prepared_report_is_reported_clearly(self):
        self.client.get.return_value = _response(200, {"prepared_report": True, "doc": {"name": "x"}, "add_total_row": True})
        res = self.run_ok()
        self.assertEqual(res["status"], "system_error")
        self.assertIn("prepared report", res["error"])

    def test_error_statuses(self):
        cases = [(404, "not_found"), (401, "permission_denied"), (403, "permission_denied"),
                 (417, "system_error"), (500, "system_error")]
        for code, expected in cases:
            self.client.get.return_value = _response(code, {"exception": "boom"} if code == 417 else None, text="boom")
            self.assertEqual(self.run_ok()["status"], expected, code)

    def test_transport_error(self):
        self.client.get.side_effect = httpx.ConnectError("down")
        res = self.run_ok()
        self.assertEqual(res["status"], "system_error")
        self.assertIn("down", res["error"])

    def test_malformed_responses(self):
        bad = MagicMock(spec=httpx.Response, status_code=200, is_success=True, text="x")
        bad.json.side_effect = ValueError("Non-JSON")
        self.client.get.return_value = bad
        self.assertEqual(self.run_ok()["status"], "system_error")
        for message in (None, "text", [], {"columns": "x", "result": []}, {"columns": [], "result": "x"}, {}):
            self.client.get.return_value = _response(200, message)
            self.assertEqual(self.run_ok()["status"], "system_error", message)

    def test_report_runs_once_with_no_retry(self):
        self.client.get.return_value = _response(500, text="boom")
        self.run_ok()
        self.assertEqual(self.client.get.call_count, 1)


class TestPostProcessing(ReportsTestBase):
    def test_row_filter_operators(self):
        cases = [
            ([["warehouse", "=", "W1"]], {"A", "B"}, 2),
            ([["warehouse", "!=", "W1"]], {"C", "A"}, 2),
            ([["qty", ">", 5]], {"B", "C"}, 2),
            ([["qty", ">=", 5]], {"A", "B", "C"}, 3),
            ([["qty", "<", 5]], {"A"}, 1),
            ([["qty", "<=", 5]], {"A"}, 2),
            ([["item_code", "contains", "a"]], {"A"}, 2),
            ([["qty", ">", 4], ["warehouse", "=", "W2"]], {"C"}, 1),
        ]
        for flt, items, count in cases:
            res = self.run_ok(row_filters=flt)
            self.assertEqual({r["item_code"] for r in res["data"]["rows"]}, items, flt)
            self.assertEqual(len(res["data"]["rows"]), count, flt)

    def test_row_filter_validation(self):
        bad_filters = [
            [["nope", "=", 1]],
            [["qty", "LIKE", 1]],
            [["qty", "; DROP TABLE x", 1]],
            [["qty", "="]],
            ["qty = 1"],
            [["qty", "=", [1, 2]]],
            "qty > 1",
            [["qty", "=", 1]] * (reports.MAX_ROW_FILTERS + 1),
        ]
        for bad in bad_filters:
            res = self.run_ok(row_filters=bad)
            self.assertEqual(res["status"], "system_error", bad)

    def test_aggregation(self):
        res = self.run_ok(group_by="warehouse", metric="qty", aggregation_fn="sum")
        self.assertEqual(res["data"]["rows"], [{"warehouse": "W1", "qty": 25.0}, {"warehouse": "W2", "qty": 15.5}])
        self.assertEqual([c["fieldname"] for c in res["data"]["columns"]], ["warehouse", "qty"])
        self.assertIsNone(res["data"]["totals"])

    def test_count_does_not_need_a_metric(self):
        res = self.run_ok(group_by="warehouse", aggregation_fn="count")
        self.assertEqual(res["data"]["rows"], [{"warehouse": "W1", "count": 2}, {"warehouse": "W2", "count": 2}])

    def test_aggregation_runs_after_row_filters(self):
        res = self.run_ok(row_filters=[["qty", ">", 4]], group_by="item_code", metric="qty", aggregation_fn="max")
        self.assertEqual({r["item_code"]: r["qty"] for r in res["data"]["rows"]}, {"A": 5.0, "B": 20.0, "C": 12.5})

    def test_aggregation_validation(self):
        bad_calls = [
            dict(group_by="warehouse", metric="qty", aggregation_fn="median"),
            dict(group_by="warehouse", metric="qty"),
            dict(metric="qty", aggregation_fn="sum"),
            dict(group_by="warehouse", aggregation_fn="sum"),
            dict(group_by="ware house", metric="qty", aggregation_fn="sum"),
            dict(group_by="nope", metric="qty", aggregation_fn="sum"),
            dict(group_by="warehouse", metric="nope", aggregation_fn="sum"),
            dict(group_by="warehouse", metric="item_code", aggregation_fn="sum"),
        ]
        for kwargs in bad_calls:
            res = self.run_ok(**kwargs)
            self.assertEqual(res["status"], "system_error", kwargs)

    def test_aggregation_argument_errors_do_not_run_the_report(self):
        self.run_ok(group_by="warehouse", metric="qty", aggregation_fn="median")
        self.run_ok(group_by="warehouse", metric="qty")
        self.client.get.assert_not_called()

    def test_sort_numeric_and_text(self):
        desc = self.run_ok(sort="qty desc")
        self.assertEqual([r["qty"] for r in desc["data"]["rows"]], [20.0, 12.5, 5.0, 3.0])
        asc = self.run_ok(sort="qty")
        self.assertEqual([r["qty"] for r in asc["data"]["rows"]], [3.0, 5.0, 12.5, 20.0])
        text = self.run_ok(sort="item_code desc")
        self.assertEqual([r["item_code"] for r in text["data"]["rows"]][0], "C")

    def test_rows_without_a_sort_value_come_last(self):
        rows = copy.deepcopy(ROWS) + [{"item_code": "Z", "warehouse": "W9", "qty": None}]
        self.client.get.return_value = _response(200, _report(rows=rows))
        res = self.run_ok(sort="qty desc")
        self.assertEqual(res["data"]["rows"][-1]["item_code"], "Z")

    def test_sort_validation(self):
        for bad in ("qty sideways", "qty desc extra", "nope desc"):
            self.assertEqual(self.run_ok(sort=bad)["status"], "system_error", bad)

    def test_malformed_sort_does_not_run_the_report(self):
        self.run_ok(sort="qty sideways")
        self.client.get.assert_not_called()

    def test_sort_can_use_aggregated_columns(self):
        res = self.run_ok(group_by="warehouse", metric="qty", aggregation_fn="sum", sort="qty desc")
        self.assertEqual([r["warehouse"] for r in res["data"]["rows"]], ["W1", "W2"])
        self.assertEqual(self.run_ok(group_by="warehouse", metric="qty", aggregation_fn="sum", sort="item_code")["status"],
                         "system_error")


class TestLimits(ReportsTestBase):
    def with_max_rows(self, value):
        entry = copy.deepcopy(REPORT_ALLOWLIST["Stock Projected Qty"])
        entry["max_rows"] = value
        return patch.dict(REPORT_ALLOWLIST, {"Stock Projected Qty": entry})

    def test_rows_are_capped_and_flagged(self):
        with self.with_max_rows(3):
            res = self.run_ok()
        self.assertEqual(len(res["data"]["rows"]), 3)
        self.assertTrue(res["truncated"])
        self.assertTrue(res["has_more"])
        self.assertEqual(res["total_count"], 4)
        self.assertEqual(res["limit"], 3)

    def test_a_caller_limit_cannot_exceed_the_report_cap(self):
        with self.with_max_rows(3):
            res = self.run_ok(limit=100)
        self.assertEqual(len(res["data"]["rows"]), 3)

    def test_a_smaller_caller_limit_applies(self):
        res = self.run_ok(limit=2)
        self.assertEqual(len(res["data"]["rows"]), 2)
        self.assertTrue(res["truncated"])

    def test_cap_applies_after_sorting(self):
        res = self.run_ok(sort="qty desc", limit=2)
        self.assertEqual([r["qty"] for r in res["data"]["rows"]], [20.0, 12.5])

    def test_the_hard_ceiling_beats_a_larger_report_cap(self):
        rows = [{"item_code": f"I{i}", "warehouse": "W", "qty": 1} for i in range(HARD_MAX_ROWS + 50)]
        self.client.get.return_value = _response(200, _report(rows=rows))
        with self.with_max_rows(HARD_MAX_ROWS * 10):
            res = self.run_ok()
        self.assertEqual(len(res["data"]["rows"]), HARD_MAX_ROWS)
        self.assertTrue(res["truncated"])

    def test_invalid_limit(self):
        for bad in (0, -1, "5", True, 2.5):
            self.assertEqual(self.run_ok(limit=bad)["status"], "system_error", bad)


class TestAllowlistConfiguration(unittest.TestCase):
    """Guards against a careless edit to the allowlist."""

    def test_every_entry_names_roles_a_cap_and_valid_filter_types(self):
        for name, spec in REPORT_ALLOWLIST.items():
            self.assertTrue(spec["allowed_roles"], f"{name} must name at least one role")
            self.assertTrue(all(isinstance(r, str) and r for r in spec["allowed_roles"]), name)
            self.assertGreater(spec["max_rows"], 0, name)
            for fname, rule in spec["filters"].items():
                self.assertIn(rule["type"], ("str", "date", "int", "choice"), f"{name}.{fname}")
                if rule["type"] == "choice":
                    self.assertTrue(rule["choices"], f"{name}.{fname}")

    def test_no_financial_reports_are_listed(self):
        financial = {"Accounts Receivable", "Accounts Payable", "Sales Register", "Purchase Register",
                     "Item-wise Sales Register", "General Ledger", "Trial Balance", "Balance Sheet",
                     "Profit and Loss Statement", "Cash Flow", "Accounts Receivable Summary"}
        self.assertEqual(financial & set(REPORT_ALLOWLIST), set())

    def test_the_allowlist_can_be_replaced_through_the_seam(self):
        client = MagicMock()
        client.get.return_value = _response(200, _report())
        custom = {"Custom Report": {"allowed_roles": ["Analyst"], "max_rows": 10, "filters": {}}}
        with patch.object(reports, "get_report_allowlist", return_value=custom):
            ok = run_report("Custom Report", {}, user_roles=["Analyst"], client=client)
            denied = run_report("Stock Projected Qty", {"company": "x"}, user_roles=["System Manager"], client=client)
        self.assertEqual(ok["status"], "success")
        self.assertEqual(denied["status"], "permission_denied")


if __name__ == "__main__":
    unittest.main()
