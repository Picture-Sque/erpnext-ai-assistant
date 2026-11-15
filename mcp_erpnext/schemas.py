"""
Pydantic schemas and input/output contracts for MCP ERPNext tools.

This module defines strict, validated input models for all ERPNext MCP operations.
It enforces strict type validation, rejects unknown fields (extra='forbid'), and prevents
user-supplied identity injection.
"""

from typing import Any, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel, ConfigDict, Field


class BaseToolModel(BaseModel):
    """Base model for all tool input parameters enforcing strict schema validation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# Read Tool Input Models
# ---------------------------------------------------------------------------

class GetListInput(BaseToolModel):
    """Input parameters for the get_list tool."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType to query.")
    filters: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Field-value filter dictionary or list of filter expressions."
    )
    fields: Optional[List[str]] = Field(default=None, description="List of field names to retrieve.")
    limit: Optional[int] = Field(default=None, ge=1, description="Maximum number of records to return.")
    order_by: Optional[str] = Field(default=None, min_length=1, description="Sort order expression.")
    offset: Optional[int] = Field(default=0, ge=0, description="Number of records to skip.")
    include_total: bool = Field(default=False, description="Whether to calculate and return total record count.")
    group_by: Optional[str] = Field(default=None, min_length=1, description="Field name to group results by.")
    parent_doctype: Optional[str] = Field(default=None, min_length=1, description="Parent DocType for child tables.")
    or_filters: Optional[Union[List[Any], Dict[str, Any]]] = Field(
        default=None, description="Disjunctive filters (OR)."
    )


class GetDocumentInput(BaseToolModel):
    """Input parameters for the get_document tool."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    name: str = Field(..., min_length=1, description="Unique document name/ID.")


class SearchDocumentInput(BaseToolModel):
    """Input parameters for the search_document tool."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType to search within.")
    query: str = Field(..., min_length=1, description="Search query string.")
    limit: int = Field(default=10, ge=1, description="Maximum number of search matches to return.")


class GetCountInput(BaseToolModel):
    """Input parameters for the get_count tool."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType to count records in.")
    filters: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Optional filter dictionary or list of expressions."
    )


class AggregateInput(BaseToolModel):
    """Input parameters for the aggregate tool."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    group_by: str = Field(..., min_length=1, description="Field name to group by.")
    metric: str = Field(..., min_length=1, description="Field name to aggregate.")
    aggregation_fn: Literal["sum", "avg", "count", "max", "min"] = Field(
        ..., description="Aggregation function: sum, avg, count, max, or min."
    )
    filters: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Optional filter dictionary or list of expressions."
    )
    sort: Optional[str] = Field(default=None, min_length=1, description="Sort specification (metric or group).")
    limit: Optional[int] = Field(default=None, ge=0, description="Maximum number of aggregate groups to return.")
    parent_doctype: Optional[str] = Field(default=None, min_length=1, description="Parent DocType if aggregating child table.")


class RunReportInput(BaseToolModel):
    """
    Input parameters for the run_report tool.

    Note: user_roles and client are strictly injected from trusted authentication context,
    never accepted as user input.
    """

    report_name: str = Field(..., min_length=1, description="Name of the allowlisted report to execute.")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Report filter parameters.")
    row_filters: Optional[List[Any]] = Field(default=None, description="Client-side row filters.")
    group_by: Optional[str] = Field(default=None, min_length=1, description="Optional group by field.")
    metric: Optional[str] = Field(default=None, min_length=1, description="Optional metric field.")
    aggregation_fn: Optional[Literal["sum", "avg", "count", "max", "min"]] = Field(
        default=None, description="Optional aggregation function."
    )
    sort: Optional[str] = Field(default=None, min_length=1, description="Optional sort specification.")
    limit: Optional[int] = Field(default=None, ge=0, description="Maximum number of rows to return.")


# ---------------------------------------------------------------------------
# Write Tool Input Models (Validated contracts; blocked in read-only mode)
# ---------------------------------------------------------------------------

class CreateDocumentInput(BaseToolModel):
    """Input parameters for create_document."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType to create.")
    fields: Dict[str, Any] = Field(..., description="Document field values.")


class UpdateDocumentInput(BaseToolModel):
    """Input parameters for update_document."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    name: str = Field(..., min_length=1, description="Document ID/name to update.")
    fields: Dict[str, Any] = Field(..., description="Fields to update.")


class DeleteDocumentInput(BaseToolModel):
    """Input parameters for delete_document."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    name: str = Field(..., min_length=1, description="Document ID/name to delete.")


class CancelDocumentInput(BaseToolModel):
    """Input parameters for cancel_document."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    name: str = Field(..., min_length=1, description="Document ID/name to cancel.")


class SubmitDocumentInput(BaseToolModel):
    """Input parameters for submit_document."""

    doctype: str = Field(..., min_length=1, description="The ERPNext DocType.")
    name: str = Field(..., min_length=1, description="Document ID/name to submit.")


# ---------------------------------------------------------------------------
# Tool Schema Registry and Registry Helpers
# ---------------------------------------------------------------------------

READ_TOOL_SCHEMAS: Dict[str, Type[BaseToolModel]] = {
    "get_list": GetListInput,
    "get_document": GetDocumentInput,
    "search_document": SearchDocumentInput,
    "get_count": GetCountInput,
    "aggregate": AggregateInput,
    "run_report": RunReportInput,
}

WRITE_TOOL_SCHEMAS: Dict[str, Type[BaseToolModel]] = {
    "create_document": CreateDocumentInput,
    "update_document": UpdateDocumentInput,
    "delete_document": DeleteDocumentInput,
    "cancel_document": CancelDocumentInput,
    "submit_document": SubmitDocumentInput,
}

TOOL_INPUT_SCHEMAS: Dict[str, Type[BaseToolModel]] = {
    **READ_TOOL_SCHEMAS,
    **WRITE_TOOL_SCHEMAS,
}


def get_tool_schema(tool_name: str) -> Optional[Type[BaseToolModel]]:
    """Return the Pydantic schema class for the given tool name, or None."""
    return TOOL_INPUT_SCHEMAS.get(tool_name)


def validate_tool_arguments(tool_name: str, arguments: Dict[str, Any]) -> BaseToolModel:
    """
    Validate tool arguments against the registered schema.

    Raises:
        ValueError: If tool_name is unknown.
        pydantic.ValidationError: If arguments fail schema validation.
    """
    schema_cls = get_tool_schema(tool_name)
    if not schema_cls:
        raise ValueError(f"No schema registered for tool '{tool_name}'.")
    return schema_cls.model_validate(arguments)


# ---------------------------------------------------------------------------
# Output Envelope Models
# ---------------------------------------------------------------------------

class StandardEnvelope(BaseModel):
    """Standard 5-key response envelope for all ERPNext operations."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["success", "empty", "permission_denied", "not_found", "system_error"]
    data: Optional[Any] = None
    error: Optional[str] = None
    has_more: bool = False
    success: bool


class PaginatedEnvelope(StandardEnvelope):
    """Extended response envelope containing pagination metadata for read operations."""

    total_count: Optional[int] = None
    offset: Optional[int] = None
    limit: Optional[int] = None
    truncated: Optional[bool] = None
