import logging
from typing import Any, Dict, List, Optional
# Keep import httpx: existing tests patch agent.tools.generic_tools.httpx.Client
import httpx
from dotenv import load_dotenv

from mcp_erpnext import crud, defaults
from mcp_erpnext.crud import (
    DEFAULT_WHITELIST,
    ORDER_BY_PATTERN,
    _format_result,
    get_allowed_doctypes,
    is_doctype_allowed,
)
from mcp_erpnext.errors import extract_erpnext_error

load_dotenv()

logger = logging.getLogger("generic_tools")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

try:
    from agent.tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL
except ImportError:
    from tools.erpnext_client import get_auth_headers, ERPNEXT_BASE_URL


def get_default_company(reload: bool = False) -> str:
    return defaults.get_default_company(reload=reload, logger=logger)


def get_default_warehouse(company: Optional[str] = None, reload: bool = False) -> str:
    return defaults.get_default_warehouse(company=company, reload=reload, logger=logger)


def get_list(
    doctype: str,
    filters: Optional[dict] = None,
    fields: Optional[list] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    offset: Optional[int] = 0,
    include_total: bool = False,
) -> dict:
    return crud.get_list(
        doctype=doctype,
        filters=filters,
        fields=fields,
        limit=limit,
        order_by=order_by,
        offset=offset,
        include_total=include_total,
    )


def create_document(doctype: str, fields: dict) -> dict:
    return crud.create_document(
        doctype=doctype,
        fields=fields,
        default_company_resolver=get_default_company,
        default_warehouse_resolver=get_default_warehouse,
        logger=logger,
    )


def update_document(doctype: str, name: str, fields: dict) -> dict:
    return crud.update_document(
        doctype=doctype,
        name=name,
        fields=fields,
        document_getter=get_document,
        logger=logger,
    )


def delete_document(doctype: str, name: str) -> dict:
    return crud.delete_document(
        doctype=doctype,
        name=name,
    )


def get_document(doctype: str, name: str) -> dict:
    return crud.get_document(
        doctype=doctype,
        name=name,
    )


def search_document(doctype: str, query: str, limit: int = 10) -> dict:
    return crud.search_document(
        doctype=doctype,
        query=query,
        limit=limit,
    )


def get_count(doctype: str, filters: Optional[dict] = None) -> dict:
    return crud.get_count(
        doctype=doctype,
        filters=filters,
    )


def aggregate(
    doctype: str,
    group_by: str,
    metric: str,
    aggregation_fn: str,
    filters: Optional[dict] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    parent_doctype: Optional[str] = None,
) -> dict:
    return crud.aggregate(
        doctype=doctype,
        group_by=group_by,
        metric=metric,
        aggregation_fn=aggregation_fn,
        filters=filters,
        sort=sort,
        limit=limit,
        parent_doctype=parent_doctype,
    )


def cancel_document(doctype: str, name: str) -> dict:
    return crud.cancel_document(
        doctype=doctype,
        name=name,
    )


def submit_document(doctype: str, name: str) -> dict:
    return crud.submit_document(
        doctype=doctype,
        name=name,
    )
