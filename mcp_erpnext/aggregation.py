"""
Row-level aggregation shared by the tool layer.

`aggregate_rows` groups a list of row dicts and applies one of a fixed set of functions.
It is used by the Python fallback in `crud.aggregate` and by `reports.run_report`.
Nothing here talks to ERPNext.
"""

from typing import Any, Dict, Iterable, Optional

# Aggregation functions callers may ask for, mapped to the SQL function ERPNext evaluates
# when the aggregation runs in the database.
AGGREGATION_FUNCTIONS = {"sum": "sum", "avg": "avg", "count": "count", "max": "max", "min": "min"}

UNKNOWN_GROUP = "Unknown"


def group_key(value: Any) -> str:
    """The key a group value is reported under. Missing and null values share one group."""
    return UNKNOWN_GROUP if value is None else str(value)


def aggregate_rows(
    rows: Iterable[dict],
    group_by: str,
    metric: Optional[str],
    aggregation_fn: str,
) -> Dict[str, Any]:
    """
    Group `rows` by `group_by` and aggregate `metric` with `aggregation_fn`.

    Returns {group key: value} in first-seen order. `count` counts rows and ignores `metric`.
    Null and empty metric values count as 0. Raises ValueError for an unknown function or a
    metric value that is not a number.
    """
    if aggregation_fn not in AGGREGATION_FUNCTIONS:
        raise ValueError(f"Unsupported aggregation_fn: {aggregation_fn!r}.")

    agg: Dict[str, Any] = {}
    counts: Dict[str, int] = {}
    for row in rows:
        key = group_key(row.get(group_by))
        if aggregation_fn == "count":
            value: Any = 1
        else:
            try:
                value = float(row.get(metric) or 0)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Field '{metric}' has a non-numeric value and cannot be aggregated with '{aggregation_fn}'."
                ) from None

        if key not in agg:
            agg[key] = 0 if aggregation_fn in ("sum", "avg", "count") else value
            counts[key] = 0

        counts[key] += 1
        if aggregation_fn in ("sum", "avg", "count"):
            agg[key] += value
        elif aggregation_fn == "max":
            agg[key] = max(agg[key], value)
        elif aggregation_fn == "min":
            agg[key] = min(agg[key], value)

    if aggregation_fn == "avg":
        for key in agg:
            agg[key] = agg[key] / counts[key]
    return agg
