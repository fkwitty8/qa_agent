"""
tests/test_prompt_loader.py — Unit tests for src/prompt_loader.py.

All tests use a temporary directory structure so no real prompt files are
required on disk. The _PROMPTS_DIR module variable is monkeypatched to point
at the tmp directory for each test.

Test cases:
  1. load_prompt()            — returns file content for an existing prompt.
  2. load_prompt() missing    — raises FileNotFoundError with a helpful message.
  3. load_prompt() empty name — raises ValueError.
  4. load_prompt() path sep   — raises ValueError when name contains '/'.
  5. load_versioned_prompt()  — returns versioned file content from versions/.
  6. version fallback         — falls back to current file when versioned file
                                is absent but declared version matches.
  7. version fallback mismatch— raises FileNotFoundError when declared version
                                does not match the requested version.
  8. load_versioned_prompt() missing — raises FileNotFoundError.
  9. fill_template()          — replaces all matching {{KEY}} placeholders.
 10. fill_template() partial  — leaves unknown placeholders untouched.
 11. fill_template() multiple — replaces the same placeholder everywhere.
"""

import importlib
from pathlib import Path

import pytest

import src.prompt_loader as loader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prompts_dir(tmp_path, monkeypatch):
    """Create a minimal prompts/ layout in tmp_path and patch the module."""
    (tmp_path / "versions").mkdir()

    # Write a canonical prompt file
    (tmp_path / "test_planner.txt").write_text(
        "## Version: 1.0\nYou are a QA engineer.\n{{GOAL}}\n{{CONTEXT}}",
        encoding="utf-8",
    )
    # Write a versioned copy
    (tmp_path / "versions" / "test_planner_v1.0.txt").write_text(
        "## Version: 1.0\nVersioned copy v1.0.\n{{GOAL}}",
        encoding="utf-8",
    )
    # Write a v1.1 versioned copy (different content)
    (tmp_path / "versions" / "test_planner_v1.1.txt").write_text(
        "## Version: 1.1\nVersioned copy v1.1.\n{{GOAL}}",
        encoding="utf-8",
    )

    # Patch the module-level _PROMPTS_DIR
    monkeypatch.setattr(loader, "_PROMPTS_DIR", tmp_path)

    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: load_prompt() — happy path
# ---------------------------------------------------------------------------

def test_load_prompt_returns_content(prompts_dir):
    """load_prompt() returns the full file content for an existing prompt."""
    text = loader.load_prompt("test_planner")
    assert "QA engineer" in text
    assert "{{GOAL}}" in text


# ---------------------------------------------------------------------------
# Test 2: load_prompt() — missing file raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_load_prompt_missing_raises(prompts_dir):
    """load_prompt() raises FileNotFoundError when the prompt file does not exist."""
    with pytest.raises(FileNotFoundError, match="not found"):
        loader.load_prompt("nonexistent_prompt")


# ---------------------------------------------------------------------------
# Test 3: load_prompt() — empty name raises ValueError
# ---------------------------------------------------------------------------

def test_load_prompt_empty_name_raises(prompts_dir):
    """load_prompt() raises ValueError when name is an empty string."""
    with pytest.raises(ValueError, match="must not be empty"):
        loader.load_prompt("")


# ---------------------------------------------------------------------------
# Test 4: load_prompt() — name with path separator raises ValueError
# ---------------------------------------------------------------------------

def test_load_prompt_path_separator_raises(prompts_dir):
    """load_prompt() raises ValueError when name contains a '/' separator."""
    with pytest.raises(ValueError, match="path separators"):
        loader.load_prompt("some/path/prompt")


# ---------------------------------------------------------------------------
# Test 5: load_versioned_prompt() — returns versioned file content
# ---------------------------------------------------------------------------

def test_load_versioned_prompt_returns_versioned_content(prompts_dir):
    """load_versioned_prompt() reads from versions/ when the file exists."""
    text = loader.load_versioned_prompt("test_planner", "1.1")
    assert "v1.1" in text
    # Should NOT be the current file content
    assert "QA engineer" not in text


# ---------------------------------------------------------------------------
# Test 6: load_versioned_prompt() — fallback to current when version matches
# ---------------------------------------------------------------------------

def test_load_versioned_prompt_fallback_when_version_matches(prompts_dir):
    """Falls back to the current file when no versioned copy exists but the
    declared version in the current file header matches the request."""
    # Remove the v1.0 versioned file so only the current file (declaring 1.0) exists
    (prompts_dir / "versions" / "test_planner_v1.0.txt").unlink()

    text = loader.load_versioned_prompt("test_planner", "1.0")
    # Should return the current file's content
    assert "QA engineer" in text


# ---------------------------------------------------------------------------
# Test 7: load_versioned_prompt() — fallback fails when version mismatch
# ---------------------------------------------------------------------------

def test_load_versioned_prompt_fallback_version_mismatch_raises(prompts_dir):
    """Raises FileNotFoundError when neither the versioned file exists nor the
    current file declares the requested version."""
    with pytest.raises(FileNotFoundError, match="not found"):
        # Current file declares 1.0; requesting 9.9 which doesn't exist anywhere
        loader.load_versioned_prompt("test_planner", "9.9")


# ---------------------------------------------------------------------------
# Test 8: load_versioned_prompt() — missing prompt entirely raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_load_versioned_prompt_missing_prompt_raises(prompts_dir):
    """Raises FileNotFoundError when the prompt name doesn't exist at all."""
    with pytest.raises(FileNotFoundError, match="not found"):
        loader.load_versioned_prompt("ghost_prompt", "1.0")


# ---------------------------------------------------------------------------
# Test 9: fill_template() — replaces all matching placeholders
# ---------------------------------------------------------------------------

def test_fill_template_replaces_placeholders():
    """fill_template() substitutes every {{KEY}} with the supplied value."""
    template = "Goal: {{GOAL}}\nContext: {{CONTEXT}}"
    filled = loader.fill_template(template, {"GOAL": "test RAG", "CONTEXT": "some code"})
    assert filled == "Goal: test RAG\nContext: some code"
    assert "{{" not in filled


# ---------------------------------------------------------------------------
# Test 10: fill_template() — unknown placeholders are left untouched
# ---------------------------------------------------------------------------

def test_fill_template_leaves_unknown_placeholders():
    """fill_template() does not remove placeholders not in the variables dict."""
    template = "Goal: {{GOAL}}\nContext: {{CONTEXT}}"
    filled = loader.fill_template(template, {"GOAL": "test RAG"})
    assert "test RAG" in filled
    assert "{{CONTEXT}}" in filled   # not provided — must remain


# ---------------------------------------------------------------------------
# Test 11: fill_template() — replaces same placeholder multiple times
# ---------------------------------------------------------------------------

def test_fill_template_replaces_repeated_placeholder():
    """fill_template() replaces all occurrences of a placeholder, not just first."""
    template = "{{GOAL}} is the {{GOAL}}"
    filled = loader.fill_template(template, {"GOAL": "target"})
    assert filled == "target is the target"
