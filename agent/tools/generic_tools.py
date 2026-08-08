import os
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

# Import shared ERPNext client settings and auth headers
try:
    from agent.tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL
except ImportError:
    from tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL

DEFAULT_WHITELIST = ["Customer", "Item", "Sales Order", "Bin"]


def get_allowed_doctypes() -> List[str]:
    """
    Retrieves the list of whitelisted DocTypes from environment or configuration.
    Defaults to ["Customer", "Item", "Sales Order", "Bin"].
    """
    env_val = os.getenv("DOCTYPE_WHITELIST") or os.getenv("ALLOWED_DOCTYPES")
    if env_val:
        items = [item.strip() for item in env_val.split(",") if item.strip()]
        if items:
            return items
    return DEFAULT_WHITELIST


def is_doctype_allowed(doctype_name: str) -> bool:
    """
    Checks if a given DocType name is present in the whitelist (case-insensitive).
    """
    if not doctype_name:
        return False
    allowed = get_allowed_doctypes()
    allowed_lower = {dt.lower() for dt in allowed}
    return doctype_name.strip().lower() in allowed_lower


def extract_erpnext_error(response: httpx.Response) -> str:
    """
    Extracts human-readable error details from ERPNext HTTP responses.
    Handles Frappe _server_messages, exception strings, message fields, or raw text.
    """
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


def add_doctype(doctype_name: str, parameters: dict) -> dict:
    """
    Creates a new document of type <doctype_name> via POST /api/resource/<doctype_name>.
    Body = parameters (passed through as-is).
    """
    if not is_doctype_allowed(doctype_name):
        allowed = get_allowed_doctypes()
        msg = f"DocType '{doctype_name}' is not permitted by whitelist. Allowed DocTypes: {', '.join(allowed)}"
        logger.warning(msg)
        return {
            "status_code": 403,
            "success": False,
            "data": None,
            "error": msg
        }

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype_name}"
    headers = get_auth_headers()
    body_payload = parameters if isinstance(parameters, dict) else {}

    logger.info(f"REST Call: POST {url}")
    logger.info(f"Payload: {body_payload}")

    try:
        with httpx.Client() as client:
            response = client.post(url, headers=headers, json=body_payload, timeout=60.0)
            logger.info(f"Response Status: {response.status_code}")

            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": True,
                    "data": data,
                    "error": None
                }
            else:
                error_msg = extract_erpnext_error(response)
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": False,
                    "data": data,
                    "error": error_msg
                }
    except Exception as e:
        logger.exception(f"Exception in add_doctype for '{doctype_name}'")
        return {
            "status_code": 500,
            "success": False,
            "data": None,
            "error": str(e)
        }


def list_doctype(doctype_name: str, parameters: Optional[dict] = None) -> dict:
    """
    Queries documents of type <doctype_name> via GET /api/resource/<doctype_name>.
    Encodes filters, fields, limit/limit_page_length, limit_start, order_by into query string.
    """
    if not is_doctype_allowed(doctype_name):
        allowed = get_allowed_doctypes()
        msg = f"DocType '{doctype_name}' is not permitted by whitelist. Allowed DocTypes: {', '.join(allowed)}"
        logger.warning(msg)
        return {
            "status_code": 403,
            "success": False,
            "data": None,
            "error": msg
        }

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype_name}"
    headers = get_auth_headers()
    raw_params = dict(parameters) if isinstance(parameters, dict) else {}

    # Build ERPNext expected query params dictionary
    query_params: Dict[str, Any] = {}

    for key, val in raw_params.items():
        if key == "filters":
            if isinstance(val, (list, dict)):
                query_params["filters"] = json.dumps(val)
            else:
                query_params["filters"] = str(val)
        elif key == "fields":
            if isinstance(val, list):
                query_params["fields"] = json.dumps(val)
            else:
                query_params["fields"] = str(val)
        elif key == "limit" and "limit_page_length" not in raw_params:
            query_params["limit_page_length"] = str(val)
        elif isinstance(val, (list, dict)):
            query_params[key] = json.dumps(val)
        else:
            query_params[key] = str(val)

    logger.info(f"REST Call: GET {url}")
    logger.info(f"Query Params: {query_params}")

    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=query_params, timeout=60.0)
            logger.info(f"Response Status: {response.status_code}")

            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": True,
                    "data": data,
                    "error": None
                }
            else:
                error_msg = extract_erpnext_error(response)
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": False,
                    "data": data,
                    "error": error_msg
                }
    except Exception as e:
        logger.exception(f"Exception in list_doctype for '{doctype_name}'")
        return {
            "status_code": 500,
            "success": False,
            "data": None,
            "error": str(e)
        }


def update_doctype(doctype_name: str, id: str, parameters: dict) -> dict:
    """
    Updates an existing document of type <doctype_name> via PUT /api/resource/<doctype_name>/<id>.
    Body = parameters (passed through as-is).
    """
    if not is_doctype_allowed(doctype_name):
        allowed = get_allowed_doctypes()
        msg = f"DocType '{doctype_name}' is not permitted by whitelist. Allowed DocTypes: {', '.join(allowed)}"
        logger.warning(msg)
        return {
            "status_code": 403,
            "success": False,
            "data": None,
            "error": msg
        }

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype_name}/{id}"
    headers = get_auth_headers()
    body_payload = parameters if isinstance(parameters, dict) else {}

    logger.info(f"REST Call: PUT {url}")
    logger.info(f"Payload: {body_payload}")

    try:
        with httpx.Client() as client:
            response = client.put(url, headers=headers, json=body_payload, timeout=60.0)
            logger.info(f"Response Status: {response.status_code}")

            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": True,
                    "data": data,
                    "error": None
                }
            else:
                error_msg = extract_erpnext_error(response)
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": False,
                    "data": data,
                    "error": error_msg
                }
    except Exception as e:
        logger.exception(f"Exception in update_doctype for '{doctype_name}/{id}'")
        return {
            "status_code": 500,
            "success": False,
            "data": None,
            "error": str(e)
        }


def delete_doctype(doctype_name: str, id: str) -> dict:
    """
    Deletes a document of type <doctype_name> via DELETE /api/resource/<doctype_name>/<id>.
    """
    if not is_doctype_allowed(doctype_name):
        allowed = get_allowed_doctypes()
        msg = f"DocType '{doctype_name}' is not permitted by whitelist. Allowed DocTypes: {', '.join(allowed)}"
        logger.warning(msg)
        return {
            "status_code": 403,
            "success": False,
            "data": None,
            "error": msg
        }

    url = f"{ERPNEXT_BASE_URL}/api/resource/{doctype_name}/{id}"
    headers = get_auth_headers()

    logger.info(f"REST Call: DELETE {url}")

    try:
        with httpx.Client() as client:
            response = client.delete(url, headers=headers, timeout=60.0)
            logger.info(f"Response Status: {response.status_code}")

            if response.is_success:
                try:
                    resp_json = response.json()
                    data = resp_json.get("data", resp_json)
                except Exception:
                    data = response.text or "ok"
                return {
                    "status_code": response.status_code,
                    "success": True,
                    "data": data,
                    "error": None
                }
            else:
                error_msg = extract_erpnext_error(response)
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return {
                    "status_code": response.status_code,
                    "success": False,
                    "data": data,
                    "error": error_msg
                }
    except Exception as e:
        logger.exception(f"Exception in delete_doctype for '{doctype_name}/{id}'")
        return {
            "status_code": 500,
            "success": False,
            "data": None,
            "error": str(e)
        }
