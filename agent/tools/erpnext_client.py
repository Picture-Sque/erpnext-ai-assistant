import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("erpnext_client")
logger.setLevel(logging.INFO)
# Ensure handlers don't duplicate
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

ERPNEXT_BASE_URL = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8081").rstrip("/")
ERPNEXT_API_KEY = os.getenv("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.getenv("ERPNEXT_API_SECRET", "")

class UnauthorizedError(Exception):
    pass

class ItemNotFoundError(Exception):
    pass

class CustomerNotFoundError(Exception):
    pass

def get_auth_headers() -> dict:
    """
    Constructs authorization headers for ERPNext REST API.
    Uses header format: Authorization: token API_KEY:API_SECRET
    """
    return {
        "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def get_customer(customer_name: str) -> dict:
    """
    Retrieves details of a customer from ERPNext via GET /api/resource/Customer/<customer_name>
    """
    url = f"{ERPNEXT_BASE_URL}/api/resource/Customer/{customer_name}"
    headers = get_auth_headers()
    
    # Log the exact request details for visibility/verification
    logger.info(f"REST Call: GET {url}")
    logger.info(f"Headers: { {k: v if k != 'Authorization' else '[REDACTED]' for k, v in headers.items()} }")
    
    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, timeout=60.0)
            
            logger.info(f"Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "data": data.get("data", {}),
                    "error": None
                }
            else:
                if response.status_code == 401:
                    raise UnauthorizedError()
                if response.status_code == 404 or "not found" in response.text.lower() or "doesnotexisterror" in response.text.lower():
                    raise CustomerNotFoundError()
                return {
                    "success": False,
                    "data": None,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
    except (UnauthorizedError, CustomerNotFoundError, ItemNotFoundError):
        raise
    except Exception as e:
        logger.exception("Exception in get_customer")
        return {
            "success": False,
            "data": None,
            "error": str(e)
        }

def check_stock(item_code: str) -> dict:
    """
    Checks stock levels for an item across warehouses via GET /api/resource/Bin
    Filters: [["item_code", "=", item_code]]
    Fields: ["warehouse", "actual_qty", "ordered_qty", "reserved_qty"]
    """
    url = f"{ERPNEXT_BASE_URL}/api/resource/Bin"
    headers = get_auth_headers()
    params = {
        "filters": f'[["item_code", "=", "{item_code}"]]',
        "fields": '["warehouse", "actual_qty", "ordered_qty", "reserved_qty"]'
    }
    
    # Log the exact request details
    logger.info(f"REST Call: GET {url}")
    logger.info(f"Params: {params}")
    logger.info(f"Headers: { {k: v if k != 'Authorization' else '[REDACTED]' for k, v in headers.items()} }")
    
    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=params, timeout=60.0)
            
            logger.info(f"Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                bins = data.get("data", [])
                if not bins:
                    # Check if the Item actually exists in ERPNext
                    item_url = f"{ERPNEXT_BASE_URL}/api/resource/Item/{item_code}"
                    item_resp = client.get(item_url, headers=headers, timeout=10.0)
                    if item_resp.status_code == 404 or "not found" in item_resp.text.lower() or "doesnotexisterror" in item_resp.text.lower():
                        raise ItemNotFoundError()
                return {
                    "success": True,
                    "data": bins,
                    "error": None
                }
            else:
                if response.status_code == 401:
                    raise UnauthorizedError()
                if response.status_code == 404 or "not found" in response.text.lower() or "doesnotexisterror" in response.text.lower():
                    raise ItemNotFoundError()
                return {
                    "success": False,
                    "data": None,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
    except (UnauthorizedError, CustomerNotFoundError, ItemNotFoundError):
        raise
    except Exception as e:
        logger.exception("Exception in check_stock")
        return {
            "success": False,
            "data": None,
            "error": str(e)
        }

def create_sales_order(customer: str, items: list[dict], delivery_date: str = None) -> dict:
    """
    Creates a Sales Order in ERPNext via POST /api/resource/Sales Order.
    Body format:
    {
        "customer": customer,
        "delivery_date": delivery_date,
        "items": [
            {
                "item_code": item_code,
                "qty": qty,
                "delivery_date": delivery_date
            },
            ...
        ]
    }
    """
    import datetime
    url = f"{ERPNEXT_BASE_URL}/api/resource/Sales Order"
    headers = get_auth_headers()
    
    # Calculate default delivery_date (7 days from today) if not provided
    if not delivery_date:
        delivery_date = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        
    # Standardize child items list to ensure they all have delivery_date
    processed_items = []
    for item in items:
        processed_item = dict(item)
        if "delivery_date" not in processed_item:
            processed_item["delivery_date"] = delivery_date
        processed_items.append(processed_item)
        
    # Structure the document payload
    payload = {
        "customer": customer,
        "delivery_date": delivery_date,
        "items": processed_items
    }
    
    # Log the exact request details
    logger.info(f"REST Call: POST {url}")
    logger.info(f"Headers: { {k: v if k != 'Authorization' else '[REDACTED]' for k, v in headers.items()} }")
    logger.info(f"Payload Body: {payload}")
    
    try:
        with httpx.Client() as client:
            response = client.post(url, headers=headers, json=payload, timeout=60.0)
            
            logger.info(f"Response Status: {response.status_code}")
            
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    "success": True,
                    "data": data.get("data", {}),
                    "error": None
                }
            else:
                if response.status_code == 401:
                    raise UnauthorizedError()
                err_text = response.text.lower()
                if "customer" in err_text and ("not found" in err_text or "does not exist" in err_text or "doesnotexisterror" in err_text):
                    raise CustomerNotFoundError()
                if ("item" in err_text or "item_code" in err_text) and ("not found" in err_text or "does not exist" in err_text or "doesnotexisterror" in err_text):
                    raise ItemNotFoundError()
                return {
                    "success": False,
                    "data": None,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }
    except (UnauthorizedError, CustomerNotFoundError, ItemNotFoundError):
        raise
    except Exception as e:
        logger.exception("Exception in create_sales_order")
        return {
            "success": False,
            "data": None,
            "error": str(e)
        }
