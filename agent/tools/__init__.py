# Tools package initialization
try:
    from agent.tools.generic_tools import (
        add_doctype,
        list_doctype,
        update_doctype,
        delete_doctype,
        is_doctype_allowed,
        get_allowed_doctypes
    )
except ImportError:
    from tools.generic_tools import (
        add_doctype,
        list_doctype,
        update_doctype,
        delete_doctype,
        is_doctype_allowed,
        get_allowed_doctypes
    )

__all__ = [
    "add_doctype",
    "list_doctype",
    "update_doctype",
    "delete_doctype",
    "is_doctype_allowed",
    "get_allowed_doctypes"
]
