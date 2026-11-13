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
_cached_warehouses: Dict[str, str] = {}

def get_default_company(reload: bool = False) -> str:
    global _cached_company
    if _cached_company and not reload:
        return _cached_company

    headers = get_auth_headers()
    try:
        with httpx.Client() as client:
            resp = client.get(
                f"{ERPNEXT_BASE_URL}/api/resource/Company",
                headers=headers,
                params={"limit_page_length": 100},
                timeout=10.0
            )
            if not resp.is_success:
                err_msg = extract_erpnext_error(resp) or resp.text
                raise RuntimeError(f"Failed to fetch Company list from ERPNext ({resp.status_code}): {err_msg}")
            data = resp.json().get("data", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Network error or invalid JSON while fetching Company list from ERPNext: {e}")
        raise RuntimeError(f"Failed to fetch Company list from ERPNext: {e}") from e

    company_names = [c["name"] for c in data if isinstance(c, dict) and "name" in c]

    env_val = os.getenv("DEFAULT_COMPANY")
    if env_val:
        target = env_val.strip()
        if target in company_names:
            _cached_company = target
            return _cached_company
        logger.warning(f"DEFAULT_COMPANY '{target}' does not exist in ERPNext companies: {company_names}")

    if len(company_names) == 1:
        _cached_company = company_names[0]
        return _cached_company
    elif len(company_names) == 0:
        raise RuntimeError("No companies found in ERPNext.")
    else:
        raise RuntimeError("Default company is not configured.")

def get_default_warehouse(company: Optional[str] = None, reload: bool = False) -> str:
    global _cached_warehouses
    comp = company or get_default_company(reload=reload)

    if comp in _cached_warehouses and not reload:
        return _cached_warehouses[comp]

    headers = get_auth_headers()
    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{ERPNEXT_BASE_URL}/api/resource/Warehouse",
                headers=headers,
                params={
                    "filters": json.dumps([["company", "=", comp]]),
                    "fields": json.dumps(["name", "warehouse_name", "company", "is_group"]),
                    "limit_page_length": 100,
                },
                timeout=10.0
            )
            if not resp.is_success:
                err_msg = extract_erpnext_error(resp) or resp.text
                raise RuntimeError(f"Failed to fetch Warehouse list for '{comp}' from ERPNext ({resp.status_code}): {err_msg}")

            data = resp.json().get("data", [])
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"Network error or invalid JSON while fetching Warehouse list for '{comp}' from ERPNext: {e}")
            raise RuntimeError(f"Failed to fetch Warehouse list for '{comp}' from ERPNext: {e}") from e

        warehouses = [w for w in data if isinstance(w, dict)]
        if not warehouses:
            raise RuntimeError(f"No warehouses found for company '{comp}' in ERPNext.")

        warehouse_names = [w["name"] for w in warehouses if "name" in w]

        # 1. DEFAULT_WAREHOUSE env is honored only if its company matches target company
        env_val = os.getenv("DEFAULT_WAREHOUSE")
        if env_val:
            target_wh = env_val.strip()
            for w in warehouses:
                if w.get("name") == target_wh and w.get("company") == comp:
                    _cached_warehouses[comp] = target_wh
                    return target_wh
            logger.info(f"DEFAULT_WAREHOUSE '{target_wh}' does not belong to company '{comp}'; ignoring.")

        # 2. Prefer Company or Stock Settings default warehouse if available and belongs to company
        pref_wh = None
        try:
            c_resp = client.get(f"{ERPNEXT_BASE_URL}/api/resource/Company/{comp}", headers=headers, timeout=5.0)
            if c_resp.is_success:
                c_def = (c_resp.json().get("data") or {}).get("default_warehouse")
                if c_def and c_def in warehouse_names:
                    pref_wh = c_def
            else:
                logger.warning(f"Failed to fetch Company '{comp}' from ERPNext ({c_resp.status_code})")
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"Failed to fetch default warehouse from Company '{comp}': {e}")

        if not pref_wh:
            try:
                s_resp = client.get(f"{ERPNEXT_BASE_URL}/api/resource/Stock Settings/Stock Settings", headers=headers, timeout=5.0)
                if s_resp.is_success:
                    s_def = (s_resp.json().get("data") or {}).get("default_warehouse")
                    if s_def and s_def in warehouse_names:
                        pref_wh = s_def
                else:
                    logger.warning(f"Failed to fetch Stock Settings from ERPNext ({s_resp.status_code})")
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(f"Failed to fetch default warehouse from Stock Settings: {e}")

        if pref_wh:
            _cached_warehouses[comp] = pref_wh
            return pref_wh

        # 3. Existing 'stores' heuristic
        for wh in warehouses:
            w_name = wh.get("name", "")
            w_label = wh.get("warehouse_name", "")
            if "stores" in w_name.lower() or "stores" in w_label.lower():
                _cached_warehouses[comp] = w_name
                return w_name

        # 4. Fallback to first warehouse (prefer non-group)
        leaf = [wh["name"] for wh in warehouses if not wh.get("is_group") and "name" in wh]
        chosen = leaf[0] if leaf else warehouse_names[0]
        _cached_warehouses[comp] = chosen
        return chosen


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
        try:
            if not payload.get("company"):
                payload["company"] = get_default_company()

            items = payload.get("items")
            default_wh = None
            if isinstance(items, list) and any(isinstance(it, dict) and not it.get("warehouse") for it in items):
                default_wh = get_default_warehouse(payload.get("company"))
        except RuntimeError as e:
            err_msg = str(e)
            if "Default company is not configured" in err_msg:
                err_msg = "Default company is not configured. Set DEFAULT_COMPANY or pass a company."
            logger.error(f"Failed to resolve default company or warehouse: {err_msg}")
            return _format_result("system_error", error=err_msg)

        if not payload.get("transaction_date"):
            payload["transaction_date"] = datetime.now().strftime("%Y-%m-%d")
        if doctype == "Sales Order":
            if not payload.get("delivery_date"):
                payload["delivery_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if default_wh and not item.get("warehouse"):
                        item["warehouse"] = default_wh
                    if not item.get("delivery_date"):
                        item["delivery_date"] = payload["delivery_date"]
        elif doctype == "Purchase Order":
            if not payload.get("schedule_date"):
                payload["schedule_date"] = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            if "items" in payload and isinstance(payload["items"], list):
                for item in payload["items"]:
                    if default_wh and not item.get("warehouse"):
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
