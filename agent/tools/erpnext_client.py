import os
import logging
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
