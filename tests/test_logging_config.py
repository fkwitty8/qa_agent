"""
tests/test_logging_config.py — Unit tests for src/logging_config.py
and the output schema validation in src/agents.py.

Test cases:
  1.  configure_logging() creates the log file.
  2.  configure_logging() writes JSON-lines on an INFO log call.
  3.  Each JSON-line contains required fields (timestamp, level, logger, message).
  4.  configure_logging() is idempotent — duplicate handlers not added.
  5.  _validate_tool_output() returns None for a valid propose_tests output.
  6.  _validate_tool_output() returns None for an error dict (missing keys ok).
  7.  _validate_tool_output() returns an error string when required keys missing.
  8.  _validate_tool_output() covers run_tests schema.
  9.  _validate_tool_output() covers analyze_failures schema.
  10. _validate_tool_output() covers draft_issue schema.
  11. Agent loop surfaces schema validation error as status='error'.
  12. JSON-lines log is written during an agent run (integration).
"""

import json
import logging
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.logging_config as lc
from src.agents import _validate_tool_output, QAAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_root_logger():
    """Remove all handlers from root logger before/after each test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.level = original_level


# ---------------------------------------------------------------------------
# Test 1: configure_logging() creates the log file
# ---------------------------------------------------------------------------

def test_configure_logging_creates_file(tmp_path):
    """configure_logging() creates the log file at the specified path."""
    log_file = tmp_path / "test.log"
    lc.configure_logging(log_path=log_file)
    assert log_file.exists()


# ---------------------------------------------------------------------------
# Test 2: writes JSON-lines on INFO log call
# ---------------------------------------------------------------------------

def test_configure_logging_writes_json_lines(tmp_path):
    """A logged message appears as a JSON object in the log file."""
    log_file = tmp_path / "test.log"
    lc.configure_logging(log_path=log_file, level="DEBUG")

    logger = logging.getLogger("test.json")
    logger.info("hello world", extra={"event": "test_event", "run_id": "abc"})

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"


# ---------------------------------------------------------------------------
# Test 3: each JSON-line has required fields
# ---------------------------------------------------------------------------

def test_json_lines_contain_required_fields(tmp_path):
    """Every JSON-line record contains timestamp, level, logger, message."""
    log_file = tmp_path / "test.log"
    lc.configure_logging(log_path=log_file, level="DEBUG")

    logging.getLogger("test.fields").warning("check fields")

    lines = log_file.read_text().strip().splitlines()
    record = json.loads(lines[-1])
    for field in ("timestamp", "level", "logger", "message"):
        assert field in record, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Test 4: configure_logging() is idempotent
# ---------------------------------------------------------------------------

def test_configure_logging_idempotent(tmp_path):
    """Calling configure_logging() twice does not add duplicate handlers."""
    log_file = tmp_path / "test.log"
    lc.configure_logging(log_path=log_file)
    handler_count_after_first = len(logging.getLogger().handlers)
    lc.configure_logging(log_path=log_file)
    assert len(logging.getLogger().handlers) == handler_count_after_first


# ---------------------------------------------------------------------------
# Test 5: _validate_tool_output returns None for valid propose_tests output
# ---------------------------------------------------------------------------

def test_validate_tool_output_valid_propose_tests():
    """Returns None when propose_tests output has all required keys."""
    output = {"goal": "g", "context_chunks": [], "test_cases": []}
    assert _validate_tool_output("propose_tests", output) is None


# ---------------------------------------------------------------------------
# Test 6: _validate_tool_output returns None for error dicts
# ---------------------------------------------------------------------------

def test_validate_tool_output_error_dict_passes():
    """Returns None when output contains 'error' key (incomplete by design)."""
    output = {"error": "LLM call failed", "test_cases": []}
    assert _validate_tool_output("propose_tests", output) is None


# ---------------------------------------------------------------------------
# Test 7: _validate_tool_output returns error string for missing keys
# ---------------------------------------------------------------------------

def test_validate_tool_output_missing_keys():
    """Returns a descriptive error string when required keys are missing."""
    output = {"goal": "g"}   # missing context_chunks and test_cases
    result = _validate_tool_output("propose_tests", output)
    assert result is not None
    assert "context_chunks" in result or "test_cases" in result


# ---------------------------------------------------------------------------
# Test 8: validates run_tests schema
# ---------------------------------------------------------------------------

def test_validate_tool_output_run_tests_valid():
    output = {"status": "passed", "passed": 1, "failed": 0,
              "stdout": "", "stderr": "", "exit_code": 0}
    assert _validate_tool_output("run_tests", output) is None


def test_validate_tool_output_run_tests_missing():
    output = {"status": "passed"}   # missing passed, failed, stdout, stderr, exit_code
    result = _validate_tool_output("run_tests", output)
    assert result is not None


# ---------------------------------------------------------------------------
# Test 9: validates analyze_failures schema
# ---------------------------------------------------------------------------

def test_validate_tool_output_analyze_failures_valid():
    output = {"summary": "s", "root_cause": "r", "affected_files": [],
              "suggested_fix": "f", "confidence": "high"}
    assert _validate_tool_output("analyze_failures", output) is None


# ---------------------------------------------------------------------------
# Test 10: validates draft_issue schema
# ---------------------------------------------------------------------------

def test_validate_tool_output_draft_issue_valid():
    output = {"title": "t", "body": "b", "labels": [], "github_url": None}
    assert _validate_tool_output("draft_issue", output) is None


# ---------------------------------------------------------------------------
# Test 11: agent loop returns status='error' on schema validation failure
# ---------------------------------------------------------------------------

def test_agent_returns_error_on_schema_validation_failure(tmp_path):
    """Agent surfaces schema validation error as status='error'."""
    # propose_tests returns output missing 'context_chunks' and 'test_cases'
    bad_plan = {"goal": "test X"}  # missing required keys, no 'error' key

    def side_effect(name, params):
        if name == "propose_tests":
            return bad_plan
        raise ValueError(f"Unexpected: {name}")

    agent = QAAgent(max_iterations=1)
    with patch("src.agents.rag.retrieve", return_value=[]), \
         patch("src.agents.call_tool", side_effect=side_effect), \
         patch("src.agents._TRACES_DIR", tmp_path):
        result = agent.run("test something")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Test 12: JSON-lines log written during agent run
# ---------------------------------------------------------------------------

def test_agent_run_writes_json_lines_log(tmp_path):
    """Agent run produces at least one JSON-lines entry in app.log."""
    log_file = tmp_path / "app.log"
    lc.configure_logging(log_path=log_file, level="DEBUG")

    plan = {"goal": "g", "context_chunks": [], "test_cases": [
        {"name": "test_x", "type": "unit", "description": "d",
         "code": "def test_x():\n    assert True\n"}
    ]}
    run_result = {"status": "passed", "passed": 1, "failed": 0,
                  "stdout": "1 passed", "stderr": "", "exit_code": 0}
    draft = {"title": "t", "body": "b", "labels": [], "github_url": None}

    def side_effect(name, params):
        if name == "propose_tests":    return plan
        if name == "run_tests":        return run_result
        if name == "analyze_failures": return {"summary": "s", "root_cause": "r",
                                               "affected_files": [], "suggested_fix": "f",
                                               "confidence": "high"}
        if name == "draft_issue":      return draft
        raise ValueError(name)

    agent = QAAgent(max_iterations=1)
    with patch("src.agents.rag.retrieve", return_value=[]), \
         patch("src.agents.call_tool", side_effect=side_effect), \
         patch("src.agents._TRACES_DIR", tmp_path):
        agent.run("test something")

    assert log_file.exists()
    lines = [l for l in log_file.read_text().strip().splitlines() if l.strip()]
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert "timestamp" in record
    assert "message" in record
