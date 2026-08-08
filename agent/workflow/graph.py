import os
import logging
import re
import json
import yaml
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END

from llm import invoke_structured_llm, invoke_llm
from workflow.state import AgentState
from tools.generic_tools import add_doctype, list_doctype, update_doctype, delete_doctype, is_doctype_allowed, get_allowed_doctypes

logger = logging.getLogger("workflow_graph")
logger.setLevel(logging.INFO)


# Dynamic Declarative Skill Loader
def load_skills() -> List[Dict[str, Any]]:
    skills_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills"))
    skills = []
    if os.path.exists(skills_dir):
        for folder in os.listdir(skills_dir):
            skill_md = os.path.join(skills_dir, folder, "SKILL.md")
            if os.path.exists(skill_md):
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()
                        parts = content.split("---")
                        if len(parts) >= 3:
                            frontmatter_str = parts[1]
                            try:
                                data = yaml.safe_load(frontmatter_str)
                                if not isinstance(data, dict):
                                    data = {}
                            except Exception as ex:
                                logger.warning(f"YAML parsing failed for frontmatter in {skill_md}: {ex}")
                                data = {}
                            
                            name = data.get("name") or data.get("intent") or folder
                            desc = data.get("description", "")
                            inst = data.get("instructions") or parts[2].strip()
                            
                            if name and desc:
                                skills.append({
                                    "name": name,
                                    "intent": data.get("intent", name),
                                    "description": desc,
                                    "instructions": inst,
                                    "allowed_roles": data.get("allowed_roles") or data.get("roles", []),
                                    "tool": data.get("tool", "list_doctype"),
                                    "doctype": data.get("doctype", ""),
                                    "required_fields": data.get("required_fields", []),
                                    "optional_fields": data.get("optional_fields", []),
                                    "query_parameters": data.get("query_parameters", {}),
                                    "payload_structure": data.get("payload_structure", {}),
                                    "defaults": data.get("defaults", []),
                                    "validation_rules": data.get("validation_rules", []),
                                    "keywords": data.get("keywords", []),
                                    "context_pronouns": data.get("context_pronouns", {}),
                                    "response_template": data.get("response_template", ""),
                                    "not_found_message": data.get("not_found_message", ""),
                                    "error_template": data.get("error_template", ""),
                                    "examples": data.get("examples", [])
                                })
                except Exception as e:
                    logger.warning(f"Failed to parse skill {skill_md}: {e}")
                    
    return skills


loaded_skills = load_skills()


def apply_skill_defaults(fields: dict, defaults: list) -> dict:
    updated_fields = dict(fields)
    for rule in defaults:
        field = rule.get("field")
        value_expr = rule.get("value")
        if not field or not value_expr:
            continue
        
        # Only apply default if field is not present or is empty/None
        if updated_fields.get(field) is None or str(updated_fields.get(field)).strip() == "":
            match = re.match(r"^\s*(\w+)\s*([\+\-])\s*(\d+)d\s*$", str(value_expr))
            if match:
                base_field = match.group(1)
                op = match.group(2)
                days_offset = int(match.group(3))
                
                base_val = None
                if base_field == "today":
                    base_val = datetime.now().strftime("%Y-%m-%d")
                else:
                    base_val = updated_fields.get(base_field)
                
                if base_val:
                    try:
                        base_date = datetime.strptime(str(base_val).strip(), "%Y-%m-%d").date()
                        if op == "+":
                            new_date = base_date + timedelta(days=days_offset)
                        else:
                            new_date = base_date - timedelta(days=days_offset)
                        updated_fields[field] = new_date.strftime("%Y-%m-%d")
                    except Exception as e:
                        logger.warning(f"Error evaluating date math '{value_expr}' for field '{field}': {e}")
            elif value_expr == "today":
                updated_fields[field] = datetime.now().strftime("%Y-%m-%d")
            else:
                updated_fields[field] = value_expr
    return updated_fields


def evaluate_validation_rules(collected: dict, rules: list) -> Tuple[bool, List[str]]:
    all_required_filled = True
    missing_parameters = []
    
    for rule in rules:
        field = rule.get("field")
        if not field:
            continue
            
        val1 = collected.get(field)
        if val1 is None or str(val1).strip() == "":
            continue
            
        is_val1_date = False
        date1 = None
        try:
            date1 = datetime.strptime(str(val1).strip(), "%Y-%m-%d").date()
            is_val1_date = True
        except ValueError:
            pass
            
        # must_be_after
        if "must_be_after" in rule:
            other_field = rule["must_be_after"]
            val2 = collected.get(other_field)
            if val2 is not None and str(val2).strip() != "":
                if is_val1_date:
                    try:
                        date2 = datetime.strptime(str(val2).strip(), "%Y-%m-%d").date()
                        if date1 <= date2:
                            logger.warning(f"Validation failed: {field} ({val1}) <= {other_field} ({val2})")
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be after ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                    except ValueError:
                        pass
                else:
                    try:
                        if not (float(val1) > float(val2)):
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be after ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                    except (ValueError, TypeError):
                        if not (str(val1) > str(val2)):
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be after ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                                
        # must_be_before
        elif "must_be_before" in rule:
            other_field = rule["must_be_before"]
            val2 = collected.get(other_field)
            if val2 is not None and str(val2).strip() != "":
                if is_val1_date:
                    try:
                        date2 = datetime.strptime(str(val2).strip(), "%Y-%m-%d").date()
                        if date1 >= date2:
                            logger.warning(f"Validation failed: {field} ({val1}) >= {other_field} ({val2})")
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be before ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                    except ValueError:
                        pass
                else:
                    try:
                        if not (float(val1) < float(val2)):
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be before ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                    except ValueError:
                        if not (str(val1) < str(val2)):
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must be before ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)

        # must_equal
        elif "must_equal" in rule:
            other_field = rule["must_equal"]
            val2 = collected.get(other_field)
            if val2 is not None and str(val2).strip() != "":
                if is_val1_date:
                    try:
                        date2 = datetime.strptime(str(val2).strip(), "%Y-%m-%d").date()
                        if date1 != date2:
                            logger.warning(f"Validation failed: {field} ({val1}) != {other_field} ({val2})")
                            collected.pop(field, None)
                            all_required_filled = False
                            msg = f"{field} ({rule.get('on_fail', 'must equal ' + other_field)})"
                            if msg not in missing_parameters:
                                missing_parameters.append(msg)
                    except ValueError:
                        pass
                else:
                    if str(val1) != str(val2):
                        logger.warning(f"Validation failed: {field} ({val1}) != {other_field} ({val2})")
                        collected.pop(field, None)
                        all_required_filled = False
                        msg = f"{field} ({rule.get('on_fail', 'must equal ' + other_field)})"
                        if msg not in missing_parameters:
                            missing_parameters.append(msg)
                            
    return all_required_filled, missing_parameters


# Declarative Heuristic Parameter Extractor (Driven by Skill Schema)
def _extract_fields_from_schema(skill: dict, last_msg: str, current_fields: dict, history_text: str = "") -> dict:
    fields = dict(current_fields)
    cleaned = last_msg.strip()
    
    # 1. Context pronoun memory resolution from skill definition
    cp = skill.get("context_pronouns", {})
    if cp:
        entity_field = cp.get("entity_field")
        triggers = cp.get("triggers", [])
        if entity_field and not fields.get(entity_field):
            if any(t in last_msg.lower() for t in triggers):
                if "code" in entity_field or entity_field in ("item_code", "item"):
                    # For code/SKU fields: try current message first, then history
                    m_code = re.search(r"\b(SKU\w+|INVALID_\w+)\b", cleaned, re.IGNORECASE)
                    if m_code:
                        fields[entity_field] = m_code.group(1).strip()
                    else:
                        m_sku_hist = re.search(r"\b(SKU\w+)\b", history_text, re.IGNORECASE)
                        if m_sku_hist:
                            fields[entity_field] = m_sku_hist.group(1).strip()
                else:
                    # For name fields (customer_name etc): look for entity name in history
                    m_hist = re.search(r"(?:look up customer|lookup customer|find customer|customer details|details for|about|for client|for customer|customer:?|client:?)\s+([A-Za-z0-9][\w\s\-\.]+?)(?:\s*\.|\s*\?|\s*\n|$|\s+what\b|\s+how\b|\s+who\b|\s+with\b|\s+details\b)", history_text, re.IGNORECASE)
                    if m_hist:
                        extracted = m_hist.group(1).strip()
                        extracted = re.sub(r"^(?:customer|client)\s+", "", extracted, flags=re.IGNORECASE).strip()
                        fields[entity_field] = extracted
                    else:
                        m_sku = re.search(r"\b(SKU\w+)\b", history_text, re.IGNORECASE)
                        if m_sku:
                            fields[entity_field] = m_sku.group(1).strip()
                        
    # 2. Schema-driven entity matching for required / optional fields
    req_fields = skill.get("required_fields", [])
    
    # Check for items list extraction
    if "items" in req_fields and not fields.get("items"):
        item_warehouse = "Stores - LS"
        if skill.get("payload_structure", {}).get("item_defaults", {}).get("warehouse"):
            item_warehouse = skill["payload_structure"]["item_defaults"]["warehouse"]
            
        match_c = re.search(r"\b(SKU\w+)\b.*?(\d+(?:\.\d+)?)", cleaned, re.IGNORECASE)
        if match_c:
            fields["items"] = [{
                "item_code": match_c.group(1).strip(),
                "qty": float(match_c.group(2)),
                "warehouse": item_warehouse
            }]
        else:
            match_rev = re.search(r"(\d+(?:\.\d+)?)\s+(?:units?\s+of\s+)?(SKU\w+)", cleaned, re.IGNORECASE)
            if match_rev:
                fields["items"] = [{
                    "item_code": match_rev.group(2).strip(),
                    "qty": float(match_rev.group(1)),
                    "warehouse": item_warehouse
                }]
            else:
                sku_only = re.search(r"\b(SKU\w+)\b", cleaned, re.IGNORECASE)
                if sku_only:
                    fields["_partial_item_code"] = sku_only.group(1).strip()
                elif fields.get("_partial_item_code") and re.match(r"^\d+(?:\.\d+)?$", cleaned):
                    fields["items"] = [{
                        "item_code": fields.pop("_partial_item_code"),
                        "qty": float(cleaned),
                        "warehouse": item_warehouse
                    }]

    # Check for entity codes or names
    for f in req_fields:
        if f == "items":
            continue
        if not fields.get(f):
            if "code" in f or f == "item":
                match_code = re.search(r"\b(SKU\w+|INVALID_\w+)\b", cleaned, re.IGNORECASE)
                if match_code:
                    fields[f] = match_code.group(1).strip()
                else:
                    paren = re.search(r"\((SKU\w+)\)", cleaned, re.IGNORECASE)
                    if paren:
                        fields[f] = paren.group(1).strip()
                    elif re.match(r"^[A-Z0-9_\-]+$", cleaned, re.IGNORECASE):
                        fields[f] = cleaned
            else:
                # Name extraction - strip entity type prefixes like 'customer', 'client'
                m_lead = re.search(r"(?:look up|lookup|find|details of|details for|about|for client|for customer|for)\s+([\w\s\-\.]+?)(?:,|$|\?|\bwith\b|\bqty\b|\bquantity\b|\bof\b|\bitem\b)", cleaned, re.IGNORECASE)
                if m_lead:
                    raw_name = m_lead.group(1).strip()
                    raw_name = re.sub(r"^(?:customer|client|vendor|supplier|item|product)\s+", "", raw_name, flags=re.IGNORECASE).strip()
                    fields[f] = raw_name
                elif not any(w in cleaned.lower() for w in ["hello", "hi", "help", "what", "stock", "order", "tomorrow", "today", "yesterday"]) and len(cleaned.split()) <= 6:
                    if not re.search(r"\b(SKU\d+|\d+)\b", cleaned, re.IGNORECASE):
                        fields[f] = cleaned.strip(". ?")
                        
    # Check date relative mentions
    if "delivery_date" in skill.get("optional_fields", []) or "delivery_date" in req_fields:
        if not fields.get("delivery_date") and "tomorrow" in cleaned.lower():
            fields["delivery_date"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
    return fields


# =========================================================================
# Generic Pipeline Nodes (100% Schema-Driven)
# =========================================================================

def classify_intent_node(state: AgentState):
    messages = state.get("messages", [])
    if not messages:
        return {"detected_intent": "fallback"}
        
    last_msg = messages[-1].content.strip()
    cleaned = last_msg.lower()
    current_intent = state.get("detected_intent", "")
    is_complete = state.get("is_workflow_complete", False)
    
    # 1. Immediate greetings / capabilities
    if cleaned in ["hello", "hi", "hey", "greetings", "good morning", "good evening", "what can you do?", "what can you do", "help", "who are you?"]:
        return {"detected_intent": "fallback"}
        
    # 2. Check skill declarative keywords (longest match first)
    all_kw = []
    for skill in loaded_skills:
        for kw in skill.get("keywords", []):
            all_kw.append((kw, skill["name"]))
    all_kw.sort(key=lambda x: len(x[0]), reverse=True)
    for kw, skill_name in all_kw:
        if kw in cleaned:
            return {"detected_intent": skill_name}
            
    # 3. Multi-turn continuation: if currently in an incomplete workflow, preserve active intent
    if current_intent and current_intent in [s["name"] for s in loaded_skills] and not is_complete:
        logger.info(f"Preserving active intent '{current_intent}' during slot-filling.")
        return {"detected_intent": current_intent}

    # 4. Query LLM for intent classification dynamically based on loaded skills
    skills_options = ", ".join([f"'{s['name']}'" for s in loaded_skills])
    prompt = (
        f"Classify the user message intent into one of the allowed categories.\n"
        f"Allowed categories: {skills_options}, or 'fallback'.\n"
        f"User message: '{last_msg}'\n"
        f"Respond strictly with JSON object: {{\"intent\": \"<category>\"}}"
    )
    res = invoke_structured_llm(prompt)
    intent = "fallback"
    if res and res.get("intent"):
        llm_intent = res.get("intent")
        if llm_intent in [s["name"] for s in loaded_skills] or llm_intent == "fallback":
            intent = llm_intent
            
    logger.info(f"Classified intent: {intent}")
    return {"detected_intent": intent}


def collect_parameters_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    intent = state.get("detected_intent", "")
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"collected_fields": current_fields}
        
    conversation_text = " ".join([m.content for m in messages])
    fields = dict(current_fields)
    
    if last_msg.strip():
        prompt = (
            f"You are a precise JSON entity extractor for ERPNext AI Assistant.\n"
            f"Skill name: '{skill['name']}'\n"
            f"Skill instructions:\n{skill['instructions']}\n"
            f"Current collected fields: {json.dumps(current_fields)}\n"
            f"User message: '{last_msg}'\n"
            f"Extract or update fields according to the skill instructions.\n"
            f"Respond strictly with a JSON object of extracted fields."
        )

        res = invoke_structured_llm(prompt)
        
        # Detect if this is a continuation turn (pronouns, short follow-up)
        # vs a new explicit entity query
        continuation_pronouns = [
            "their", "they", "its", "it's", "the customer", "the item",
            "what is", "what are", "how much", "how many", "tell me more",
            "more about", "more details", "more info"
        ]
        is_continuation = (
            any(t in last_msg.lower() for t in continuation_pronouns)
            and len(last_msg.split()) <= 12
        )
        
        if res and isinstance(res, dict):
            for k, v in res.items():
                if v is not None and str(v).strip() != "" and v != "null":
                    # On continuation turns, never overwrite already-collected fields
                    if is_continuation and k in current_fields and current_fields.get(k):
                        continue
                    fields[k] = v
        prev_history = " ".join([m.content for m in messages[:-1]]) if len(messages) > 1 else ""
        if not prev_history:
            prev_history = conversation_text
        fields = _extract_fields_from_schema(skill, last_msg, fields, prev_history)
        
    defaults = skill.get("defaults", [])
    if defaults:
        fields = apply_skill_defaults(fields, defaults)
        
    # Flat key normalization
    if "filters" in fields and isinstance(fields["filters"], list):
        for flt in fields["filters"]:
            if isinstance(flt, list) and len(flt) >= 3:
                key = str(flt[0]).strip()
                val = str(flt[2]).replace("%", "").strip()
                if key and key not in fields:
                    fields[key] = val
                    
    logger.info(f"Collected parameters: {fields}")
    return {"collected_fields": fields}


def validate_parameters_node(state: AgentState):
    intent = state.get("detected_intent", "")
    collected = dict(state.get("collected_fields", {}) or {})
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"all_required_filled": True, "missing_parameters": []}
        
    all_required_filled = True
    missing_parameters = []
    
    # 1. Validate required fields declared in skill
    for field in skill.get("required_fields", []):
        val = collected.get(field)
        if field == "items":
            if not val or not isinstance(val, list) or len(val) == 0:
                all_required_filled = False
                missing_parameters.append("items")
            else:
                for entry in val:
                    if not entry.get("item_code"):
                        all_required_filled = False
                        if "item_code" not in missing_parameters:
                            missing_parameters.append("item_code")
                    if entry.get("qty") is None or float(entry.get("qty", 0)) <= 0:
                        all_required_filled = False
                        if "qty" not in missing_parameters:
                            missing_parameters.append("qty")
        else:
            if val is None or str(val).strip() == "":
                all_required_filled = False
                missing_parameters.append(field)
                
    # 2. Evaluate declarative validation rules
    rules = skill.get("validation_rules", [])
    if rules:
        rules_ok, rules_missing = evaluate_validation_rules(collected, rules)
        if not rules_ok:
            # Reapply skill defaults if an invalid date was cleared
            defaults = skill.get("defaults", [])
            if defaults:
                collected = apply_skill_defaults(collected, defaults)
                # Re-validate with applied defaults
                rules_ok, rules_missing = evaluate_validation_rules(collected, rules)
                
            if not rules_ok:
                all_required_filled = False
                for m in rules_missing:
                    if m not in missing_parameters:
                        missing_parameters.append(m)

    return {
        "all_required_filled": all_required_filled,
        "missing_parameters": missing_parameters,
        "collected_fields": collected
    }


def ask_for_missing_info_node(state: AgentState):
    intent = state.get("detected_intent", "")
    missing = state.get("missing_parameters", [])
    
    prompt = (
        f"You are an ERPNext AI Assistant.\n"
        f"We are running the skill: '{intent}'.\n"
        f"The following required parameters are missing: {missing}.\n"
        f"Write a friendly, concise request asking the user to provide these missing details."
    )
    
    response_text = invoke_llm(prompt)
    if "Error" in response_text or not response_text.strip():
        response_text = f"I need some more details to proceed: {', '.join(missing)}. Please provide them."
        
    return {"final_response": response_text, "is_workflow_complete": False}


def call_generic_tool_node(state: AgentState):
    intent = state.get("detected_intent", "")
    collected = state.get("collected_fields", {}) or {}
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"tool_raw_response": {"success": False, "error": "Skill not found"}}
        
    tool = skill.get("tool", "list_doctype")
    doctype_name = skill.get("doctype", "")
    record_id = collected.get("id") or collected.get("name")
    
    # Whitelist check
    if not is_doctype_allowed(doctype_name):
        allowed = get_allowed_doctypes()
        msg = f"DocType '{doctype_name}' is not permitted by whitelist. Allowed DocTypes: {', '.join(allowed)}"
        logger.warning(msg)
        return {
            "target_tool": tool,
            "target_doctype": doctype_name,
            "tool_raw_response": {
                "status_code": 403,
                "success": False,
                "data": None,
                "error": msg
            }
        }
        
    # Build tool parameters dynamically from skill metadata
    parameters = {}
    if tool == "list_doctype":
        query_def = skill.get("query_parameters", {})
        raw_filters = query_def.get("filters", [])
        formatted_filters = []
        for flt in raw_filters:
            if isinstance(flt, list) and len(flt) == 3:
                field_name, op, template_val = flt
                val = str(template_val)
                for k, v in collected.items():
                    val = val.replace(f"%{{{k}}}%", f"%{v}%").replace(f"{{{k}}}", str(v))
                formatted_filters.append([field_name, op, val])
                
        # Optional filters
        for opt in query_def.get("optional_filters", []):
            field_name = opt.get("field")
            if field_name and collected.get(field_name):
                flt = opt.get("filter")
                if isinstance(flt, list) and len(flt) == 3:
                    f_name, op, template_val = flt
                    val = str(template_val).replace(f"%{{{field_name}}}%", f"%{collected.get(field_name)}%").replace(f"{{{field_name}}}", str(collected.get(field_name)))
                    formatted_filters.append([f_name, op, val])
                    
        parameters = {
            "filters": formatted_filters,
            "fields": query_def.get("fields", [])
        }
        
    elif tool == "add_doctype":
        ps = skill.get("payload_structure", {})
        parameters = dict(ps.get("fixed", {}))
        
        # Field mappings
        for k, v in ps.get("field_mappings", {}).items():
            if collected.get(v) is not None:
                parameters[k] = collected.get(v)
                
        # Child table defaults
        child_defaults = ps.get("child_defaults", {})
        if "items" in parameters and isinstance(parameters["items"], list):
            for row in parameters["items"]:
                for dk, dv in child_defaults.items():
                    if dk not in row or not row[dk] or str(row[dk]).lower() == "default":
                        row[dk] = dv
                        
    elif tool == "update_doctype":
        parameters = dict(collected)
        parameters.pop("id", None)
        parameters.pop("name", None)

    # Execute generic CRUD tool
    logger.info(f"Calling generic tool: {tool} on DocType: {doctype_name} with params: {parameters}")
    try:
        if tool == "add_doctype":
            tool_res = add_doctype(doctype_name, parameters)
        elif tool == "list_doctype":
            tool_res = list_doctype(doctype_name, parameters)
        elif tool == "update_doctype":
            tool_res = update_doctype(doctype_name, record_id, parameters)
        elif tool == "delete_doctype":
            tool_res = delete_doctype(doctype_name, record_id)
        else:
            tool_res = {"success": False, "error": f"Unknown tool: {tool}"}
    except Exception as e:
        logger.exception("Error calling generic tool")
        tool_res = {"success": False, "error": str(e)}
        
    return {
        "target_tool": tool,
        "target_doctype": doctype_name,
        "tool_raw_response": tool_res
    }


def format_agent_message_node(state: AgentState):
    intent = state.get("detected_intent", "")
    tool_raw_response = state.get("tool_raw_response", {}) or {}
    collected = state.get("collected_fields", {}) or {}
    messages = state.get("messages", [])
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"final_response": "I couldn't process this request.", "is_workflow_complete": True}
        
    success = tool_raw_response.get("success", False)
    error = tool_raw_response.get("error")
    data = tool_raw_response.get("data")
    
    prompt = (
        f"You are an ERPNext AI Assistant.\n"
        f"Skill: {skill['name']}\n"
        f"User query: {messages[-1].content if messages else ''}\n"
        f"Raw tool execution output:\n{json.dumps(tool_raw_response, indent=2)}\n"
        f"Format a helpful, clean response for the user based on the tool results."
    )
    response_text = invoke_llm(prompt)
    
    # Declarative Template Fallback Rendering
    if "Error" in response_text or not response_text.strip():
        if not success:
            if error and ("not permitted by whitelist" in str(error) or "403" in str(error)):
                response_text = f"Permission Error: {error}"
            elif skill.get("error_template"):
                err_tmpl = skill.get("error_template")
                response_text = err_tmpl.replace("{error}", str(error))
            else:
                response_text = f"Operation failed: {error}"
        else:
            # Succeeded
            if isinstance(data, list):
                if not data:
                    not_found_tmpl = skill.get("not_found_message")
                    has_params = any(str(v).strip() for v in collected.values() if v is not None)
                    if not_found_tmpl and has_params:
                        response_text = not_found_tmpl
                        for k, v in collected.items():
                            response_text = response_text.replace(f"{{{k}}}", str(v))
                    else:
                        response_text = "I encountered an error trying to format the results. Please try again."
                else:
                    # Single or multiple records rendering via response_template
                    resp_tmpl = skill.get("response_template", "")
                    if resp_tmpl and len(data) == 1:
                        rec = data[0]
                        response_text = resp_tmpl
                        for k, v in rec.items():
                            response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                        for k, v in collected.items():
                            response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                    else:
                        # Multi-record or inventory table/summary rendering
                        first_row = data[0]
                        if "actual_qty" in first_row and "warehouse" in first_row:
                            item_code_val = collected.get("item_code", "item")
                            lines = [f"**Inventory Availability Check**\n\nBased on the current inventory levels, here's the availability status for the item \"{item_code_val}\":\n"]
                            total_qty = 0.0
                            for row in data:
                                w = row.get("warehouse", "Unknown")
                                q = float(row.get("actual_qty", 0))
                                total_qty += q
                                lines.append(f"- **Warehouse**: {w} | **Actual Qty**: {q}")
                            lines.append(f"\n**Total Available Quantity**: {total_qty}")
                            response_text = "\n".join(lines)
                        elif resp_tmpl:
                            rec = data[0]
                            response_text = resp_tmpl
                            for k, v in rec.items():
                                response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                            for k, v in collected.items():
                                response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                        else:
                            response_text = "Action completed successfully."
                            
            elif isinstance(data, dict):
                resp_tmpl = skill.get("response_template", "")
                if resp_tmpl:
                    response_text = resp_tmpl
                    for k, v in data.items():
                        response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                    for k, v in collected.items():
                        response_text = response_text.replace(f"{{{k}}}", str(v) if v is not None else "N/A")
                else:
                    response_text = f"Action completed successfully: {data.get('name', 'OK')}"
            else:
                response_text = "Action completed successfully."
        
    return {"final_response": response_text, "is_workflow_complete": True}


def fallback_response_node(state: AgentState):
    response_text = (
        "I'm an AI assistant for ERPNext.\n"
        "Currently, I can assist you with:\n"
        "- Creating a Sales Order (e.g. 'Create a sales order for customer ABC with item XYZ qty 10')\n"
        "- Checking stock / inventory (e.g. 'Check stock for SKU005' or 'Do we have 40 Headphones (SKU009)?')\n"
        "- Looking up customer details (e.g. 'Look up customer West View Software Ltd.')\n"
        "- Looking up item details (e.g. 'Look up item SKU001')\n"
        "\n"
        "How can I help you today?"
    )
    return {"final_response": response_text, "is_workflow_complete": True}


def unauthorized_response_node(state: AgentState):
    response_text = "Permission Denied: You do not have the required role permissions to perform this action."
    return {"final_response": response_text, "is_workflow_complete": True}


def format_response_node(state: AgentState):
    final_resp = state.get("final_response", "")
    return {"messages": [AIMessage(content=final_resp)]}


# Routers
def route_by_intent_and_auth(state: AgentState):
    intent = state.get("detected_intent", "fallback")
    roles = state.get("user_roles", [])
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if skill:
        allowed = skill.get("allowed_roles", [])
        if allowed and not any(r in roles for r in allowed):
            logger.warning(f"User unauthorized for intent {intent}. User roles: {roles}")
            return "unauthorized"
            
    if intent == "fallback":
        return "fallback"
        
    return "authorized"


def route_after_validation(state: AgentState):
    if state.get("all_required_filled"):
        return "call_generic_tool"
    else:
        return "ask_for_missing_info"


# Graph Construction
workflow = StateGraph(AgentState)

workflow.add_node("classify_intent", classify_intent_node)
workflow.add_node("collect_parameters", collect_parameters_node)
workflow.add_node("validate_parameters", validate_parameters_node)
workflow.add_node("ask_for_missing_info", ask_for_missing_info_node)
workflow.add_node("call_generic_tool", call_generic_tool_node)
workflow.add_node("format_agent_message", format_agent_message_node)
workflow.add_node("fallback_response", fallback_response_node)
workflow.add_node("unauthorized_response", unauthorized_response_node)
workflow.add_node("format_response", format_response_node)

workflow.set_entry_point("classify_intent")

workflow.add_conditional_edges(
    "classify_intent",
    route_by_intent_and_auth,
    {
        "unauthorized": "unauthorized_response",
        "fallback": "fallback_response",
        "authorized": "collect_parameters"
    }
)

workflow.add_edge("collect_parameters", "validate_parameters")

workflow.add_conditional_edges(
    "validate_parameters",
    route_after_validation,
    {
        "ask_for_missing_info": "ask_for_missing_info",
        "call_generic_tool": "call_generic_tool"
    }
)

workflow.add_edge("ask_for_missing_info", "format_response")
workflow.add_edge("call_generic_tool", "format_agent_message")
workflow.add_edge("format_agent_message", "format_response")
workflow.add_edge("unauthorized_response", "format_response")
workflow.add_edge("fallback_response", "format_response")
workflow.add_edge("format_response", END)

compiled_graph = workflow.compile()
