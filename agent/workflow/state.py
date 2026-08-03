from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """
    Defines the state structure for our LangGraph conversation flow.
    """
    # messages: Accumulates conversation history (messages list).
    # Uses add_messages reducer so append operations merge correctly.
    messages: Annotated[list, add_messages]
    
    # detected_intent: Tracks what intent the LLM has classified for the user's input.
    # Used for routing decisions (e.g. "create_sales_order", "fallback").
    detected_intent: str
    
    # collected_fields: A dictionary representing slots/entity fields extracted so far.
    # For a sales order, this stores keys like "customer" and "items".
    collected_fields: dict
    
    # final_response: The compiled response message that will be sent back to the client.
    final_response: str
    
    # user_roles: Stores the list of roles for the authenticated user from the JWT payload, used for RBAC checks.
    user_roles: list[str]
