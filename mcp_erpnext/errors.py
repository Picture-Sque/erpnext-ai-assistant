import json
import logging
import httpx
from typing import Optional

logger = logging.getLogger("mcp_erpnext")


class ERPNextError(RuntimeError):
    """Base error for all ERPNext operations, subclassing RuntimeError."""
    pass


class ERPNextTransportError(ERPNextError):
    """Transport / network errors (connection, timeout, DNS, etc.)."""
    pass


class ERPNextConfigError(ERPNextError):
    """Missing or invalid credentials, configuration, or environment."""
    pass


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
