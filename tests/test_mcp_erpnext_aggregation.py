import os
import sys
import unittest

root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from mcp_erpnext.aggregation import AGGREGATION_FUNCTIONS, aggregate_rows, group_key

ROWS = [
    {"customer": "Acme", "total": 100},
    {"customer": "Acme", "total": 300},
    {"customer": "Beta", "total": 50},
    {"customer": None, "total": 7},
]


class TestAggregateRows(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(aggregate_rows(ROWS, "customer", "total", "sum"),
                         {"Acme": 400.0, "Beta": 50.0, "Unknown": 7.0})

    def test_avg(self):
        self.assertEqual(aggregate_rows(ROWS, "customer", "total", "avg")["Acme"], 200.0)

    def test_max_and_min(self):
        self.assertEqual(aggregate_rows(ROWS, "customer", "total", "max")["Acme"], 300.0)
        self.assertEqual(aggregate_rows(ROWS, "customer", "total", "min")["Acme"], 100.0)

    def test_count_counts_rows_and_ignores_metric(self):
        result = aggregate_rows(ROWS, "customer", None, "count")
        self.assertEqual(result, {"Acme": 2, "Beta": 1, "Unknown": 1})
        self.assertIsInstance(result["Acme"], int)

    def test_first_seen_order_is_kept(self):
        self.assertEqual(list(aggregate_rows(ROWS, "customer", "total", "sum")), ["Acme", "Beta", "Unknown"])

    def test_null_and_empty_metric_count_as_zero(self):
        rows = [{"g": "a", "m": None}, {"g": "a", "m": ""}, {"g": "a", "m": 5}, {"g": "b"}]
        self.assertEqual(aggregate_rows(rows, "g", "m", "sum"), {"a": 5.0, "b": 0.0})

    def test_non_numeric_metric_raises(self):
        with self.assertRaises(ValueError) as ctx:
            aggregate_rows([{"g": "a", "m": "abc"}], "g", "m", "sum")
        self.assertIn("non-numeric", str(ctx.exception))

    def test_unknown_function_raises(self):
        with self.assertRaises(ValueError):
            aggregate_rows(ROWS, "customer", "total", "median")

    def test_empty_rows(self):
        self.assertEqual(aggregate_rows([], "customer", "total", "sum"), {})

    def test_group_key(self):
        self.assertEqual(group_key(None), "Unknown")
        self.assertEqual(group_key(12), "12")

    def test_function_set_is_fixed(self):
        self.assertEqual(set(AGGREGATION_FUNCTIONS), {"sum", "avg", "count", "max", "min"})


if __name__ == "__main__":
    unittest.main()
