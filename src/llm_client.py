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

def _is_transient_error(exc_str: str) -> bool:
    """Return True if the exception string indicates a retryable server-side error."""
    transient_markers = ("503", "502", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                         "INTERNAL", "ServiceUnavailable", "InternalServerError",
                         "ResourceExhausted", "429", "RATE_LIMIT")
    return any(marker in exc_str for marker in transient_markers)


def generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_retries: Optional[int] = None,
) -> str:
    """Send *prompt* to Gemini and return the text response.

    Transient server errors (5xx, 429, UNAVAILABLE, RESOURCE_EXHAUSTED) are
    retried with exponential backoff.  If the primary model exhausts all
    retries, the function falls back to the next model in the chain defined
    by the ``GEMINI_FALLBACK_MODELS`` environment variable (comma-separated).
    Non-transient errors (auth, bad request) are surfaced immediately.

    Args:
        prompt:        The user-facing prompt text.
        system_prompt: Optional system / role instruction prepended to the
                       conversation.
        model:         Gemini model ID.  Defaults to the GEMINI_MODEL env var
                       (fallback: ``gemini-3.8-flash``).
        temperature:   Sampling temperature (lower = more deterministic).
        max_retries:   Maximum number of retry attempts per model for transient
                       errors.  Defaults to the LLM_MAX_RETRIES env var
                       (fallback: 3).

    Returns:
        The model's text response as a plain string.

    Raises:
        EnvironmentError:  GEMINI_API_KEY is missing.
        ValueError:        The model returned an empty response.
        RuntimeError:      Any other API-level error, wrapped with context.
    """
    primary_model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    _max_retries = max_retries if max_retries is not None else int(
        os.getenv("LLM_MAX_RETRIES", "3")
    )

    # Build the fallback model chain from the env var (comma-separated list)
    fallback_str = os.getenv("GEMINI_FALLBACK_MODELS", "")
    fallback_models = [m.strip() for m in fallback_str.split(",") if m.strip()]
    model_chain = [primary_model] + [m for m in fallback_models if m != primary_model]

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
            "model": primary_model,
            "prompt_hash": prompt_hash,
            "prompt_length": len(prompt),
        },
    )

    t0 = time.monotonic()
    last_exc: Optional[Exception] = None
    response = None

    for chain_idx, current_model in enumerate(model_chain):
        is_last_model = chain_idx == len(model_chain) - 1

        if chain_idx > 0:
            logger.info(
                "Falling back to %s after %s failed",
                current_model,
                model_chain[chain_idx - 1],
                extra={
                    "event": "llm_call_fallback",
                    "model": current_model,
                    "previous_model": model_chain[chain_idx - 1],
                    "prompt_hash": prompt_hash,
                },
            )

        for attempt in range(1, _max_retries + 2):  # attempts: 1 … _max_retries+1
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=contents,
                    config=config,
                )
                break  # success — exit retry loop
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc)

                # Non-retryable: auth errors surface immediately
                if "API_KEY_INVALID" in exc_str or "401" in exc_str or "403" in exc_str:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    raise EnvironmentError(
                        f"Gemini authentication failed — check your GEMINI_API_KEY. "
                        f"Original error: {exc}"
                    ) from exc

                is_transient = _is_transient_error(exc_str)
                retries_left = _max_retries - (attempt - 1)

                if is_transient and retries_left > 0:
                    delay = 2 ** (attempt - 1)  # 1 s, 2 s, 4 s, …
                    logger.warning(
                        "Transient Gemini error (attempt %d/%d) on %s, "
                        "retrying in %ds: %s",
                        attempt, _max_retries + 1, current_model, delay,
                        exc_str[:120],
                        extra={
                            "event": "llm_call_retry",
                            "model": current_model,
                            "prompt_hash": prompt_hash,
                            "attempt": attempt,
                            "delay_s": delay,
                            "error": exc_str,
                        },
                    )
                    time.sleep(delay)
                    continue  # retry same model

                # Retries exhausted or non-transient error
                if is_transient and not is_last_model:
                    logger.warning(
                        "Model %s exhausted %d retries, falling back to %s",
                        current_model, _max_retries + 1,
                        model_chain[chain_idx + 1],
                        extra={
                            "event": "llm_call_fallback",
                            "model": model_chain[chain_idx + 1],
                            "previous_model": current_model,
                            "prompt_hash": prompt_hash,
                        },
                    )
                    break  # exit inner loop -> try next model in chain

                # Last model or non-transient error — fail
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.error(
                    "LLM call failed",
                    extra={
                        "event": "llm_call_error",
                        "model": current_model,
                        "prompt_hash": prompt_hash,
                        "latency_ms": latency_ms,
                        "attempts": attempt,
                        "error": exc_str,
                    },
                )
                raise RuntimeError(f"Gemini API call failed: {exc}") from exc

        # Success — exit the model chain
        if response is not None:
            break

    # Safety net: all models exhausted without a response
    if response is None:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "LLM call failed — all models in chain exhausted",
            extra={
                "event": "llm_call_error",
                "model": primary_model,
                "prompt_hash": prompt_hash,
                "latency_ms": latency_ms,
                "models_tried": len(model_chain),
                "error": str(last_exc),
            },
        )
        raise RuntimeError(
            f"Gemini API call failed after trying {len(model_chain)} "
            f"model(s): {last_exc}"
        ) from last_exc

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
            "model": current_model,
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
               var (fallback: ``gemini-embedding-001``).

    Returns:
        A list of floats representing the embedding vector.

    Raises:
        EnvironmentError: GEMINI_API_KEY is missing.
        RuntimeError:     Any API-level error.
    """
    model_id = model or os.getenv(
        "GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
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
