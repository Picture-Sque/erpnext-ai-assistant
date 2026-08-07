import os
import logging
import re
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END

from llm import invoke_structured_llm

from workflow.state import AgentState
from tools.erpnext_client import (
    create_sales_order,
    check_stock,
    get_customer,
    UnauthorizedError,
    ItemNotFoundError,
    CustomerNotFoundError
)
from datetime import datetime

def validate_delivery_date(date_str):
    try:
        user_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.today().date()
        if user_date < today:
            return None
        return user_date
    except ValueError:
        return None

logger = logging.getLogger("workflow_graph")
logger.setLevel(logging.INFO)

# Structured Output Schemas
class IntentClassification(BaseModel):
    intent: str = Field(
        description="Intent of the user message. Must be one of: 'create_sales_order', 'check_inventory', 'stock_check', 'customer_lookup', or 'fallback'."
    )

class SalesOrderItem(BaseModel):
    item_code: Optional[str] = Field(None, description="The product or item code/name.")
    qty: Optional[float] = Field(None, description="Quantity of the item.")
    rate: Optional[float] = Field(None, description="Rate/price of the item.")

class SalesOrderExtraction(BaseModel):
    customer: Optional[str] = Field(None, description="The customer name/ID.")
    items: List[SalesOrderItem] = Field(default_factory=list, description="List of items in the sales order.")
    delivery_date: Optional[str] = Field(None, description="The delivery date in YYYY-MM-DD format.")

class StockCheckExtraction(BaseModel):
    item_code: Optional[str] = Field(None, description="The product or item code/name to check stock for.")

class CustomerLookupExtraction(BaseModel):
    customer_name: Optional[str] = Field(None, description="The customer name or ID to look up.")

# Heuristics Fallbacks for Offline/Mock Testing
def heuristic_classify(text: str) -> str:
    cleaned = text.lower()
    if any(keyword in cleaned for keyword in ["sales order", "create order", "place an order"]):
        return "create_sales_order"
    if any(keyword in cleaned for keyword in ["stock", "inventory", "qty of", "quantity of", "check stock", "have", "available", "in stock"]):
        return "check_inventory"
    if any(keyword in cleaned for keyword in ["customer", "client", "customer details", "look up customer", "details for", "lookup"]):
        return "customer_lookup"
    return "fallback"

def heuristic_extract(text: str, current_fields: dict, intent: str = "") -> dict:
    fields = dict(current_fields)
    
    if intent == "create_sales_order":
        # Extract Customer (e.g., "for customer Acme", "for Acme")
        if not fields.get("customer"):
            cust_match = re.search(r"for customer ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b|\bqty\b|\bfor\b|\bof\b)", text, re.IGNORECASE)
            if not cust_match:
                cust_match = re.search(r"for ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b|\bqty\b|\bfor\b|\bof\b)", text, re.IGNORECASE)
            if cust_match:
                fields["customer"] = cust_match.group(1).strip()
                
        # Extract Items and Qty (e.g., "item widget qty 5", "20 Backpack (SKU008)")
        if not fields.get("items"):
            # Pattern A: <qty> <description> (<item_code>) e.g. "20 Backpack (SKU008)"
            match_a = re.search(r"\b(\d+)\s+[\w\s\-]+?\s*\(([\w\-]+)\)", text, re.IGNORECASE)
            # Pattern B: "item <item_code> qty <qty>"
            match_b_item = re.search(r"item ([\w\s\-\.]+?)(?:,|$|\bqty\b|\bquantity\b)", text, re.IGNORECASE)
            match_b_qty = re.search(r"(?:qty|quantity) (\d+)", text, re.IGNORECASE)
            # Pattern C: "<qty> <item_code>" where item_code starts with SKU
            match_c = re.search(r"\b(\d+)\s+(SKU\d+)\b", text, re.IGNORECASE)
            
            if match_a:
                fields["items"] = [{
                    "item_code": match_a.group(2).strip(),
                    "qty": float(match_a.group(1))
                }]
            elif match_b_item and match_b_qty:
                fields["items"] = [{
                    "item_code": match_b_item.group(1).strip(),
                    "qty": float(match_b_qty.group(1))
                }]
            elif match_c:
                fields["items"] = [{
                    "item_code": match_c.group(2).strip(),
                    "qty": float(match_c.group(1))
                }]

        # Extract Delivery Date (e.g., "delivery date 27-05-2026", "delivery_date: 2026-05-27")
        if not fields.get("delivery_date"):
            date_match = re.search(r"delivery\s*date\s*(?:is|:|to)?\s*(\d{2,4}[\-\/\.]\d{2}[\-\/\.]\d{2,4})", text, re.IGNORECASE)
            if date_match:
                raw_date = date_match.group(1).replace("/", "-").replace(".", "-")
                parts = raw_date.split("-")
                if len(parts) == 3:
                    if len(parts[0]) == 4: # YYYY-MM-DD
                        fields["delivery_date"] = raw_date
                    elif len(parts[2]) == 4: # DD-MM-YYYY
                        fields["delivery_date"] = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        
    elif intent in ["check_inventory", "stock_check"]:
        # Extract item_code (e.g. SKU009 or (SKU009) or ITEM-003)
        item_match = re.search(r"\b(SKU\d+)\b", text, re.IGNORECASE)
        if not item_match:
            item_match = re.search(r"(?:stock for|stock of|check stock for|stock check|inventory of|qty of|quantity of) ([\w\-\.]+)", text, re.IGNORECASE)
        if not item_match:
            item_match = re.search(r"\(([\w\-]+)\)", text)
        if item_match:
            fields["item_code"] = item_match.group(1).strip()
            
        # Extract qty (e.g. "40 Headphones")
        qty_match = re.search(r"\b(\d+)\b", text)
        if qty_match:
            fields["qty"] = float(qty_match.group(1))
            
        # Extract warehouse (e.g. "in Work In Progress - MFD")
        wh_match = re.search(r"\bin\s+([\w\s\-]+?)(?:\?|$)", text, re.IGNORECASE)
        if wh_match:
            fields["warehouse"] = wh_match.group(1).strip()
            
    elif intent == "customer_lookup":
        cust_match = re.search(r"for\s+([\w\s\-\.]+?)(?:\?|$)", text, re.IGNORECASE)
        if not cust_match:
            cust_match = re.search(r"details\s+of\s+([\w\s\-\.]+?)(?:\?|$)", text, re.IGNORECASE)
        if cust_match:
            fields["customer_name"] = cust_match.group(1).strip()
            
    return fields

def heuristic_extract_stock(text: str, current_fields: dict) -> dict:
    fields = dict(current_fields)
    # Extract item code: e.g., "check stock for ITEM-003", "stock check ITEM-003", "stock of ITEM-003"
    item_match = re.search(r"(?:stock for|stock of|check stock for|stock check|inventory of|qty of|quantity of) ([\w\-\.]+)", text, re.IGNORECASE)
    if item_match:
        fields["item_code"] = item_match.group(1).strip()
    return fields

def heuristic_extract_customer(text: str, current_fields: dict) -> dict:
    fields = dict(current_fields)
    # Extract customer name: e.g., "look up customer Acme", "details for Acme", "customer info for Acme"
    cust_match = re.search(r"(?:customer|client|details for|info for|look up customer|profile of) ([\w\s\-\.]+)", text, re.IGNORECASE)
    if cust_match:
        fields["customer_name"] = cust_match.group(1).strip()
    return fields

# Nodes Implementation
def classify_intent_node(state: AgentState):
    # If we are already in an active intent, and slots are not fully filled yet,
    # preserve the intent to prevent drift under heuristic-only classification.
    current_intent = state.get("detected_intent", "")
    if current_intent == "create_sales_order":
        collected = state.get("collected_fields", {}) or {}
        customer = collected.get("customer")
        items = collected.get("items", [])
        
        missing = False
        if not customer or not items:
            missing = True
        else:
            for item in items:
                if not item.get("item_code") or not item.get("qty"):
                    missing = True
        # ALSO preserve intent if the delivery date was invalid and we are waiting for a correction!
        if collected.get("invalid_delivery_date_provided"):
            missing = True
            
        if missing:
            logger.info(f"Preserving active intent '{current_intent}' during slot-filling.")
            return {"detected_intent": current_intent}
            
    elif current_intent == "check_inventory":
        collected = state.get("collected_fields", {}) or {}
        item_code = collected.get("item_code")
        if not item_code:
            logger.info(f"Preserving active intent '{current_intent}' during slot-filling.")
            return {"detected_intent": current_intent}
            
    elif current_intent == "customer_lookup":
        collected = state.get("collected_fields", {}) or {}
        customer_name = collected.get("customer_name")
        if not customer_name:
            logger.info(f"Preserving active intent '{current_intent}' during slot-filling.")
            return {"detected_intent": current_intent}

    messages = state.get("messages", [])
    if not messages:
        return {"detected_intent": "fallback"}
        
    current_intent = state.get("detected_intent", "")
    collected = state.get("collected_fields", {}) or {}
    
    # Preserve in-progress intent if slot filling is incomplete
    if current_intent == "create_sales_order" and (not collected.get("customer") or not collected.get("items")):
        logger.info(f"Retaining in-progress intent: {current_intent}")
        return {"detected_intent": current_intent}
        
    last_msg = messages[-1].content
    intent = heuristic_classify(last_msg)
    
    prompt = (
        f"Classify the user message intent into one of the allowed categories.\n"
        f"Allowed categories: 'create_sales_order', 'check_inventory', 'stock_check', 'customer_lookup', 'fallback'.\n"
        f"User message: '{last_msg}'\n"
        f"Respond strictly with JSON object: {{\"intent\": \"<category>\"}}"
    )
    res = invoke_structured_llm(prompt)
    if res and res.get("intent"):
        llm_intent = res.get("intent")
        if llm_intent in ["create_sales_order", "check_inventory", "stock_check", "customer_lookup", "fallback"]:
            if llm_intent == "stock_check":
                llm_intent = "check_inventory"
            intent = llm_intent
            
    logger.info(f"Classified intent: {intent}")
    return {"detected_intent": intent}

def collect_sales_order_info_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    intent = state.get("detected_intent", "")
    
    # Reset invalid date flag since user is providing new input
    if current_fields.get("invalid_delivery_date_provided"):
        current_fields["invalid_delivery_date_provided"] = False
        
    # Run heuristics first
    fields = heuristic_extract(last_msg, current_fields, intent)
    
    prompt = (
        f"Extract Sales Order details from the user message: '{last_msg}'.\n"
        f"Existing fields: {current_fields}.\n"
        f"Respond strictly with JSON object containing keys:\n"
        f"- 'customer': Customer name string or null\n"
        f"- 'items': List of objects [{{\"item_code\": \"...\", \"qty\": 1.0}}]\n"
        f"- 'delivery_date': Date string (YYYY-MM-DD) or null"
    )
    res = invoke_structured_llm(prompt)
    if res:
        if res.get("customer"):
            fields["customer"] = res["customer"]
        if res.get("delivery_date"):
            fields["delivery_date"] = res["delivery_date"]
        if res.get("items"):
            fields["items"] = []
            for item in res["items"]:
                raw_code = item.get("item_code")
                if raw_code:
                    m = re.search(r"\(([\w\-]+)\)", raw_code)
                    if m:
                        raw_code = m.group(1).strip()
                fields["items"].append({
                    "item_code": raw_code,
                    "qty": item.get("qty"),
                    "rate": item.get("rate")
                })
            
    # Validate delivery date if extracted
    date_val = fields.get("delivery_date")
    if date_val:
        validated = validate_delivery_date(date_val)
        if validated is None:
            fields["delivery_date"] = None
            fields["invalid_delivery_date_provided"] = True
            logger.info("Invalid or past delivery date parsed; clearing slot and setting correction flag.")
            
    logger.info(f"Collected fields: {fields}")
    return {"collected_fields": fields}

def check_missing_info(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    
    if collected.get("invalid_delivery_date_provided"):
        return "ask_for_missing_info"
        
    customer = collected.get("customer")
    items = collected.get("items", [])
    
    if not customer:
        return "ask_for_missing_info"
    if not items:
        return "ask_for_missing_info"
        
    for item in items:
        if not item.get("item_code") or not item.get("qty"):
            return "ask_for_missing_info"
            
    return "call_create_sales_order_tool"

def ask_for_missing_info_node(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    
    if collected.get("invalid_delivery_date_provided"):
        # Do not clear the flag here; it will be reset in the collect node once they send a new input
        return {
            "final_response": "The delivery date must be in the future. Please provide a valid date.",
            "collected_fields": collected
        }
        
    customer = collected.get("customer")
    items = collected.get("items", [])
    
    missing = []
    if not customer:
        missing.append("Customer Name")
    if not items:
        missing.append("Item details (Item Code and Quantity)")
    else:
        for i, item in enumerate(items):
            if not item.get("item_code"):
                missing.append(f"Item Code for item {i+1}")
            if not item.get("qty"):
                missing.append(f"Quantity for item {i+1}")
                
    response_text = "I'm ready to help you create a Sales Order. However, I need the following missing details:\n"
    for item in missing:
        response_text += f"- {item}\n"
    response_text += "\nPlease provide these details."
    
    return {"final_response": response_text, "collected_fields": collected}

def call_create_sales_order_tool_node(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    customer = collected.get("customer")
    items = collected.get("items", [])
    delivery_date = collected.get("delivery_date")
    
    try:
        res = create_sales_order(customer, items, delivery_date=delivery_date)
        if res.get("success"):
            so_name = res["data"].get("name", "SO-Draft")
            response_text = f"Successfully created Sales Order: {so_name} for customer '{customer}'!"
        else:
            response_text = f"Failed to create Sales Order. Details:\n{res.get('error')}"
    except UnauthorizedError:
        response_text = "I'm having trouble authenticating with ERPNext right now. Please check the API credentials."
    except ItemNotFoundError:
        response_text = "I couldn't find one or more items matching the code specified. Please verify the Item Codes."
    except CustomerNotFoundError:
        response_text = "I couldn't find a customer matching the name specified. Please check the Customer Name."
    except Exception as e:
        response_text = f"An unexpected error occurred while communicating with ERPNext: {str(e)}"
        
    return {"final_response": response_text}

# Skill 2 (Check Inventory) Nodes
def collect_inventory_info_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    intent = state.get("detected_intent", "")
    
    # Run heuristics first
    fields = heuristic_extract(last_msg, current_fields, intent)
    
    # Call Groq LLM for natural language entity extraction
    prompt = (
        f"Extract inventory check details from the user's message: '{last_msg}'.\n"
        f"Existing fields: {current_fields}.\n"
        f"Respond strictly with JSON object containing keys:\n"
        f"- 'item_code': Item code string (e.g. 'SKU005', 'SKU009') or null\n"
        f"- 'qty': Target quantity number or null\n"
        f"- 'warehouse': Warehouse name string or null"
    )
    res = invoke_structured_llm(prompt)
    if res:
        if res.get("item_code"):
            raw_code = res["item_code"]
            m = re.search(r"\(([\w\-]+)\)", raw_code)
            if m:
                raw_code = m.group(1).strip()
            fields["item_code"] = raw_code
        if res.get("qty") is not None:
            try:
                fields["qty"] = float(res["qty"])
            except (ValueError, TypeError):
                pass
        if res.get("warehouse"):
            fields["warehouse"] = res["warehouse"]
            
    logger.info(f"Collected inventory fields: {fields}")
    return {"collected_fields": fields}

def check_missing_inventory_info(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    item_code = collected.get("item_code")
    if not item_code:
        return "ask_for_missing_inventory_info"
    return "call_check_inventory_tool"

def ask_for_missing_inventory_info_node(state: AgentState):
    response_text = (
        "I'm ready to help you check stock levels. However, I need the Item Code.\n"
        "Please provide the Item Code (e.g. SKU008)."
    )
    return {"final_response": response_text}

def call_check_inventory_tool_node(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    item_code = collected.get("item_code")
    
    try:
        res = check_stock(item_code)
    except UnauthorizedError:
        res = {"success": False, "error": "UnauthorizedError", "reason": "auth_failed"}
    except ItemNotFoundError:
        res = {"success": False, "error": "ItemNotFoundError", "reason": "item_not_found"}
    except Exception as e:
        res = {"success": False, "error": str(e), "reason": "unexpected"}
        
    return {"final_response": res}

def format_check_inventory_response_node(state: AgentState):
    res = state.get("final_response")
    collected = state.get("collected_fields", {}) or {}
    item_code = collected.get("item_code")
    target_qty = collected.get("qty")
    target_wh = collected.get("warehouse")
    
    if not isinstance(res, dict) or not res.get("success"):
        reason = res.get("reason") if isinstance(res, dict) else None
        if reason == "auth_failed":
            return {"final_response": "I'm having trouble authenticating with ERPNext right now. Please check the API credentials."}
        if reason == "item_not_found":
            return {"final_response": f"I couldn't find an item matching code '{item_code}' in ERPNext. Please check the Item Code."}
        
        error_msg = res.get("error") if isinstance(res, dict) else str(res)
        return {"final_response": f"Failed to check inventory for item '{item_code}'. Details: {error_msg}"}
        
    data = res.get("data", [])
    if not data:
        return {"final_response": f"No inventory bins found for item '{item_code}' in any warehouse."}
        
    response_text = f"Inventory status for item '{item_code}':\n"
    found_any = False
    for bin_info in data:
        wh = bin_info.get("warehouse")
        actual = bin_info.get("actual_qty", 0.0)
        reserved = bin_info.get("reserved_qty", 0.0)
        
        # If target warehouse was specified, filter by it (case-insensitive substring match)
        if target_wh and target_wh.lower() not in wh.lower():
            continue
            
        found_any = True
        response_text += f"- **Warehouse**: {wh}\n"
        response_text += f"  - Actual Qty: {actual}\n"
        response_text += f"  - Reserved Qty: {reserved}\n"
        
        if target_qty is not None:
            if actual >= target_qty:
                response_text += f"  - Status: Sufficient stock (Requested: {target_qty})\n"
            else:
                response_text += f"  - Status: INSUFFICIENT stock (Requested: {target_qty})\n"
                
    if not found_any:
        if target_wh:
            return {"final_response": f"No inventory found for item '{item_code}' in warehouse matching '{target_wh}'."}
            
    return {"final_response": response_text}


# Skill 3 (Customer Lookup) Nodes
def collect_customer_lookup_info_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    intent = state.get("detected_intent", "")
    
    # Run heuristics first
    fields = heuristic_extract(last_msg, current_fields, intent)
    
    # Call Groq LLM for natural language entity extraction
    prompt = (
        f"Extract the customer name or ID to look up from the user's message: '{last_msg}'.\n"
        f"Existing fields: {current_fields}.\n"
        f"Respond strictly with JSON object: {{\"customer_name\": \"...\"}}"
    )
    res = invoke_structured_llm(prompt)
    if res and res.get("customer_name"):
        fields["customer_name"] = res["customer_name"]
        
    logger.info(f"Collected customer lookup fields: {fields}")
    return {"collected_fields": fields}

def check_missing_customer_info(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    customer_name = collected.get("customer_name")
    if not customer_name:
        return "ask_for_missing_customer_info"
    return "call_customer_lookup_tool"

def ask_for_missing_customer_info_node(state: AgentState):
    response_text = (
        "I'm ready to help you look up customer details. However, I need the Customer Name.\n"
        "Please provide the Customer Name (e.g. Grant Plastics Ltd.)."
    )
    return {"final_response": response_text}

def call_customer_lookup_tool_node(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    customer_name = collected.get("customer_name")
    
    try:
        res = get_customer(customer_name)
    except UnauthorizedError:
        res = {"success": False, "error": "UnauthorizedError", "reason": "auth_failed"}
    except CustomerNotFoundError:
        res = {"success": False, "error": "CustomerNotFoundError", "reason": "customer_not_found"}
    except Exception as e:
        res = {"success": False, "error": str(e), "reason": "unexpected"}
        
    return {"final_response": res}

def format_customer_lookup_response_node(state: AgentState):
    res = state.get("final_response")
    collected = state.get("collected_fields", {}) or {}
    customer_name = collected.get("customer_name")
    
    if not isinstance(res, dict) or not res.get("success"):
        reason = res.get("reason") if isinstance(res, dict) else None
        if reason == "auth_failed":
            return {"final_response": "I'm having trouble authenticating with ERPNext right now. Please check the API credentials."}
        if reason == "customer_not_found":
            return {"final_response": f"I couldn't find a customer matching name '{customer_name}' in ERPNext. Please check the Customer Name."}
            
        error_msg = res.get("error") if isinstance(res, dict) else str(res)
        return {"final_response": f"Failed to look up customer '{customer_name}'. Details: {error_msg}"}
        
    data = res.get("data", {})
    if not data:
        return {"final_response": f"Customer '{customer_name}' was not found in ERPNext."}
        
    cust_name = data.get("customer_name", customer_name)
    cust_group = data.get("customer_group", "N/A")
    cust_type = data.get("customer_type", "N/A")
    email = data.get("email_id", "N/A") or "N/A"
    mobile = data.get("mobile_no", "N/A") or "N/A"
    
    response_text = (
        f"Customer details for **{cust_name}**:\n"
        f"- **Customer Group**: {cust_group}\n"
        f"- **Customer Type**: {cust_type}\n"
        f"- **Email Address**: {email}\n"
        f"- **Mobile Number**: {mobile}\n"
    )
    return {"final_response": response_text}

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
    return {"final_response": response_text}

def unauthorized_response_node(state: AgentState):
    response_text = "Permission Denied: You do not have the required role permissions to perform this action."
    return {"final_response": response_text}

def format_response_node(state: AgentState):
    final_resp = state.get("final_response", "")
    return {"messages": [AIMessage(content=final_resp)]}

def route_by_intent_and_auth(state: AgentState):
    intent = state.get("detected_intent", "fallback")
    roles = state.get("user_roles", [])
    
    role_rules = {
        "create_sales_order": ["Sales User", "Sales Manager", "System Manager", "Administrator"],
        "stock_check": ["Stock User", "Stock Manager", "Sales User", "Sales Manager", "System Manager", "Administrator"],
        "customer_lookup": ["Sales User", "Sales Manager", "Accounts User", "Accounts Manager", "System Manager", "Administrator"]
    }
    
    if intent in role_rules:
        allowed = role_rules[intent]
        if not any(r in roles for r in allowed):
            logger.warning(f"User unauthorized for intent {intent}. User roles: {roles}")
            return "unauthorized"
            
    return intent

# Stock Check Nodes & Routers
def collect_stock_check_info_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    
    fields = heuristic_extract_stock(last_msg, current_fields)
    
    prompt = (
        f"Extract the item code to check stock for from the user's message: '{last_msg}'.\n"
        f"Existing fields: {current_fields}.\n"
        f"Respond strictly with JSON object: {{\"item_code\": \"...\"}}"
    )
    res = invoke_structured_llm(prompt)
    if res and res.get("item_code"):
        fields["item_code"] = res["item_code"]
            
    logger.info(f"Collected stock fields: {fields}")
    return {"collected_fields": fields}

def check_stock_missing_info(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    item_code = collected.get("item_code")
    if not item_code:
        return "ask_for_stock_item"
    return "call_check_stock_tool"

# Graph Construction
workflow = StateGraph(AgentState)

# Register common nodes
workflow.add_node("classify_intent", classify_intent_node)
workflow.add_node("fallback_response", fallback_response_node)
workflow.add_node("unauthorized_response", unauthorized_response_node)
workflow.add_node("format_response", format_response_node)

# Register Sales Order nodes
workflow.add_node("collect_sales_order_info", collect_sales_order_info_node)
workflow.add_node("ask_for_missing_info", ask_for_missing_info_node)
workflow.add_node("call_create_sales_order_tool", call_create_sales_order_tool_node)

# Skill 2 (Check Inventory) Nodes
workflow.add_node("collect_inventory_info", collect_inventory_info_node)
workflow.add_node("ask_for_missing_inventory_info", ask_for_missing_inventory_info_node)
workflow.add_node("call_check_inventory_tool", call_check_inventory_tool_node)
workflow.add_node("format_check_inventory_response", format_check_inventory_response_node)

# Skill 3 (Customer Lookup) Nodes
workflow.add_node("collect_customer_lookup_info", collect_customer_lookup_info_node)
workflow.add_node("ask_for_missing_customer_info", ask_for_missing_customer_info_node)
workflow.add_node("call_customer_lookup_tool", call_customer_lookup_tool_node)
workflow.add_node("format_customer_lookup_response", format_customer_lookup_response_node)

workflow.set_entry_point("classify_intent")

# Wire conditional routing from classify_intent
workflow.add_conditional_edges(
    "classify_intent",
    route_by_intent_and_auth,
    {
        "create_sales_order": "collect_sales_order_info",
        "check_inventory": "collect_inventory_info",
        "stock_check": "collect_inventory_info",
        "customer_lookup": "collect_customer_lookup_info",
        "unauthorized": "unauthorized_response",
        "fallback": "fallback_response"
    }
)

# Wire Sales Order loop
workflow.add_conditional_edges(
    "collect_sales_order_info",
    check_missing_info,
    {
        "ask_for_missing_info": "ask_for_missing_info",
        "call_create_sales_order_tool": "call_create_sales_order_tool"
    }
)

# Wire Inventory loop
workflow.add_conditional_edges(
    "collect_inventory_info",
    check_missing_inventory_info,
    {
        "ask_for_missing_inventory_info": "ask_for_missing_inventory_info",
        "call_check_inventory_tool": "call_check_inventory_tool"
    }
)

# Wire Customer Lookup loop
workflow.add_conditional_edges(
    "collect_customer_lookup_info",
    check_missing_customer_info,
    {
        "ask_for_missing_customer_info": "ask_for_missing_customer_info",
        "call_customer_lookup_tool": "call_customer_lookup_tool"
    }
)

# Sales Order routing
workflow.add_edge("ask_for_missing_info", "format_response")
workflow.add_edge("call_create_sales_order_tool", "format_response")

# Inventory routing
workflow.add_edge("ask_for_missing_inventory_info", "format_response")
workflow.add_edge("call_check_inventory_tool", "format_check_inventory_response")
workflow.add_edge("format_check_inventory_response", "format_response")

# Customer Lookup routing
workflow.add_edge("ask_for_missing_customer_info", "format_response")
workflow.add_edge("call_customer_lookup_tool", "format_customer_lookup_response")
workflow.add_edge("format_customer_lookup_response", "format_response")

workflow.add_edge("unauthorized_response", "format_response")
workflow.add_edge("fallback_response", "format_response")
workflow.add_edge("format_response", END)

# Compiled Graph
compiled_graph = workflow.compile()
