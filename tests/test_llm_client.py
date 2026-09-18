"""
tests/test_llm_client.py — Unit tests for src/llm_client.py.

All tests mock the Gemini API so no real network calls are made.
The module-level _client is reset between tests to prevent state leakage.

Test cases:
  1. Happy path     — generate() returns a non-empty string.
  2. Empty response — generate() raises ValueError when the model returns blank text.
  3. Auth failure   — generate() raises EnvironmentError when the API key is invalid.
  4. Missing key    — generate() raises EnvironmentError when GEMINI_API_KEY is unset.
  5. embed()        — returns a list of floats on success.
  6. embed() error  — raises RuntimeError when the embed API call fails.
"""

import importlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(text: str) -> SimpleNamespace:
    """Build a minimal fake Gemini generate_content response."""
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=20)
    return SimpleNamespace(text=text, usage_metadata=usage, finish_reason="STOP")


def _make_embed_response(vector: list) -> SimpleNamespace:
    """Build a minimal fake Gemini embed_content response."""
    embedding = SimpleNamespace(values=vector)
    return SimpleNamespace(embeddings=[embedding])


def _reload_client_module():
    """Reload llm_client so the module-level _client is reset to None."""
    import src.llm_client as mod
    mod._client = None
    return mod


# ---------------------------------------------------------------------------
# Test 1: Happy path — successful generate() call
# ---------------------------------------------------------------------------

def test_generate_happy_path(monkeypatch):
    """generate() returns the model's text when the API call succeeds."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-happy")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")

    mod = _reload_client_module()
    fake_response = _make_response("def test_foo(): assert 1 == 1")

    mock_models = MagicMock()
    mock_models.generate_content.return_value = fake_response
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    with patch("src.llm_client.genai.Client", return_value=mock_client_instance):
        mod._client = None  # force re-init inside the patched context
        result = mod.generate(prompt="Write a simple test.", system_prompt="You are a QA agent.")

    assert isinstance(result, str)
    assert result.strip() != ""
    assert "test_foo" in result
    mock_models.generate_content.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: Empty response — generate() raises ValueError
# ---------------------------------------------------------------------------

def test_generate_empty_response(monkeypatch):
    """generate() raises ValueError when the model returns an empty string."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-empty")

    mod = _reload_client_module()
    fake_response = _make_response("")   # empty body

    mock_models = MagicMock()
    mock_models.generate_content.return_value = fake_response
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    with patch("src.llm_client.genai.Client", return_value=mock_client_instance):
        mod._client = None
        with pytest.raises(ValueError, match="empty response"):
            mod.generate(prompt="Return nothing please.")


# ---------------------------------------------------------------------------
# Test 3: Auth failure — API raises 401-style error
# ---------------------------------------------------------------------------

def test_generate_auth_failure(monkeypatch):
    """generate() raises EnvironmentError when the API returns an auth error."""
    monkeypatch.setenv("GEMINI_API_KEY", "bad-key-401")

    mod = _reload_client_module()

    mock_models = MagicMock()
    mock_models.generate_content.side_effect = Exception("API_KEY_INVALID: bad credentials")
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    with patch("src.llm_client.genai.Client", return_value=mock_client_instance):
        mod._client = None
        with pytest.raises(EnvironmentError, match="authentication failed"):
            mod.generate(prompt="Any prompt.")


# ---------------------------------------------------------------------------
# Test 4: Missing API key — raises EnvironmentError before any network call
# ---------------------------------------------------------------------------

def test_generate_missing_api_key(monkeypatch):
    """generate() raises EnvironmentError immediately when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    mod = _reload_client_module()
    mod._client = None  # ensure fresh init attempt

    with pytest.raises(EnvironmentError, match="GEMINI_API_KEY is not set"):
        mod.generate(prompt="Any prompt.")


# ---------------------------------------------------------------------------
# Test 5: embed() — returns a list of floats on success
# ---------------------------------------------------------------------------

def test_embed_returns_vector(monkeypatch):
    """embed() returns a non-empty list of floats when the API call succeeds."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-embed")

    mod = _reload_client_module()
    fake_vector = [0.1, 0.2, 0.3, 0.4, 0.5]
    fake_response = _make_embed_response(fake_vector)

    mock_models = MagicMock()
    mock_models.embed_content.return_value = fake_response
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    with patch("src.llm_client.genai.Client", return_value=mock_client_instance):
        mod._client = None
        result = mod.embed("some text to embed")

    assert isinstance(result, list)
    assert len(result) == 5
    assert all(isinstance(v, float) for v in result)


# ---------------------------------------------------------------------------
# Test 6: embed() API failure — raises RuntimeError
# ---------------------------------------------------------------------------

def test_embed_api_failure(monkeypatch):
    """embed() raises RuntimeError when the embed_content API call fails."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-embed-fail")

    mod = _reload_client_module()

    mock_models = MagicMock()
    mock_models.embed_content.side_effect = Exception("503 Service Unavailable")
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    with patch("src.llm_client.genai.Client", return_value=mock_client_instance):
        mod._client = None
        with pytest.raises(RuntimeError, match="embed_content failed"):
            mod.embed("text that triggers failure")
