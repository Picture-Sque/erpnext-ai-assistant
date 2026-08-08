import os
import json
import logging
import httpx
from typing import Optional, Dict, Any
from openai import OpenAI

logger = logging.getLogger("agent_llm")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODELS = [
    os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
    "llama-3.3-70b-versatile"
]

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
        http_client = httpx.Client(timeout=4.0)
        return OpenAI(base_url=GROQ_BASE_URL, api_key=api_key, max_retries=0, http_client=http_client)
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        return None

def invoke_structured_llm(
    prompt: str,
    system_prompt: str = "You are a precise JSON extraction assistant for ERPNext. You must respond with valid JSON matching the requested schema."
) -> Optional[Dict[str, Any]]:
    """
    Unified LLM invocation function for Groq using OpenAI-compatible API with JSON mode.
    Falls back gracefully across models or to heuristic mode on error.
    """
    client = get_groq_client()
    if not client:
        return None

    for model_name in GROQ_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
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
                logger.info(f"Groq LLM ({model_name}) Response: {data}")
                return data
        except Exception as e:
            logger.warning(f"Groq invocation failed on {model_name}: {e}. Trying next model/fallback.")
            continue
            
    logger.warning("All Groq models failed or rate limited. Falling back to heuristic mode.")
    return None

def invoke_llm(
    prompt: str,
    system_prompt: str = "You are a helpful ERPNext AI Assistant. Provide concise, clear, and professional responses."
) -> str:
    """
    Standard LLM text invocation function for natural language generation.
    Falls back gracefully across models or returns empty string.
    """
    client = get_groq_client()
    if not client:
        return ""

    for model_name in GROQ_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
        except Exception as e:
            logger.warning(f"Groq text invocation failed on {model_name}: {e}. Trying next model/fallback.")
            continue
            
    return ""
