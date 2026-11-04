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

try:
    from groq import Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False

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
# Groq client (cross-provider fallback)
# ---------------------------------------------------------------------------
_groq_client: Optional["Groq"] = None


def _get_groq_client() -> Optional["Groq"]:
    """Return the Groq client, or None if not configured."""
    global _groq_client
    if not _GROQ_AVAILABLE:
        return None
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key:
        return None
    if _groq_client is None:
        _groq_client = Groq(api_key=groq_key)
        logger.debug("Groq client initialised.")
    return _groq_client


# ---------------------------------------------------------------------------
# Local embedding function (fallback when Gemini is not configured)
# ---------------------------------------------------------------------------
_local_embed_fn = None


def _get_local_embed_fn():
    """Return ChromaDB's default embedding function (all-MiniLM-L6-v2 via ONNX).

    No API key required.  Downloads the model (~80 MB) on first use and
    caches it at ``~/.cache/chroma/onnx_models/``.
    """
    global _local_embed_fn
    if _local_embed_fn is None:
        from chromadb.utils import embedding_functions
        _local_embed_fn = embedding_functions.DefaultEmbeddingFunction()
        logger.info(
            "Local embedding function initialised (all-MiniLM-L6-v2, 384 dims)."
        )
    return _local_embed_fn


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
    """Send *prompt* to an LLM and return the text response.

    If ``GEMINI_API_KEY`` is set, Gemini is the primary provider.  Transient
    server errors (5xx, 429, UNAVAILABLE, RESOURCE_EXHAUSTED) are retried with
    exponential backoff.  If the primary model exhausts all retries, the
    function falls back to the next model in the chain defined by the
    ``GEMINI_FALLBACK_MODELS`` environment variable (comma-separated).  If all
    Gemini models fail, Groq is tried as a cross-provider fallback.

    If ``GEMINI_API_KEY`` is not set but ``GROQ_API_KEY`` is, Groq is used as
    the sole provider.

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

    prompt_hash = _hash(prompt)
    t0 = time.monotonic()

    # If Gemini is not configured, use Groq as the primary provider.
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        logger.info(
            "LLM call start (Groq primary — Gemini not configured)",
            extra={
                "event": "llm_call_start",
                "model": "groq-primary",
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt),
            },
        )
        if _get_groq_client() is None:
            raise EnvironmentError(
                "No LLM provider configured. Set GEMINI_API_KEY or GROQ_API_KEY "
                "in your .env file."
            )
        groq_text = _generate_groq(
            prompt, system_prompt, temperature, t0, prompt_hash, is_primary=True
        )
        if groq_text is not None:
            return groq_text
        raise RuntimeError(
            "Groq call failed. Check that GROQ_MODEL is a valid model ID "
            "(see https://console.groq.com/docs/models) and that your "
            "GROQ_API_KEY is valid."
        )

    client = _get_client()

    # Build the contents list — system prompt is passed via config, not contents
    contents: list = [prompt]

    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_prompt,  # None is safely ignored by the SDK
    )

    logger.info(
        "LLM call start",
        extra={
            "event": "llm_call_start",
            "model": primary_model,
            "prompt_hash": prompt_hash,
            "prompt_length": len(prompt),
        },
    )

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

                # Non-retryable: auth errors surface immediately.
                # Use the exception's HTTP status code for reliable detection —
                # substring matching on "401"/"403" causes false positives (e.g.
                # the retry-delay "140130646s" in a 429 message contains "401").
                exc_code = getattr(exc, "code", None)
                if exc_code in (401, 403) or "API_KEY_INVALID" in exc_str:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    raise EnvironmentError(
                        f"Gemini authentication failed — check your GEMINI_API_KEY. "
                        f"Original error: {exc}"
                    ) from exc

                is_transient = exc_code in (429, 500, 502, 503) or _is_transient_error(exc_str)
                is_model_not_found = (
                    exc_code == 404 or "404" in exc_str or "NOT_FOUND" in exc_str
                    or "no longer available" in exc_str
                )
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

                # Retries exhausted (transient) or model deprecated (404) —
                # fall back to next model in chain, or break to reach the
                # cross-provider (Groq) safety net below.
                should_fallback = is_transient or is_model_not_found
                if should_fallback:
                    if not is_last_model:
                        logger.warning(
                            "Model %s unavailable (%s), falling back to %s",
                            current_model,
                            "retries exhausted" if is_transient else "model not found",
                            model_chain[chain_idx + 1],
                            extra={
                                "event": "llm_call_fallback",
                                "model": model_chain[chain_idx + 1],
                                "previous_model": current_model,
                                "prompt_hash": prompt_hash,
                            },
                        )
                    break  # exit inner loop -> try next model, or reach Groq

                # Non-transient, non-auth error — fail immediately
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

    # Safety net: all Gemini models exhausted — try Groq as cross-provider fallback
    if response is None:
        groq_text = _generate_groq(prompt, system_prompt, temperature, t0, prompt_hash)
        if groq_text is not None:
            return groq_text

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "LLM call failed — all providers exhausted",
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
            f"LLM call failed after trying {len(model_chain)} Gemini model(s) "
            f"and Groq fallback: {last_exc}"
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


def _generate_groq(
    prompt: str,
    system_prompt: Optional[str],
    temperature: float,
    t0: float,
    prompt_hash: str,
    is_primary: bool = False,
) -> Optional[str]:
    """Generate a response using Groq.

    When *is_primary* is True, Groq is the sole provider (Gemini not
    configured).  Otherwise Groq is used as a cross-provider fallback after
    all Gemini models have been exhausted.

    Returns the response text, or None if Groq is not configured or fails.
    """
    groq_client = _get_groq_client()
    if groq_client is None:
        return None

    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    messages: list = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    if is_primary:
        logger.info(
            "LLM call via Groq (%s) — primary provider",
            groq_model,
            extra={
                "event": "llm_call_start",
                "model": groq_model,
                "prompt_hash": prompt_hash,
                "prompt_length": len(prompt),
            },
        )
    else:
        logger.info(
            "Falling back to Groq (%s) after all Gemini models failed",
            groq_model,
            extra={
                "event": "llm_call_fallback",
                "model": groq_model,
                "previous_model": "gemini (all exhausted)",
                "prompt_hash": prompt_hash,
            },
        )

    try:
        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=messages,
            temperature=temperature,
        )
        text = response.choices[0].message.content
        if not text or not text.strip():
            logger.warning("Groq returned an empty response.")
            return None

        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None

        logger.info(
            "LLM call complete (Groq)",
            extra={
                "event": "llm_call_complete",
                "model": groq_model,
                "prompt_hash": prompt_hash,
                "response_hash": _hash(text),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
            },
        )
        return text
    except Exception as exc:
        logger.warning(
            "Groq fallback failed: %s",
            exc,
            extra={
                "event": "llm_call_error",
                "model": groq_model,
                "prompt_hash": prompt_hash,
                "error": str(exc),
            },
        )
        return None


def embed(text: str, model: Optional[str] = None) -> list[float]:
    """Return an embedding vector for *text*.

    Uses Gemini's embedding API when ``GEMINI_API_KEY`` is set.  Otherwise
    falls back to a local model (all-MiniLM-L6-v2 via ChromaDB's default
    embedding function) that requires no API key.

    .. note::

       Gemini and local embeddings have different dimensions (768 vs 384).
       If you switch providers, delete the ChromaDB persist directory and
       re-run ``build_index()`` to re-index with the new embedding model.

    Args:
        text:  The text to embed.
        model: Gemini embedding model ID (ignored when using local fallback).
               Defaults to the GEMINI_EMBEDDING_MODEL env var
               (fallback: ``gemini-embedding-001``).

    Returns:
        A list of floats representing the embedding vector.

    Raises:
        RuntimeError: Any API-level error.
    """
    # --- Local embedding fallback (no Gemini key needed) ---
    if not os.getenv("GEMINI_API_KEY", "").strip():
        ef = _get_local_embed_fn()
        t0 = time.monotonic()
        try:
            vector = [float(x) for x in ef([text])[0]]
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.error(
                "Local embedding failed",
                extra={
                    "event": "embed_call_error",
                    "model": "all-MiniLM-L6-v2",
                    "latency_ms": latency_ms,
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"Local embedding failed: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.debug(
            "Embedding complete (local)",
            extra={
                "event": "embed_call_complete",
                "model": "all-MiniLM-L6-v2",
                "vector_dim": len(vector),
                "latency_ms": latency_ms,
            },
        )
        return vector

    # --- Gemini embedding ---
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
