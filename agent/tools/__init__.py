# Tools package initialization
try:
    from agent.tools.generic_tools import (
        create_document,
        get_list,
        get_document,
        search_document,
        get_count,
        aggregate,
        update_document,
        delete_document,
        cancel_document,
        submit_document,
        is_doctype_allowed,
        get_allowed_doctypes
    )
except ImportError:
    from tools.generic_tools import (
        create_document,
        get_list,
        get_document,
        search_document,
        get_count,
        aggregate,
        update_document,
        delete_document,
        cancel_document,
        submit_document,
        is_doctype_allowed,
        get_allowed_doctypes
    )

__all__ = [
    "create_document",
    "get_list",
    "get_document",
    "search_document",
    "get_count",
    "aggregate",
    "update_document",
    "delete_document",
    "cancel_document",
    "submit_document",
    "is_doctype_allowed",
    "get_allowed_doctypes",
]
