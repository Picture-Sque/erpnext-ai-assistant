import os
import logging
import httpx
from typing import Optional, Dict, Any
from mcp_erpnext.errors import ERPNextConfigError, ERPNextTransportError

logger = logging.getLogger("mcp_erpnext")


class Credentials:
    """
    Carries ERPNext connection details and credentials.
    Per-request credentials can be explicitly provided.
    Repr is fully redacted to prevent credential leakage in logs or exceptions.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._custom_headers = auth_headers

    def get_headers(self) -> Dict[str, str]:
        if self._custom_headers is not None:
            headers = dict(self._custom_headers)
        elif self.api_key and self.api_secret:
            headers = {
                "Authorization": f"token {self.api_key}:{self.api_secret}",
            }
        else:
            raise ERPNextConfigError("Missing ERPNext API key or secret.")

        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        return headers

    def __repr__(self) -> str:
        key_repr = "'[REDACTED]'" if self.api_key else "None"
        sec_repr = "'[REDACTED]'" if self.api_secret else "None"
        headers_repr = "[CUSTOM_HEADERS]" if self._custom_headers else "None"
        return (
            f"Credentials(base_url='{self.base_url}', api_key={key_repr}, "
            f"api_secret={sec_repr}, custom_headers={headers_repr})"
        )


def get_env_credentials() -> Credentials:
    """
    Lazily read ERPNext credentials from environment at call time.
    Fails visibly if keys are missing.
    """
    base_url = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8081").rstrip("/")
    api_key = os.getenv("ERPNEXT_API_KEY", "").strip()
    api_secret = os.getenv("ERPNEXT_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise ERPNextConfigError(
            "ERPNext credentials not configured. Set ERPNEXT_API_KEY and ERPNEXT_API_SECRET or provide Credentials."
        )

    return Credentials(base_url=base_url, api_key=api_key, api_secret=api_secret)


class ERPNextClient:
    """
    Transport-only client for ERPNext REST API.
    Directly invokes httpx.Client methods (get, post, put, delete) matching existing call signatures,
    with zero retries and typed transport error wrapping.
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self._credentials = credentials

    def get_credentials(self) -> Credentials:
        if self._credentials is not None:
            return self._credentials
        return get_env_credentials()

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        creds = self.get_credentials()
        url = f"{creds.base_url}/{path.lstrip('/')}"
        req_headers = headers if headers is not None else creds.get_headers()
        try:
            with httpx.Client() as client:
                if params is not None:
                    return client.get(url, headers=req_headers, params=params, timeout=timeout)
                return client.get(url, headers=req_headers, timeout=timeout)
        except httpx.HTTPError as e:
            logger.warning(f"HTTP transport error during GET {url}: {e}")
            raise ERPNextTransportError(str(e)) from e

    def post(
        self,
        path: str,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        creds = self.get_credentials()
        url = f"{creds.base_url}/{path.lstrip('/')}"
        req_headers = headers if headers is not None else creds.get_headers()
        try:
            with httpx.Client() as client:
                return client.post(url, headers=req_headers, json=json, timeout=timeout)
        except httpx.HTTPError as e:
            logger.warning(f"HTTP transport error during POST {url}: {e}")
            raise ERPNextTransportError(str(e)) from e

    def put(
        self,
        path: str,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        creds = self.get_credentials()
        url = f"{creds.base_url}/{path.lstrip('/')}"
        req_headers = headers if headers is not None else creds.get_headers()
        try:
            with httpx.Client() as client:
                return client.put(url, headers=req_headers, json=json, timeout=timeout)
        except httpx.HTTPError as e:
            logger.warning(f"HTTP transport error during PUT {url}: {e}")
            raise ERPNextTransportError(str(e)) from e

    def delete(
        self,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
    ) -> httpx.Response:
        creds = self.get_credentials()
        url = f"{creds.base_url}/{path.lstrip('/')}"
        req_headers = headers if headers is not None else creds.get_headers()
        try:
            with httpx.Client() as client:
                return client.delete(url, headers=req_headers, timeout=timeout)
        except httpx.HTTPError as e:
            logger.warning(f"HTTP transport error during DELETE {url}: {e}")
            raise ERPNextTransportError(str(e)) from e
