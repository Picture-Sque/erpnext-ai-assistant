import os
import json
import logging
import threading
import secrets
import hmac
import hashlib
from typing import Optional, Dict, List
import httpx

from mcp_erpnext.client import ERPNextClient
from mcp_erpnext.errors import ERPNextError, ERPNextConfigError, extract_erpnext_error

logger = logging.getLogger("mcp_erpnext.defaults")

# Per-process secret key for HMAC hashing of credentials in cache keys
_HMAC_KEY = secrets.token_bytes(32)
_cache_lock = threading.Lock()

# Resolver caches: keyed by (base_url, hmac_hash)
_company_cache: Dict[tuple, str] = {}
_warehouse_cache: Dict[tuple, Dict[str, str]] = {}


def _get_cache_key(client: ERPNextClient) -> tuple:
    creds = client.get_credentials()
    base_url = creds.base_url
    if creds.api_key and creds.api_secret:
        cred_str = f"{creds.api_key}:{creds.api_secret}"
    elif creds._custom_headers:
        cred_str = str(sorted(creds._custom_headers.items()))
    else:
        cred_str = ""
    cred_hash = hmac.new(_HMAC_KEY, cred_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return (base_url, cred_hash)


def reset_resolver_caches() -> None:
    """Clear in-memory caches for default company and warehouse resolvers."""
    with _cache_lock:
        _company_cache.clear()
        _warehouse_cache.clear()


def get_default_company(
    client: Optional[ERPNextClient] = None,
    reload: bool = False,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Resolves default company in ERPNext with module-level caching.
    Uses ERPNextClient with timeout=10.0.
    """
    log = logger or logging.getLogger("mcp_erpnext.defaults")
    client = client or ERPNextClient()
    cache_key = _get_cache_key(client)

    with _cache_lock:
        if not reload and cache_key in _company_cache:
            return _company_cache[cache_key]

    try:
        resp = client.get(
            "/api/resource/Company",
            params={"limit_page_length": 100},
            timeout=10.0
        )
        if not resp.is_success:
            err_msg = extract_erpnext_error(resp) or resp.text
            raise RuntimeError(f"Failed to fetch Company list from ERPNext ({resp.status_code}): {err_msg}")
        data = resp.json().get("data", [])
    except (ERPNextError, httpx.HTTPError, ValueError) as e:
        log.warning(f"Network error or invalid JSON while fetching Company list from ERPNext: {e}")
        raise RuntimeError(f"Failed to fetch Company list from ERPNext: {e}") from e

    company_names = [c["name"] for c in data if isinstance(c, dict) and "name" in c]

    env_val = os.getenv("DEFAULT_COMPANY")
    if env_val:
        target = env_val.strip()
        if target in company_names:
            with _cache_lock:
                _company_cache[cache_key] = target
            return target
        log.warning(f"DEFAULT_COMPANY '{target}' does not exist in ERPNext companies: {company_names}")

    if len(company_names) == 1:
        res = company_names[0]
        with _cache_lock:
            _company_cache[cache_key] = res
        return res
    elif len(company_names) == 0:
        raise RuntimeError("No companies found in ERPNext.")
    else:
        raise RuntimeError("Default company is not configured.")


def get_default_warehouse(
    company: Optional[str] = None,
    client: Optional[ERPNextClient] = None,
    reload: bool = False,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Resolves default warehouse for a company in ERPNext with module-level caching.
    Uses ERPNextClient with timeouts (10.0s list, 5.0s doc/settings).
    """
    log = logger or logging.getLogger("mcp_erpnext.defaults")
    client = client or ERPNextClient()
    comp = company or get_default_company(client=client, reload=reload, logger=log)
    cache_key = _get_cache_key(client)

    with _cache_lock:
        if not reload and cache_key in _warehouse_cache and comp in _warehouse_cache[cache_key]:
            return _warehouse_cache[cache_key][comp]

    try:
        resp = client.get(
            "/api/resource/Warehouse",
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
    except (ERPNextError, httpx.HTTPError, ValueError) as e:
        log.warning(f"Network error or invalid JSON while fetching Warehouse list for '{comp}' from ERPNext: {e}")
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
                with _cache_lock:
                    _warehouse_cache.setdefault(cache_key, {})[comp] = target_wh
                return target_wh
        log.info(f"DEFAULT_WAREHOUSE '{target_wh}' does not belong to company '{comp}'; ignoring.")

    # 2. Prefer Company or Stock Settings default warehouse if available and belongs to company
    pref_wh = None
    try:
        c_resp = client.get(f"/api/resource/Company/{comp}", timeout=5.0)
        if c_resp.is_success:
            c_def = (c_resp.json().get("data") or {}).get("default_warehouse")
            if c_def and c_def in warehouse_names:
                pref_wh = c_def
        else:
            log.warning(f"Failed to fetch Company '{comp}' from ERPNext ({c_resp.status_code})")
    except (ERPNextError, httpx.HTTPError, ValueError) as e:
        log.warning(f"Failed to fetch default warehouse from Company '{comp}': {e}")

    if not pref_wh:
        try:
            s_resp = client.get("/api/resource/Stock Settings/Stock Settings", timeout=5.0)
            if s_resp.is_success:
                s_def = (s_resp.json().get("data") or {}).get("default_warehouse")
                if s_def and s_def in warehouse_names:
                    pref_wh = s_def
            else:
                log.warning(f"Failed to fetch Stock Settings from ERPNext ({s_resp.status_code})")
        except (ERPNextError, httpx.HTTPError, ValueError) as e:
            log.warning(f"Failed to fetch default warehouse from Stock Settings: {e}")

    if pref_wh:
        with _cache_lock:
            _warehouse_cache.setdefault(cache_key, {})[comp] = pref_wh
        return pref_wh

    # 3. Existing 'stores' heuristic
    for wh in warehouses:
        w_name = wh.get("name", "")
        w_label = wh.get("warehouse_name", "")
        if "stores" in w_name.lower() or "stores" in w_label.lower():
            with _cache_lock:
                _warehouse_cache.setdefault(cache_key, {})[comp] = w_name
            return w_name

    # 4. Fallback to first warehouse (prefer non-group)
    leaf = [wh["name"] for wh in warehouses if not wh.get("is_group") and "name" in wh]
    chosen = leaf[0] if leaf else warehouse_names[0]
    with _cache_lock:
        _warehouse_cache.setdefault(cache_key, {})[comp] = chosen
    return chosen
