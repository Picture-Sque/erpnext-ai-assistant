"""
agent/tests/test_skill_files.py

Contract checks for every skills/*/SKILL.md file. They hold each skill to what the
pipeline actually reads, so a skill that is malformed, references a field it does not
declare, or has keywords that route other skills' requests to it fails here instead of
misbehaving at runtime. Offline: no LLM and no ERPNext.
"""

import os
import re
import string
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import workflow.graph as graph

SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills"))
SKILL_DIRS = sorted(d for d in os.listdir(SKILLS_DIR) if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md")))

READ_TOOLS = {"get_list", "get_document", "aggregate"}
WRITE_TOOLS = {"create_document", "update_document", "cancel_document", "submit_document"}
REQUIRED_KEYS = (
    "name", "intent", "description", "allowed_roles", "tool", "doctype", "required_fields",
    "optional_fields", "defaults", "validation_rules", "keywords", "response_template",
    "error_template", "examples",
)
# Every key the loader or the pipeline reads. Anything else is a typo or dead config.
KNOWN_KEYS = set(REQUIRED_KEYS) | {
    "query_parameters", "payload_structure", "not_found_message", "follow_up_eligible",
    "follow_up_slots", "context_pronouns", "chain_exports", "field_values", "outputs", "instructions",
}


def _frontmatter(skill_dir):
    with open(os.path.join(SKILLS_DIR, skill_dir, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n") or text.startswith("---\r\n"), "SKILL.md must start with a '---' line"
    parts = text.split("---")
    assert len(parts) >= 3, "frontmatter must be closed by a second '---' line"
    data = yaml.safe_load(parts[1])
    assert isinstance(data, dict), "frontmatter must be a YAML mapping"
    return data, parts[2]


def _placeholders(template):
    if not isinstance(template, str):
        return set()
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


def _filter_placeholders(flt):
    return set(re.findall(r"\{(\w+)\}", str(flt[-1]))) if isinstance(flt, list) else set()


@pytest.fixture(params=SKILL_DIRS)
def skill(request):
    data, body = _frontmatter(request.param)
    return request.param, data, body


def test_frontmatter_shape(skill):
    folder, data, body = skill
    missing = [k for k in REQUIRED_KEYS if k not in data]
    assert not missing, f"{folder}: missing keys {missing}"
    unknown = sorted(set(data) - KNOWN_KEYS)
    assert not unknown, f"{folder}: unknown keys {unknown}"
    assert data["name"] == data["intent"] == folder, f"{folder}: name, intent and folder must match"
    assert data["tool"] in READ_TOOLS | WRITE_TOOLS, f"{folder}: unknown tool {data['tool']}"
    assert data["doctype"], f"{folder}: doctype is empty"
    assert data["allowed_roles"], f"{folder}: allowed_roles is empty"
    assert data["examples"] and data["keywords"], f"{folder}: needs examples and keywords"
    assert body.strip().startswith("# "), f"{folder}: body must start with a '# Title' heading"
    for section in ("## When to use", "## ERPNext details", "## Tool"):
        assert section in body, f"{folder}: body is missing the '{section}' section"
    for key in ("required_fields", "optional_fields", "keywords", "examples", "allowed_roles", "defaults", "validation_rules"):
        assert isinstance(data[key], list), f"{folder}: {key} must be a list"
    assert len(set(k.lower() for k in data["keywords"])) == len(data["keywords"]), f"{folder}: duplicate keywords"


def test_fields_are_declared_where_they_are_used(skill):
    folder, data, _ = skill
    required, optional = set(data["required_fields"]), set(data["optional_fields"])
    declared = required | optional
    assert not required & optional, f"{folder}: fields both required and optional: {required & optional}"

    for rule in data["defaults"]:
        assert rule.get("field") in declared, f"{folder}: default for undeclared field {rule.get('field')}"
    for rule in data["validation_rules"]:
        refs = {rule.get("field"), rule.get("must_be_after"), rule.get("must_be_before")} - {None}
        assert refs <= declared, f"{folder}: validation rule references undeclared {refs - declared}"
    for slot in data.get("follow_up_slots") or []:
        assert slot in declared, f"{folder}: follow_up_slot '{slot}' is not a declared field"
    if data.get("follow_up_eligible"):
        assert data["tool"] in READ_TOOLS, f"{folder}: only read skills can be follow-up eligible"
        assert data.get("follow_up_slots"), f"{folder}: follow_up_eligible needs follow_up_slots"

    for field, allowed in (data.get("field_values") or {}).items():
        assert field in declared, f"{folder}: field_values for undeclared field '{field}'"
        assert isinstance(allowed, list) and allowed, f"{folder}: field_values['{field}'] must be a non-empty list"
        for rule in data["defaults"]:
            if rule.get("field") == field:
                assert rule.get("value") in allowed, f"{folder}: default {rule.get('value')!r} for '{field}' is not allowed"


def test_queries_only_use_fields_that_will_be_filled(skill):
    folder, data, _ = skill
    if data["tool"] != "get_list":
        return
    q = data.get("query_parameters") or {}
    assert q.get("fields"), f"{folder}: get_list skill needs query_parameters.fields"
    required, optional = set(data["required_fields"]), set(data["optional_fields"])
    defaulted = {r["field"] for r in data["defaults"]}
    for flt in q.get("filters") or []:
        # A placeholder left unfilled would be sent to ERPNext literally, e.g. "{item_code}".
        unfilled = _filter_placeholders(flt) - required - defaulted
        assert not unfilled, f"{folder}: filter {flt} uses {unfilled}, which is neither required nor defaulted"
    filtered = set().union(*[_filter_placeholders(f) for f in q.get("filters") or []])
    for opt in q.get("optional_filters") or []:
        assert opt.get("field") in optional | required, f"{folder}: optional filter on undeclared field {opt.get('field')}"
        assert isinstance(opt.get("filter"), list) and len(opt["filter"]) in (3, 4), f"{folder}: malformed optional filter {opt}"
        assert _filter_placeholders(opt.get("filter")) <= {opt["field"]}, f"{folder}: optional filter {opt} uses another field"
        filtered.add(opt["field"])
    # An optional field nothing reads makes the LLM extract a value that is then ignored.
    unused = optional - filtered
    assert not unused, f"{folder}: optional fields {unused} are not used by any filter"


def test_get_document_skills_take_only_a_record_name(skill):
    folder, data, _ = skill
    if data["tool"] == "get_document":
        assert data["required_fields"] == ["name"] and not data["optional_fields"],             f"{folder}: get_document fetches one record by name; other fields are never used"


def test_payload_covers_required_fields(skill):
    folder, data, _ = skill
    if data["tool"] != "create_document":
        return
    mappings = (data.get("payload_structure") or {}).get("field_mappings") or {}
    declared = set(data["required_fields"]) | set(data["optional_fields"])
    assert set(data["required_fields"]) <= set(mappings.values()), f"{folder}: a required field is not mapped into the payload"
    assert set(mappings.values()) <= declared, f"{folder}: payload maps undeclared fields"


def test_templates_reference_known_values(skill):
    folder, data, _ = skill
    declared = set(data["required_fields"]) | set(data["optional_fields"])
    if data["tool"] in WRITE_TOOLS:
        known = declared | {"name"}
    else:
        known = declared | set((data.get("query_parameters") or {}).get("fields") or []) | set(data.get("outputs") or [])
    for key in ("response_template", "not_found_message"):
        unknown = _placeholders(data.get(key)) - known
        assert not unknown, f"{folder}: {key} uses unknown placeholders {unknown}"
    assert _placeholders(data["error_template"]) <= {"error"}, f"{folder}: error_template may only use {{error}}"


# ---------------------------------------------------------------------------
# Keyword routing across skills
# ---------------------------------------------------------------------------

def test_no_keyword_is_shared_by_two_skills():
    owners = {}
    for folder in SKILL_DIRS:
        data, _ = _frontmatter(folder)
        for kw in data["keywords"]:
            owners.setdefault(kw.lower(), set()).add(folder)
    shared = {k: v for k, v in owners.items() if len(v) > 1}
    assert not shared, f"keywords claimed by more than one skill: {shared}"


def _examples():
    for folder in SKILL_DIRS:
        data, _ = _frontmatter(folder)
        for ex in data["examples"]:
            yield folder, ex


@pytest.mark.parametrize("owner,example", list(_examples()))
def test_examples_never_keyword_route_to_another_skill(owner, example):
    """An example either routes to its own skill by keyword, or reaches the LLM classifier."""
    routed = graph.match_skill_keyword(example)
    assert routed in (None, owner), f"{example!r} (example of {owner}) is keyword-routed to {routed}"


# Requests that read similar but belong to different skills. None = no keyword decides;
# the LLM classifier, which sees every skill description, makes the call.
ROUTING_PROBES = [
    ("what is the status of purchase order PUR-ORD-2026-00013", "purchase-order-lookup"),
    ("cancel order PUR-ORD-2026-00013", None),
    ("submit order PUR-ORD-2026-00027", None),
    ("show me sales order SAL-ORD-2026-00037", "sales-order-lookup"),
    ("how many sales orders are in draft", None),
    ("how much revenue did we make in august", None),
    ("what did we sell last month", None),
    ("show me all orders from september", None),
    ("details of SAL-ORD-2026-00037", None),
    ("update the delivery date of SAL-ORD-2026-00037 to 2026-11-01", None),
    ("update sales order SAL-ORD-2026-00037 delivery date to 2026-11-01", "update-sales-order"),
    ("is there a purchase order for ITEM-DESK-001", "purchase-order-lookup"),
    ("how many units of ITEM-DESK-001 sold", None),
    ("change schedule date of PUR-ORD-2026-00013 to 2026-11-01", "update-purchase-order"),
    ("check stock for ITEM-DESK-001", "check-inventory"),
    ("what is low on stock", "low-stock-report"),
    ("who is our top customer", "sales-analytics-report"),
    ("look up customer Apex Logistics Global", "customer-lookup"),
]


@pytest.mark.parametrize("message,expected", ROUTING_PROBES)
def test_ambiguous_phrases_are_not_misrouted_by_keywords(message, expected):
    assert graph.match_skill_keyword(message) == expected
