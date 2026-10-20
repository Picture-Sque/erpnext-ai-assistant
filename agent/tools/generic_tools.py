import os
import re
import json
import logging
import httpx
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("generic_tools")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

ORDER_BY_PATTERN = re.compile(
    r"^\s*[a-zA-Z_][a-zA-Z0-9_]*(?:\s+(?:asc|desc))?"
    r"(?:\s*,\s*[a-zA-Z_][a-zA-Z0-9_]*(?:\s+(?:asc|desc))?)*\s*$",
    re.IGNORECASE
)

try:
    from agent.tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL
except ImportError:
    from tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL

DEFAULT_WHITELIST = [
    "Customer", "Item", "Sales Order", "Bin",
    "Quotation", "Sales Invoice", "Purchase Order",
    # Added for new skills (Batch A-C, September 2026):
    "Supplier",              # create-purchase-order entity resolution
    "Sales Order Item",      # sales-analytics-report aggregate on child table
    "Purchase Order Item",   # purchase-order-lookup item-based PO check
    "Company",               # Dynamic company resolution
    "Warehouse",             # Dynamic warehouse resolution
    # Added for Tier 1 reporting skills:
    "Delivery Note",         # delivery-status-tracking
    "Delivery Note Item",    # delivery-status-tracking child table filter
    "Purchase Receipt",      # purchase-receipt-report
    "Purchase Receipt Item", # purchase-receipt-report child table filter
    "Stock Ledger Entry",    # stock-movement-analysis
]

_cached_company = None
_cached_warehouse = None

def get_default_company() -> str:
    global _cached_company
    if _cached_company:
        return _cached_company
    env_val = os.getenv("DEFAULT_COMPANY")
    if env_val:
        _cached_company = env_val.strip()
        return _cached_company
    try:
        headers = get_auth_headers()
        with httpx.Client() as client:
            resp = client.get(f"{ERPNEXT_BASE_URL}/api/resource/Company", headers=headers, timeout=10.0)
            if resp.is_success:
                data = resp.json().get("data", [])
                if data:
                    _cached_company = data[0]["name"]
                    return _cached_company
    except Exception as e:
        logger.warning(f"Failed to fetch default company from ERPNext: {e}")
    _cached_company = "Metal Forms"
    return _cached_company

def get_default_warehouse(company: Optional[str] = None) -> Optional[str]:
    global _cached_warehouse
    if _cached_warehouse:
        return _cached_warehouse
    env_val = os.getenv("DEFAULT_WAREHOUSE")
    if env_val:
        _cached_warehouse = env_val.strip()
        return _cached_warehouse
    comp = company or get_default_company()
    try:
        headers = get_auth_headers()
        with httpx.Client() as client:
            resp = client.get(
                f"{ERPNEXT_BASE_URL}/api/resource/Warehouse",
                headers=headers,
                params={"filters": json.dumps([["company", "=", comp]])},
                timeout=10.0
            )
            if resp.is_success:
                data = resp.json().get("data", [])
                for wh in data:
                    w_name = wh.get("name", "")
                    if "stores" in w_name.lower():
                        _cached_warehouse = w_name
                        return _cached_warehouse
                if data:
                    _cached_warehouse = data[0]["name"]
                    return _cached_warehouse
    except Exception as e:
        logger.warning(f"Failed to fetch default warehouse from ERPNext: {e}")
    _cached_warehouse = "Stores - MF"
    return _cached_warehouse


def get_allowed_doctypes() -> List[str]:
    env_val = os.getenv("DOCTYPE_WHITELIST") or os.getenv("ALLOWED_DOCTYPES")
    if env_val:
        items = [item.strip() for item in env_val.split(",") if item.strip()]
        if items:
            return items
    return DEFAULT_WHITELIST

def is_doctype_allowed(doctype_name: str) -> bool:
    if not doctype_name:
        return False
    allowed = get_allowed_doctypes()
    allowed_lower = {dt.lower() for dt in allowed}
    return doctype_name.strip().lower() in allowed_lower

def extract_erpnext_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            server_msgs = body.get("_server_messages")
            if server_msgs:
                try:
                    msgs = json.loads(server_msgs) if isinstance(server_msgs, str) else server_msgs
                    parsed = []
                    for m in msgs:
                        if isinstance(m, str):
                            try:
                                m_dict = json.loads(m)
                                if isinstance(m_dict, dict) and "message" in m_dict:
                                    parsed.append(str(m_dict["message"]))
                                else:
                                    parsed.append(str(m_dict))
                            except Exception:
                                parsed.append(m)
                        elif isinstance(m, dict) and "message" in m:
                            parsed.append(str(m["message"]))
                    if parsed:
                        return " | ".join(parsed)
                except Exception:
                    pass
            if body.get("exception"):
                return str(body["exception"])
            if body.get("message"):
                return str(body["message"])
            return json.dumps(body)
    except Exception:
        pass
    return f"HTTP {response.status_code}: {response.text}"

def _format_result(status: str, data: Any = None, error: Optional[str] = None, has_more: bool = False) -> dict:
    """
    Format standard tool response.
    status must be one of: "success", "empty", "permission_denied", "not_found", "system_error"
    """
    return {
        "status": status,
        "data": data,
        "error": error,
        "has_more": has_more,
        "success": status in ("success", "empty")
    }

def get_list(
    doctype: str,
    filters: Optional[dict] = None,
    fields: Optional[list] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None
) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist. Allowed DocTypes: {', '.join(get_allowed_doctypes())}")

    if order_by is not None:
        if not isinstance(order_by, str) or not ORDER_BY_PATTERN.match(order_by):
            return _format_result("system_error", error=f"Invalid order_by clause: '{order_by}'. Must be valid comma-separated field names with optional ASC/DESC.")

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype}"
    headers = get_auth_headers()
    
    query_params = {}
    if filters:
        if isinstance(filters, list) or isinstance(filters, dict):
            query_params["filters"] = json.dumps(filters)
        else:
            query_params["filters"] = str(filters)
    if fields:
        if isinstance(fields, list):
            query_params["fields"] = json.dumps(fields)
        else:
            query_params["fields"] = str(fields)
    if order_by:
        query_params["order_by"] = order_by.strip()
    
    # We fetch limit + 1 to determine if has_more is true
    actual_limit = limit if limit else 20
    query_params["limit_page_length"] = str(actual_limit + 1)

    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=query_params, timeout=60.0)
            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", [])
                    has_more = len(data) > actual_limit
                    if has_more:
                        data = data[:actual_limit]
                    return _format_result("success" if data else "empty", data=data, has_more=has_more)
                except Exception:
                    return _format_result("system_error", error="Invalid JSON response from ERPNext")
            elif response.status_code == 404:
                return _format_result("not_found", error=extract_erpnext_error(response))
            elif response.status_code in (401, 403):
                return _format_result("permission_denied", error=extract_erpnext_error(response))
            else:
                return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))

def create_document(doctype: str, fields: dict) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    from datetime import datetime, timedelta
    payload = dict(fields)

    # Dynamic default resolution for Sales Order and Purchase Order
    if doctype in ("Sales Order", "Purchase Order"):
        if not payload.get("company"):
            payload["company"] = get_default_company()
        if not payload.get("transaction_date"):
            payload["transaction_date"] = datetime.now().strftime("%Y-%m-%d")
        
        default_wh = get_default_warehouse(payload.get("company"))
        if doctype == "Sales Order":
            if not payload.get("delivery_date"):
                payload["delivery_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if not item.get("warehouse"):
                        item["warehouse"] = default_wh
                    if not item.get("delivery_date"):
                        item["delivery_date"] = payload["delivery_date"]
        elif doctype == "Purchase Order":
            if not payload.get("schedule_date"):
                payload["schedule_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if not item.get("warehouse"):
                        item["warehouse"] = default_wh
                    if not item.get("schedule_date"):
                        item["schedule_date"] = payload["schedule_date"]

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype}"
    headers = get_auth_headers()
    try:
        with httpx.Client() as client:
            response = client.post(url, headers=headers, json=payload, timeout=60.0)
            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                    return _format_result("success", data=data)
                except Exception:
                    return _format_result("system_error", error="Invalid JSON response")
            elif response.status_code == 404:
                return _format_result("not_found", error=extract_erpnext_error(response))
            elif response.status_code in (401, 403):
                return _format_result("permission_denied", error=extract_erpnext_error(response))
            else:
                return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))

def update_document(doctype: str, name: str, fields: dict) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    payload = dict(fields)
    # Special handling for Sales Order parent-synced fields:
    # In ERPNext, Sales Order header delivery_date is synced from items[].delivery_date.
    # When updating delivery_date, we must also update delivery_date on every row in items,
    # otherwise ERPNext will revert the header date back to the child items' date.
    # Note: Other parent-synced fields (e.g. warehouse, or rate/qty which recalculate totals)
    # may require similar child table synchronization in future enhancements.
    if doctype == "Sales Order" and "delivery_date" in payload and "items" not in payload:
        try:
            current_doc_res = get_document(doctype, name)
            if current_doc_res.get("status") == "success":
                current_items = current_doc_res.get("data", {}).get("items", [])
                if current_items:
                    updated_items = []
                    for it in current_items:
                        it_copy = dict(it)
                        it_copy["delivery_date"] = payload["delivery_date"]
                        updated_items.append(it_copy)
                    payload["items"] = updated_items
        except Exception as e:
            logger.warning(f"Failed to synchronize items delivery_date for {doctype} {name}: {e}")

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype}/{name}"
    headers = get_auth_headers()
    try:
        with httpx.Client() as client:
            response = client.put(url, headers=headers, json=payload, timeout=60.0)
            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                    return _format_result("success", data=data)
                except Exception:
                    return _format_result("system_error", error="Invalid JSON response")
            elif response.status_code == 404:
                return _format_result("not_found", error=extract_erpnext_error(response))
            elif response.status_code in (401, 403):
                return _format_result("permission_denied", error=extract_erpnext_error(response))
            else:
                return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))

def delete_document(doctype: str, name: str) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype}/{name}"
    headers = get_auth_headers()
    try:
        with httpx.Client() as client:
            response = client.delete(url, headers=headers, timeout=60.0)
            if response.is_success:
                return _format_result("success", data="deleted")
            elif response.status_code == 404:
                return _format_result("not_found", error=extract_erpnext_error(response))
            elif response.status_code in (401, 403):
                return _format_result("permission_denied", error=extract_erpnext_error(response))
            else:
                return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))

def get_document(doctype: str, name: str) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype}/{name}"
    headers = get_auth_headers()
    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, timeout=60.0)
            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                    return _format_result("success", data=data)
                except Exception:
                    return _format_result("system_error", error="Invalid JSON response")
            elif response.status_code == 404:
                return _format_result("not_found", error=extract_erpnext_error(response))
            elif response.status_code in (401, 403):
                return _format_result("permission_denied", error=extract_erpnext_error(response))
            else:
                return _format_result("system_error", error=extract_erpnext_error(response))
    except Exception as e:
        return _format_result("system_error", error=str(e))

def search_document(doctype: str, query: str) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")

    search_fields_map = {
        "Customer": "customer_name",
        "Item": "item_name",
        "Sales Order": "name",
        "Bin": "item_code",
        "Quotation": "name",
        "Sales Invoice": "name",
        "Purchase Order": "name",
        "Supplier": "supplier_name"
    }
    display_fields_map = {
        "Customer": ["name", "customer_name", "customer_group", "territory"],
        "Item": ["name", "item_code", "item_name", "item_group"],
        "Sales Order": ["name", "customer", "status"],
        "Bin": ["name", "item_code", "warehouse", "actual_qty"],
        "Quotation": ["name", "customer", "status"],
        "Sales Invoice": ["name", "customer", "status"],
        "Purchase Order": ["name", "supplier", "status"],
        "Supplier": ["name", "supplier_name", "supplier_group"]
    }
    
    primary_field = search_fields_map.get(doctype, "name")
    display_fields = display_fields_map.get(doctype, ["name"])
    
    filters = [[primary_field, "like", f"%{query}%"]]
    
    return get_list(doctype, filters=filters, fields=display_fields, limit=10)

def get_count(doctype: str, filters: Optional[dict] = None) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")
    
    # Fetching name to keep payload size minimal
    res = get_list(doctype, filters=filters, fields=["name"], limit=999999)
    if res["status"] in ("success", "empty"):
        data_list = res.get("data", [])
        count = len(data_list)
        return _format_result("empty" if count == 0 else "success", data=count)
    return res

def aggregate(doctype: str, group_by: str, metric: str, aggregation_fn: str, filters: Optional[dict] = None, sort: Optional[str] = None, limit: Optional[int] = None) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")
    
    # We compute aggregation tool-level, fetching all required fields first.
    fields = ["name", group_by, metric]
    res = get_list(doctype, filters=filters, fields=fields, limit=999999)
    if res["status"] not in ("success", "empty"):
        return res
    
    data = res.get("data", [])
    if not data:
        return _format_result("empty", data=[])
    
    agg = {}
    counts = {}
    
    for row in data:
        g_val = str(row.get(group_by, "Unknown"))
        m_val = float(row.get(metric, 0)) if aggregation_fn != "count" else 1
        
        if g_val not in agg:
            agg[g_val] = 0 if aggregation_fn in ("sum", "avg", "count") else m_val
            counts[g_val] = 0
            
        counts[g_val] += 1
        if aggregation_fn in ("sum", "avg", "count"):
            agg[g_val] += m_val
        elif aggregation_fn == "max":
            agg[g_val] = max(agg[g_val], m_val)
        elif aggregation_fn == "min":
            agg[g_val] = min(agg[g_val], m_val)
            
    result_list = []
    for g_val, val in agg.items():
        if aggregation_fn == "avg" and counts[g_val] > 0:
            val = val / counts[g_val]
        result_list.append({group_by: g_val, metric: val})
        
    if sort:
        parts = sort.split()
        if len(parts) == 2 and parts[1].lower() == "desc":
            result_list.sort(key=lambda x: x.get(parts[0], 0), reverse=True)
        else:
            result_list.sort(key=lambda x: x.get(parts[0], 0))
            
    if limit:
        result_list = result_list[:limit]
        
    return _format_result("success", data=result_list)

def cancel_document(doctype: str, name: str) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")
        
    return update_document(doctype, name, {"docstatus": 2})

def submit_document(doctype: str, name: str) -> dict:
    if not is_doctype_allowed(doctype):
        return _format_result("permission_denied", error=f"DocType '{doctype}' is not permitted by whitelist.")
        
    return update_document(doctype, name, {"docstatus": 1})
