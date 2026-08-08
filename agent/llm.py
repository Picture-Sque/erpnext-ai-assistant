import os
import json
import logging
from typing import Optional, Dict, Any
from openai import OpenAI

logger = logging.getLogger("agent_llm")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"

def get_groq_client() -> Optional[OpenAI]:
    """
    Initializes and returns an OpenAI client configured for Groq API endpoint.
    Returns None if GROQ_API_KEY is not set or invalid placeholder.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key in ("your_groq_api_key_here", "mock_groq_api_key"):
        logger.warning("GROQ_API_KEY is unconfigured or set to placeholder.")
        return None
    try:
        return OpenAI(base_url=GROQ_BASE_URL, api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        return None

def invoke_structured_llm(
    prompt: str,
    system_prompt: str = "You are a precise JSON extraction assistant for ERPNext. You must respond with valid JSON matching the requested schema."
) -> Optional[Dict[str, Any]]:
    """
    Unified LLM invocation function for Groq using OpenAI-compatible API with JSON mode.
    Returns parsed dictionary on success, or None on error/quota/unconfigured API key.
    Returning None triggers seamless heuristic fallback in LangGraph nodes.
    """
    client = get_groq_client()
    if not client:
        return None

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        content = response.choices[0].message.content
        if content:
            data = json.loads(content)
            logger.info(f"Groq LLM Response: {data}")
            return data
        return None
    except Exception as e:
        logger.warning(f"Groq LLM invocation failed: {e}. Falling back to heuristic mode.")
        return None

def invoke_llm(
    prompt: str,
    system_prompt: str = "You are a helpful ERPNext AI Assistant. Provide concise, clear, and professional responses."
) -> str:
    """
    Standard LLM text invocation function for natural language generation.
    Returns generated text on success, or empty string on failure.
    """
    client = get_groq_client()
    if not client:
        return ""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"Groq LLM text invocation failed: {e}")
        return ""
