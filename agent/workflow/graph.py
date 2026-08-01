import os
import logging
import re
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, END

from workflow.state import AgentState
from tools.erpnext_client import create_sales_order

logger = logging.getLogger("workflow_graph")
logger.setLevel(logging.INFO)

# Structured Output Schemas
class IntentClassification(BaseModel):
    intent: str = Field(
        description="Intent of the user message. Must be either 'create_sales_order' or 'fallback'."
    )

class SalesOrderItem(BaseModel):
    item_code: Optional[str] = Field(None, description="The product or item code/name.")
    qty: Optional[float] = Field(None, description="Quantity of the item.")
    rate: Optional[float] = Field(None, description="Rate/price of the item.")

class SalesOrderExtraction(BaseModel):
    customer: Optional[str] = Field(None, description="The customer name/ID.")
    items: List[SalesOrderItem] = Field(default_factory=list, description="List of items in the sales order.")

# Heuristics Fallbacks for Offline/Mock Testing
def heuristic_classify(text: str) -> str:
    cleaned = text.lower()
    if any(keyword in cleaned for keyword in ["sales order", "create order", "place an order"]):
        return "create_sales_order"
    return "fallback"

def heuristic_extract(text: str, current_fields: dict) -> dict:
    fields = dict(current_fields)
    cleaned = text.lower()
    
    # Extract Customer (e.g., "for customer Acme", "for Acme")
    if not fields.get("customer"):
        cust_match = re.search(r"for customer ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b)", text, re.IGNORECASE)
        if not cust_match:
            cust_match = re.search(r"for ([\w\s\-\.]+?)(?:,|$|\bitem\b|\bwith\b)", text, re.IGNORECASE)
        if cust_match:
            fields["customer"] = cust_match.group(1).strip()
            
    # Extract Items and Qty (e.g., "item widget qty 5", "item: widget, quantity 5")
    if not fields.get("items"):
        item_match = re.search(r"item ([\w\s\-\.]+?)(?:,|$|\bqty\b|\bquantity\b)", text, re.IGNORECASE)
        qty_match = re.search(r"(?:qty|quantity) (\d+)", text, re.IGNORECASE)
        if item_match and qty_match:
            fields["items"] = [{
                "item_code": item_match.group(1).strip(),
                "qty": float(qty_match.group(1))
            }]
    return fields

# Nodes Implementation
def classify_intent_node(state: AgentState):
    messages = state.get("messages", [])
    if not messages:
        return {"detected_intent": "fallback"}
        
    last_msg = messages[-1].content
    intent = heuristic_classify(last_msg)
    
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if api_key and api_key != "mock_google_api_key":
        try:
            llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=api_key, temperature=0)
            structured_llm = llm.with_structured_output(IntentClassification)
            res = structured_llm.invoke([{"role": "user", "content": last_msg}])
            if res and res.intent in ["create_sales_order", "fallback"]:
                intent = res.intent
        except Exception as e:
            logger.warning(f"LLM Classification failed: {e}. Using heuristic fallback.")
            
    logger.info(f"Classified intent: {intent}")
    return {"detected_intent": intent}

def collect_sales_order_info_node(state: AgentState):
    messages = state.get("messages", [])
    last_msg = messages[-1].content if messages else ""
    current_fields = state.get("collected_fields", {}) or {}
    
    # Run heuristics first
    fields = heuristic_extract(last_msg, current_fields)
    
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if api_key and api_key != "mock_google_api_key":
        try:
            llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=api_key, temperature=0)
            structured_llm = llm.with_structured_output(SalesOrderExtraction)
            
            prompt = (
                f"Extract Sales Order details from the user's message. "
                f"Existing fields: {current_fields}. "
                f"Update fields if new info is given. "
                f"User message: {last_msg}"
            )
            res = structured_llm.invoke(prompt)
            if res:
                if res.customer:
                    fields["customer"] = res.customer
                if res.items:
                    fields["items"] = []
                    for item in res.items:
                        fields["items"].append({
                            "item_code": item.item_code,
                            "qty": item.qty,
                            "rate": item.rate
                        })
        except Exception as e:
            logger.warning(f"LLM extraction failed: {e}. Using heuristic values.")
            
    logger.info(f"Collected fields: {fields}")
    return {"collected_fields": fields}

def check_missing_info(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
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
    
    return {"final_response": response_text}

def call_create_sales_order_tool_node(state: AgentState):
    collected = state.get("collected_fields", {}) or {}
    customer = collected.get("customer")
    items = collected.get("items", [])
    
    # Execute actual ERPNext client tool
    res = create_sales_order(customer, items)
    
    if res["success"]:
        so_name = res["data"].get("name", "SO-Draft")
        response_text = f"Successfully created Sales Order: {so_name} for customer '{customer}'!"
    else:
        response_text = f"Failed to create Sales Order. Details:\n{res['error']}"
        
    return {"final_response": response_text}

def fallback_response_node(state: AgentState):
    response_text = (
        "I'm an AI assistant for ERPNext.\n"
        "Currently, I can assist you with:\n"
        "- Creating a Sales Order (e.g. 'Create a sales order for customer ABC with item XYZ qty 10')\n"
        "\n"
        "How can I help you today?"
    )
    return {"final_response": response_text}

def format_response_node(state: AgentState):
    final_resp = state.get("final_response", "")
    return {"messages": [AIMessage(content=final_resp)]}

# Graph Construction
workflow = StateGraph(AgentState)

workflow.add_node("classify_intent", classify_intent_node)
workflow.add_node("collect_sales_order_info", collect_sales_order_info_node)
workflow.add_node("ask_for_missing_info", ask_for_missing_info_node)
workflow.add_node("call_create_sales_order_tool", call_create_sales_order_tool_node)
workflow.add_node("fallback_response", fallback_response_node)
workflow.add_node("format_response", format_response_node)

workflow.set_entry_point("classify_intent")

workflow.add_conditional_edges(
    "classify_intent",
    lambda state: state["detected_intent"],
    {
        "create_sales_order": "collect_sales_order_info",
        "fallback": "fallback_response"
    }
)

workflow.add_conditional_edges(
    "collect_sales_order_info",
    check_missing_info,
    {
        "ask_for_missing_info": "ask_for_missing_info",
        "call_create_sales_order_tool": "call_create_sales_order_tool"
    }
)

workflow.add_edge("ask_for_missing_info", "format_response")
workflow.add_edge("call_create_sales_order_tool", "format_response")
workflow.add_edge("fallback_response", "format_response")

workflow.add_edge("format_response", END)

# Compiled Graph
compiled_graph = workflow.compile()
