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
        get_allowed_doctypes,
    )
    from agent.tools.adapter import (
        ToolAdapter,
        adapter,
        get_tool_adapter,
        get_document_direct,
    )
    from agent.tools.mcp_client import (
        ERPNextMCPClient,
        MCPClient,
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
        get_allowed_doctypes,
    )
    from tools.adapter import (
        ToolAdapter,
        adapter,
        get_tool_adapter,
        get_document_direct,
    )
    from tools.mcp_client import (
        ERPNextMCPClient,
        MCPClient,
    )

__all__ = [
    "create_document",
    "get_list",
    "get_document",
    "get_document_direct",
    "search_document",
    "get_count",
    "aggregate",
    "update_document",
    "delete_document",
    "cancel_document",
    "submit_document",
    "is_doctype_allowed",
    "get_allowed_doctypes",
    "ToolAdapter",
    "adapter",
    "get_tool_adapter",
    "ERPNextMCPClient",
    "MCPClient",
]
