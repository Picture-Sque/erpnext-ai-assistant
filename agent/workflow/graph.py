import os
import logging
import re
import json
import yaml
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Annotated
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END

from llm import invoke_structured_llm, invoke_llm
from workflow.state import AgentState
from tools.generic_tools import add_doctype, list_doctype, update_doctype, delete_doctype, is_doctype_allowed, get_allowed_doctypes

logger = logging.getLogger("workflow_graph")
logger.setLevel(logging.INFO)

# Load Skills Dynamically from skills/ folder
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
                            
                            name = data.get("name")
                            desc = data.get("description")
                            inst = data.get("instructions")
                            if name and desc and inst:
                                skills.append({
                                    "name": name,
                                    "description": desc,
                                    "instructions": inst,
                                    "defaults": data.get("defaults", []),
                                    "validation_rules": data.get("validation_rules", [])
                                })
                except Exception as e:
                    logger.warning(f"Failed to parse skill {skill_md}: {e}")
    
    if not skills:
        # Static fallback if reading directory fails or files don't exist
        skills = [
            {
                "name": "check-inventory",
                "description": "Check current stock levels for an item in ERPNext, optionally at a specific warehouse. Use when the user asks \"how much X do we have\", \"is X in stock\", \"check inventory for X\", or similar.",
                "instructions": "Use the `list_doctype` tool on DocType \"Bin\".\nFilter the query by `item_code` (and optionally `warehouse` if a warehouse is specified by the user).\nUse parameters:\n- filters: [[\"item_code\", \"=\", \"<item_code>\"], [\"warehouse\", \"like\", \"%<warehouse>%\"]] (if warehouse is specified)\n- fields: [\"item_code\", \"warehouse\", \"actual_qty\"]\n\nFormat the results returned by the tool as a clean Markdown table with columns: Item Code, Warehouse, and Actual Quantity.\nAt the end of the table, include a total-quantity summary summing up the actual_qty of all matching rows.\nIf the tool returns no matching rows, inform the user that no stock was found for the item.\n",
                "defaults": [],
                "validation_rules": []
            },
            {
                "name": "create-sales-order",
                "description": "Create a new Sales Order in ERPNext for a customer. Use when the user asks to place an order, create a sales order, or sell items to a customer.",
                "instructions": "Use the `add_doctype` tool on DocType \"Sales Order\".\nThe tool call parameters must be structured as follows:\n{\n  \"company\": \"Lorr\",\n  \"customer\": \"<customer>\",\n  \"transaction_date\": \"<transaction_date>\",\n  \"delivery_date\": \"<delivery_date>\",\n  \"items\": [\n    {\n      \"item_code\": \"<item_code>\",\n      \"qty\": <qty>,\n      \"warehouse\": \"Stores - Lor\"\n    }\n  ]\n}\n\nBefore invoking the tool, you must confirm that the following required slots are filled:\n- customer\n- items (containing at least one item, with both item_code and qty populated)\n\nIf any of these required slots are missing, ask the user to provide the missing details (e.g. customer name or items details).\nIf transaction_date or delivery_date are not specified, they will be automatically computed and populated in the background (transaction_date defaults to today's date, and delivery_date defaults to transaction_date + 7 days).\nAlways populate \"company\" as \"Lorr\" and each item's \"warehouse\" as \"Stores - Lor\" in the payload unless the user specifies otherwise.\nOnce the sales order is successfully created, report back the new Sales Order name and status to the user.\n",
                "defaults": [
                    {"field": "transaction_date", "value": "today"},
                    {"field": "delivery_date", "value": "transaction_date + 7d"}
                ],
                "validation_rules": [
                    {
                        "field": "delivery_date",
                        "must_be_after": "transaction_date",
                        "on_fail": "must be strictly after transaction_date"
                    }
                ]
            },
            {
                "name": "customer-lookup",
                "description": "Look up an ERPNext customer record by name, ID, or partial match. Use when the user asks \"find customer X\", \"who is X\", needs customer details, or when another skill needs to confirm a customer exists.",
                "instructions": "Use the `list_doctype` tool on DocType \"Customer\".\nFilter the query by `customer_name` using a partial match filter:\n- filters: [[\"customer_name\", \"like\", \"%<customer_name>%\"]]\n- fields: [\"customer_name\", \"customer_group\", \"territory\", \"email_id\", \"mobile_no\"]\n\nPresent the final response with the customer's name, group, territory, and contact information (email address and mobile number).\n",
                "defaults": [],
                "validation_rules": []
            }
        ]
    return skills

loaded_skills = load_skills()

# RBAC rule mapping for skills
ROLE_RULES = {
    "create-sales-order": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
    "check-inventory": ["Stock User", "Stock Manager", "Sales User", "Sales Manager", "System Manager", "Administrator"],
    "customer-lookup": ["Sales User", "Sales Manager", "Accounts User", "Accounts Manager", "System Manager", "Administrator"]
}

# Structured response schema for intent
class IntentClassification(BaseModel):
    intent: str = Field(
        description="Intent of the user message. Must be one of the skill names or 'fallback'."
    )

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


def evaluate_validation_rules(collected: dict, rules: list) -> tuple[bool, list[str]]:
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
                    except ValueError:
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


def _get_offline_fallback_fields(intent: str, last_msg: str, current_fields: dict) -> dict:
    fields = dict(current_fields)
    if intent == "create-sales-order":
        if not fields.get("customer"):
            cust_match = re.search(r"for customer ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b|\bqty\b|\bfor\b|\bof\b)", last_msg, re.IGNORECASE)
            if not cust_match:
                cust_match = re.search(r"for ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b|\bqty\b|\bfor\b|\bof\b)", last_msg, re.IGNORECASE)
            if cust_match:
                fields["customer"] = cust_match.group(1).strip()
        if not fields.get("items"):
            match_c = re.search(r"\b(\d+)\s+(SKU\d+)\b", last_msg, re.IGNORECASE)
            if match_c:
                fields["items"] = [{
                    "item_code": match_c.group(2).strip(),
                    "qty": float(match_c.group(1))
                }]
    elif intent == "check-inventory":
        item_match = re.search(r"\b(SKU\d+)\b", last_msg, re.IGNORECASE)
        if item_match:
            fields["item_code"] = item_match.group(1).strip()
    elif intent == "customer-lookup":
        cust_match = re.search(r"for\s+([\w\s\-\.]+?)(?:\?|$)", last_msg, re.IGNORECASE)
        if cust_match:
            fields["customer_name"] = cust_match.group(1).strip()
    return fields


def _offline_fallback_validator(intent: str, collected: dict) -> tuple[bool, list[str]]:
    all_required_filled = True
    missing_parameters = []
    if intent == "create-sales-order":
        if not collected.get("customer") or not collected.get("items"):
            all_required_filled = False
            missing_parameters = ["customer" if not collected.get("customer") else "items"]
        else:
            for item in collected.get("items", []):
                if not item.get("item_code") or not item.get("qty"):
                    all_required_filled = False
                    missing_parameters = ["item_code" if not item.get("item_code") else "qty"]
    elif intent == "check-inventory":
        if not collected.get("item_code"):
            all_required_filled = False
            missing_parameters = ["item_code"]
    elif intent == "customer-lookup":
        if not collected.get("customer_name"):
            all_required_filled = False
            missing_parameters = ["customer_name"]
    return all_required_filled, missing_parameters


def _offline_fallback_tool_resolver(intent: str, collected: dict) -> dict:
    if intent == "create-sales-order":
        return {
            "tool": "add_doctype",
            "doctype_name": "Sales Order",
            "parameters": {
                "customer": collected.get("customer"),
                "transaction_date": collected.get("transaction_date"),
                "delivery_date": collected.get("delivery_date"),
                "items": collected.get("items")
            }
        }
    elif intent == "check-inventory":
        res = {
            "tool": "list_doctype",
            "doctype_name": "Bin",
            "parameters": {
                "filters": [["item_code", "=", collected.get("item_code")]]
            }
        }
        if collected.get("warehouse"):
            res["parameters"]["filters"].append(["warehouse", "like", f"%{collected.get('warehouse')}%"])
        return res
    elif intent == "customer-lookup":
        return {
            "tool": "list_doctype",
            "doctype_name": "Customer",
            "parameters": {
                "filters": [["customer_name", "like", f"%{collected.get('customer_name')}%"]]
            }
        }
    return {}


# Nodes Implementation

def classify_intent_node(state: AgentState):
    current_intent = state.get("detected_intent", "")
    is_complete = state.get("is_workflow_complete", False)
    
    # If currently executing a skill and slot filling is incomplete, preserve it
    if current_intent and current_intent in [s["name"] for s in loaded_skills] and not is_complete:
        logger.info(f"Preserving active intent '{current_intent}' during slot-filling.")
        return {"detected_intent": current_intent}

    messages = state.get("messages", [])
    if not messages:
        return {"detected_intent": "fallback"}
        
    last_msg = messages[-1].content
    
    # Heuristic guess:
    intent = "fallback"
    cleaned = last_msg.lower()
    if any(keyword in cleaned for keyword in ["sales order", "create order", "place an order"]):
        intent = "create-sales-order"
    elif any(keyword in cleaned for keyword in ["stock", "inventory", "qty of", "quantity of", "check stock", "have", "available", "in stock"]):
        intent = "check-inventory"
    elif any(keyword in cleaned for keyword in ["customer", "client", "customer details", "look up customer", "details for", "lookup"]):
        intent = "customer-lookup"
        
    # Query LLM
    skills_options = ", ".join([f"'{s['name']}'" for s in loaded_skills])
    prompt = (
        f"Classify the user message intent into one of the allowed categories.\n"
        f"Allowed categories: {skills_options}, or 'fallback'.\n"
        f"User message: '{last_msg}'\n"
        f"Respond strictly with JSON object: {{\"intent\": \"<category>\"}}"
    )
    res = invoke_structured_llm(prompt)
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
        
    prompt = (
        f"You are a precise JSON entity extractor for ERPNext AI Assistant.\n"
        f"We are running the skill: '{skill['name']}'.\n"
        f"Skill Description: {skill['description']}\n"
        f"Skill Instructions:\n{skill['instructions']}\n"
        f"Currently collected fields/parameters: {json.dumps(current_fields, indent=2)}\n"
        f"User's last message: '{last_msg}'\n"
        f"\n"
        f"Please extract or update the fields/parameters based on the skill instructions and the conversation history.\n"
        f"Respond strictly with a JSON object containing the updated fields/parameters (merge new info with existing fields)."
    )
    
    res = invoke_structured_llm(prompt)
    fields = dict(current_fields)
    if res and isinstance(res, dict):
        for k, v in res.items():
            if v is not None:
                fields[k] = v
    else:
        fields = _get_offline_fallback_fields(intent, last_msg, fields)
        
    defaults = skill.get("defaults", [])
    if defaults:
        fields = apply_skill_defaults(fields, defaults)
        
    logger.info(f"Collected parameters: {fields}")
    return {"collected_fields": fields}


def validate_parameters_node(state: AgentState):
    intent = state.get("detected_intent", "")
    collected = state.get("collected_fields", {}) or {}
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"all_required_filled": True, "missing_parameters": []}
        
    prompt = (
        f"You are a validator for ERPNext AI Assistant.\n"
        f"Skill: '{skill['name']}'\n"
        f"Skill Instructions:\n{skill['instructions']}\n"
        f"Current collected parameters:\n{json.dumps(collected, indent=2)}\n"
        f"\n"
        f"Determine if all required parameters for this skill have been successfully collected. "
        f"According to the instructions, identify if any required fields are missing or incomplete.\n"
        f"Respond strictly with a JSON object:\n"
        f"{{\n"
        f"  \"all_required_filled\": true/false,\n"
        f"  \"missing_parameters\": [\"field1\", \"field2\"]  # List of missing required fields (if any)\n"
        f"}}"
    )
    
    all_required_filled = True
    missing_parameters = []
    
    res = invoke_structured_llm(prompt)
    if res and isinstance(res, dict):
        all_required_filled = res.get("all_required_filled", True)
        missing_parameters = res.get("missing_parameters", [])
    else:
        # Heuristic fallback
        all_required_filled, missing_parameters = _offline_fallback_validator(intent, collected)
        
    rules = skill.get("validation_rules", [])
    if rules:
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
        f"Write a friendly request asking the user to provide these missing details."
    )
    
    response_text = invoke_llm(prompt)
    if "Error" in response_text or not response_text.strip():
        response_text = f"I need some more details to proceed: {', '.join(missing)}. Please provide them."
        
    return {"final_response": response_text}


def call_generic_tool_node(state: AgentState):
    intent = state.get("detected_intent", "")
    collected = state.get("collected_fields", {}) or {}
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"tool_raw_response": {"success": False, "error": "Skill not found"}}
        
    prompt = (
        f"Determine which generic CRUD tool to call and its parameters based on the skill instructions and collected fields.\n"
        f"Available tools:\n"
        f"- `add_doctype(doctype_name: str, parameters: dict)`: Create a new document\n"
        f"- `list_doctype(doctype_name: str, parameters: dict)`: Query documents\n"
        f"- `update_doctype(doctype_name: str, id: str, parameters: dict)`: Update a document\n"
        f"- `delete_doctype(doctype_name: str, id: str)`: Delete a document\n"
        f"\n"
        f"Skill instructions:\n{skill['instructions']}\n"
        f"Collected fields:\n{json.dumps(collected, indent=2)}\n"
        f"\n"
        f"Note: Ensure that the parameters dictionary strictly follows the schema defined in the instructions.\n"
        f"Respond strictly with a JSON object:\n"
        f"{{\n"
        f"  \"tool\": \"add_doctype\" / \"list_doctype\" / \"update_doctype\" / \"delete_doctype\",\n"
        f"  \"doctype_name\": \"<DocType>\",\n"
        f"  \"id\": \"<record_id_if_applicable_else_null>\",\n"
        f"  \"parameters\": {{ ... }} # Parameters to pass to the tool\n"
        f"}}"
    )
    
    res = invoke_structured_llm(prompt)
    if not res:
        res = _offline_fallback_tool_resolver(intent, collected)
            
    tool = res.get("tool")
    doctype_name = res.get("doctype_name")
    record_id = res.get("id")
    parameters = res.get("parameters", {})
    
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
        
    # Execute tool
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
        logger.exception("Error calling tool")
        tool_res = {"success": False, "error": str(e)}
        
    return {
        "target_tool": tool,
        "target_doctype": doctype_name,
        "tool_raw_response": tool_res
    }


def format_agent_message_node(state: AgentState):
    intent = state.get("detected_intent", "")
    tool_raw_response = state.get("tool_raw_response", {}) or {}
    messages = state.get("messages", [])
    
    skill = next((s for s in loaded_skills if s["name"] == intent), None)
    if not skill:
        return {"final_response": "I couldn't process this request.", "is_workflow_complete": True}
        
    prompt = (
        f"You are an ERPNext AI Assistant.\n"
        f"Skill name: {skill['name']}\n"
        f"Skill instructions:\n{skill['instructions']}\n"
        f"User's query/history: {messages}\n"
        f"Raw Tool Execution Output:\n{json.dumps(tool_raw_response, indent=2)}\n"
        f"\n"
        f"Please format the final response for the user as guided by the skill instructions.\n"
        f"If the tool execution failed (success is false) or returned an error, explain the error clearly to the user so they know what went wrong (do not swallow or hide the validation error, so they can correct parameters if needed)."
    )
    
    response_text = invoke_llm(prompt)
    if "Error" in response_text or not response_text.strip():
        success = tool_raw_response.get("success", False)
        error = tool_raw_response.get("error")
        if not success:
            response_text = f"Tool execution failed. Error details: {error}"
        else:
            response_text = "I encountered an error trying to format the results. Please try again."
            
    return {"final_response": response_text, "is_workflow_complete": True}


def fallback_response_node(state: AgentState):
    response_text = (
        "I'm an AI assistant for ERPNext.\n"
        "Currently, I can assist you with:\n"
        "- Creating a Sales Order (e.g. 'Create a sales order for customer ABC with item XYZ qty 10')\n"
        "- Checking stock / inventory (e.g. 'Check stock for SKU005' or 'Do we have 40 Headphones (SKU009)?')\n"
        "- Looking up customer details (e.g. 'Look up customer West View Software Ltd.')\n"
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
    
    if intent in ROLE_RULES:
        allowed = ROLE_RULES[intent]
        if not any(r in roles for r in allowed):
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
