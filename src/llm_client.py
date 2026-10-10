"""
llm_client.py — Gemini API wrapper for the QA Agent.

Responsibilities:
  - Load configuration from environment variables.
  - Expose a single `generate(prompt, system_prompt)` function.
  - Log every API call: model, prompt hash, token usage, latency.
  - Raise descriptive errors for auth failures and empty responses.

SDK: google-genai 2.x  (from google import genai)
"""

import hashlib
import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


def _hash(text: str) -> str:
    """Return the first 8 characters of the SHA-256 hash of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Module-level client (initialised once, reused across calls)
# ---------------------------------------------------------------------------
_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    """Return (or lazily create) the module-level Gemini client.

    Raises:
        EnvironmentError: if GEMINI_API_KEY is not set.
        google.genai.errors.ClientError: if the key is invalid (raised on
            first actual API call, not here).
    """
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is not set. "
                "Copy .env.example to .env and add your Google AI Studio key."
            )
        _client = genai.Client(api_key=api_key)
        logger.debug("Gemini client initialised.")
    return _client


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
) -> str:
    """Send *prompt* to Gemini and return the text response.

    Args:
        prompt:        The user-facing prompt text.
        system_prompt: Optional system / role instruction prepended to the
                       conversation.
        model:         Gemini model ID.  Defaults to the GEMINI_MODEL env var
                       (fallback: ``gemini-3.8-flash``).
        temperature:   Sampling temperature (lower = more deterministic).

    Returns:
        The model's text response as a plain string.

    Raises:
        EnvironmentError:  GEMINI_API_KEY is missing.
        ValueError:        The model returned an empty response.
        RuntimeError:      Any other API-level error, wrapped with context.
    """
    model_id = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    client = _get_client()

    # Build the contents list — system prompt is passed via config, not contents
    contents: list = [prompt]

    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_prompt,  # None is safely ignored by the SDK
    )

    prompt_hash = _hash(prompt)
    logger.info(
        "LLM call start",
        extra={
            "event": "llm_call_start",
            "model": model_id,
            "prompt_hash": prompt_hash,
            "prompt_length": len(prompt),
        },
    )

    t0 = time.monotonic()
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        # Surface auth errors with a cleaner message
        exc_str = str(exc)
        if "API_KEY_INVALID" in exc_str or "401" in exc_str or "403" in exc_str:
            raise EnvironmentError(
                f"Gemini authentication failed — check your GEMINI_API_KEY. "
                f"Original error: {exc}"
            ) from exc
        logger.error(
            "LLM call failed",
            extra={
                "event": "llm_call_error",
                "model": model_id,
                "prompt_hash": prompt_hash,
                "latency_ms": latency_ms,
                "error": exc_str,
            },
        )
        raise RuntimeError(f"Gemini API call failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text — response.text raises if the response is blocked/empty
    try:
        text = response.text
    except Exception as exc:
        raise ValueError(
            f"Gemini returned an empty or blocked response. "
            f"Finish reason: {getattr(response, 'finish_reason', 'unknown')}. "
            f"Original error: {exc}"
        ) from exc

    if not text or not text.strip():
        raise ValueError(
            "Gemini returned an empty response body. "
            "Check your prompt or try again."
        )

    # Token usage (may be None for some models / response types)
    usage = getattr(response, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)

    logger.info(
        "LLM call complete",
        extra={
            "event": "llm_call_complete",
            "model": model_id,
            "prompt_hash": prompt_hash,
            "response_hash": _hash(text),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        },
    )

    return text


def embed(text: str, model: Optional[str] = None) -> list[float]:
    """Return an embedding vector for *text* using Gemini's embedding model.

    Args:
        text:  The text to embed.
        model: Embedding model ID.  Defaults to the GEMINI_EMBEDDING_MODEL env
               var (fallback: ``models/text-embedding-004``).

    Returns:
        A list of floats representing the embedding vector.

    Raises:
        EnvironmentError: GEMINI_API_KEY is missing.
        RuntimeError:     Any API-level error.
    """
    model_id = model or os.getenv(
        "GEMINI_EMBEDDING_MODEL", "models/text-embedding-004"
    )
    client = _get_client()

    t0 = time.monotonic()
    try:
        response = client.models.embed_content(
            model=model_id,
            contents=text,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Embedding call failed",
            extra={
                "event": "embed_call_error",
                "model": model_id,
                "latency_ms": latency_ms,
                "error": str(exc),
            },
        )
        raise RuntimeError(f"Gemini embed_content failed: {exc}") from exc

    latency_ms = int((time.monotonic() - t0) * 1000)
    vector = response.embeddings[0].values

    logger.debug(
        "Embedding complete",
        extra={
            "event": "embed_call_complete",
            "model": model_id,
            "vector_dim": len(vector),
            "latency_ms": latency_ms,
        },
    )

    return vector
