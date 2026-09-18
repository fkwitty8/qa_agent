"""
prompt_loader.py — Load and version prompt templates from disk.

Responsibilities:
  - Load a prompt by name from the canonical prompts/ directory.
  - Load a specific versioned prompt from prompts/versions/.
  - Raise FileNotFoundError with a clear message when a prompt is missing.
  - Optionally fill template placeholders ({{KEY}}) with caller-supplied values.

Directory conventions:
  prompts/<name>.txt                   — current (latest) version
  prompts/versions/<name>_v<ver>.txt   — archived versioned copy

Usage:
  from src.prompt_loader import load_prompt, load_versioned_prompt, fill_template

  # Load current version
  text = load_prompt("test_planner")

  # Load a specific version
  text = load_versioned_prompt("test_planner", "1.0")

  # Fill placeholders
  filled = fill_template(text, {"GOAL": "test the RAG module", "CONTEXT": "..."})
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Root of the prompts directory — resolved relative to this file's location so
# the module works regardless of the working directory the caller uses.
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _prompts_dir() -> Path:
    """Return the resolved prompts root directory.

    Separated into a function so tests can monkeypatch it cleanly.
    """
    return _PROMPTS_DIR


def load_prompt(name: str) -> str:
    """Load the current (latest) version of a prompt by name.

    Args:
        name: The prompt name without extension, e.g. ``"test_planner"``.

    Returns:
        The raw prompt text as a string.

    Raises:
        FileNotFoundError: If ``prompts/<name>.txt`` does not exist.
        ValueError:        If *name* is empty or contains path separators.
    """
    _validate_name(name)
    path = _prompts_dir() / f"{name}.txt"

    if not path.is_file():
        raise FileNotFoundError(
            f"Prompt '{name}' not found. "
            f"Expected file: {path}. "
            f"Available prompts: {_list_available()}"
        )

    text = path.read_text(encoding="utf-8")
    logger.debug("Loaded prompt '%s' from %s (%d chars)", name, path, len(text))
    return text


def load_versioned_prompt(name: str, version: str) -> str:
    """Load a specific versioned prompt from ``prompts/versions/``.

    If the versioned file does not exist, falls back to the current version
    only when *version* matches the version declared in the current file's
    header (``## Version: <ver>``).  Otherwise raises FileNotFoundError.

    Args:
        name:    The prompt name without extension, e.g. ``"test_planner"``.
        version: The version string, e.g. ``"1.0"`` or ``"1.1"``.

    Returns:
        The raw prompt text as a string.

    Raises:
        FileNotFoundError: If neither the versioned file nor a matching current
                           version exists.
        ValueError:        If *name* or *version* are empty or contain path
                           separators.
    """
    _validate_name(name)
    _validate_version(version)

    versioned_path = _prompts_dir() / "versions" / f"{name}_v{version}.txt"

    if versioned_path.is_file():
        text = versioned_path.read_text(encoding="utf-8")
        logger.debug(
            "Loaded versioned prompt '%s' v%s from %s (%d chars)",
            name, version, versioned_path, len(text),
        )
        return text

    # Fallback: check whether the current file declares the requested version
    current_path = _prompts_dir() / f"{name}.txt"
    if current_path.is_file():
        current_text = current_path.read_text(encoding="utf-8")
        declared = _extract_declared_version(current_text)
        if declared == version:
            logger.debug(
                "Versioned file not found for '%s' v%s; "
                "falling back to current (declared version matches).",
                name, version,
            )
            return current_text

    raise FileNotFoundError(
        f"Versioned prompt '{name}' v{version} not found. "
        f"Checked: {versioned_path}. "
        f"Available versions: {_list_available_versions(name)}"
    )


def fill_template(template: str, variables: dict[str, str]) -> str:
    """Replace ``{{KEY}}`` placeholders in *template* with values from *variables*.

    Placeholders are case-sensitive. Unknown placeholders (not in *variables*)
    are left as-is so callers can do partial fills.

    Args:
        template:  The raw prompt string containing ``{{KEY}}`` placeholders.
        variables: A mapping of placeholder name → replacement value.

    Returns:
        The template string with all matching placeholders replaced.
    """
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("Prompt name must not be empty.")
    if any(sep in name for sep in (os.sep, "/", "\\")):
        raise ValueError(
            f"Prompt name must not contain path separators. Got: {name!r}"
        )


def _validate_version(version: str) -> None:
    if not version or not version.strip():
        raise ValueError("Version string must not be empty.")
    if any(sep in version for sep in (os.sep, "/", "\\")):
        raise ValueError(
            f"Version string must not contain path separators. Got: {version!r}"
        )


def _extract_declared_version(text: str) -> Optional[str]:
    """Parse ``## Version: <ver>`` from the prompt header, or return None."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Version:"):
            return stripped.split(":", 1)[1].strip()
    return None


def _list_available() -> list[str]:
    """Return names of all .txt files in the prompts root (no extension)."""
    prompts_dir = _prompts_dir()
    if not prompts_dir.is_dir():
        return []
    return sorted(p.stem for p in prompts_dir.glob("*.txt"))


def _list_available_versions(name: str) -> list[str]:
    """Return version strings available for *name* in prompts/versions/."""
    versions_dir = _prompts_dir() / "versions"
    if not versions_dir.is_dir():
        return []
    prefix = f"{name}_v"
    return sorted(
        p.stem[len(prefix):]
        for p in versions_dir.glob(f"{name}_v*.txt")
    )
