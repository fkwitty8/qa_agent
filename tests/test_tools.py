"""
tests/test_tools.py — Unit tests for src/tools.py.

All external dependencies (RAG, LLM, Docker) are mocked.
No real network calls or container launches are made.

Test cases:
  --- allow-list ---
  1.  call_tool() raises ToolNotAllowedError for an unlisted tool name.
  2.  call_tool() raises ValueError when a required parameter is missing.
  3.  call_tool() dispatches successfully to propose_tests (happy path).

  --- propose_tests ---
  4.  Returns a test plan dict when RAG + LLM succeed.
  5.  Returns error dict (no raise) when RAG retrieval fails.
  6.  Returns error dict (no raise) when LLM call fails.
  7.  Returns error dict (no raise) when model returns non-JSON output.
  8.  Strips markdown fences from model output before JSON parsing.

  --- run_tests ---
  9.  Returns passed/failed counts on a successful Docker run (exit 0).
  10. Returns status='failed' when pytest exits non-zero.
  11. Returns status='error' when test_code has a SyntaxError.
  12. Returns status='error' when Docker daemon is unavailable.
  13. Returns status='error' when the container run times out.
  14. Container is always removed even when an exception is raised mid-run.

  --- helpers ---
  15. _strip_fences() removes ```json ... ``` wrappers.
  16. _parse_pytest_counts() parses standard pytest summary lines correctly.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

import src.tools as tools
from src.tools import (
    ToolNotAllowedError,
    call_tool,
    propose_tests,
    run_tests,
    _strip_fences,
    _parse_pytest_counts,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

VALID_TEST_CODE = (
    "def test_addition():\n"
    "    assert 1 + 1 == 2\n"
)

VALID_PLAN_JSON = json.dumps({
    "goal": "test the RAG retriever",
    "test_cases": [
        {
            "name": "test_retrieve_returns_list",
            "type": "unit",
            "description": "retrieve() returns a list.",
            "code": "def test_retrieve_returns_list():\n    assert True\n",
        }
    ],
})

SAMPLE_CHUNKS = [
    {
        "text": "def retrieve(query): ...",
        "file_path": "src/rag.py",
        "chunk_type": "function",
        "chunk_name": "retrieve",
        "score": 0.15,
    }
]


def _mock_docker_success(exit_code=0, logs=b"1 passed in 0.10s"):
    """Return a mock docker.from_env() whose container.wait returns exit_code."""
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.return_value = logs

    client = MagicMock()
    client.containers.run.return_value = container
    return client, container


# ---------------------------------------------------------------------------
# Test 1: call_tool() rejects unlisted tool names
# ---------------------------------------------------------------------------

def test_call_tool_rejects_unknown_tool():
    """call_tool() raises ToolNotAllowedError for a tool not in TOOL_REGISTRY."""
    with pytest.raises(ToolNotAllowedError, match="not in the allow-list"):
        call_tool("delete_everything", {})


# ---------------------------------------------------------------------------
# Test 2: call_tool() rejects missing required parameters
# ---------------------------------------------------------------------------

def test_call_tool_rejects_missing_required_param():
    """call_tool() raises ValueError when a required param is absent."""
    with pytest.raises(ValueError, match="requires parameter 'issue_description'"):
        call_tool("propose_tests", {})   # issue_description is required


# ---------------------------------------------------------------------------
# Test 3: call_tool() dispatches to propose_tests successfully
# ---------------------------------------------------------------------------

def test_call_tool_dispatches_to_propose_tests():
    """call_tool('propose_tests', ...) calls propose_tests and returns its result."""
    expected = {"goal": "test X", "context_chunks": [], "test_cases": []}
    with patch("src.tools.propose_tests", return_value=expected) as mock_fn:
        result = call_tool("propose_tests", {"issue_description": "test X"})
    mock_fn.assert_called_once_with(issue_description="test X")
    assert result == expected


# ---------------------------------------------------------------------------
# Test 4: propose_tests() happy path
# ---------------------------------------------------------------------------

def test_propose_tests_happy_path():
    """propose_tests() returns goal, context_chunks, and test_cases on success."""
    with patch("src.tools.rag.retrieve", return_value=SAMPLE_CHUNKS), \
         patch("src.tools.load_prompt", return_value="{{GOAL}} {{CONTEXT}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_PLAN_JSON):

        result = propose_tests("test the RAG retriever")

    assert result["goal"] == "test the RAG retriever"
    assert result["context_chunks"] == SAMPLE_CHUNKS
    assert len(result["test_cases"]) == 1
    assert result["test_cases"][0]["name"] == "test_retrieve_returns_list"
    assert "error" not in result


# ---------------------------------------------------------------------------
# Test 5: propose_tests() — RAG failure returns error dict, does not raise
# ---------------------------------------------------------------------------

def test_propose_tests_rag_failure_returns_error():
    """propose_tests() returns an error dict when RAG retrieval raises."""
    with patch("src.tools.rag.retrieve", side_effect=RuntimeError("chroma down")):
        result = propose_tests("test something")

    assert "error" in result
    assert "RAG retrieval failed" in result["error"]
    assert result["test_cases"] == []


# ---------------------------------------------------------------------------
# Test 6: propose_tests() — LLM failure returns error dict, does not raise
# ---------------------------------------------------------------------------

def test_propose_tests_llm_failure_returns_error():
    """propose_tests() returns an error dict when the LLM call raises."""
    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{GOAL}} {{CONTEXT}}"), \
         patch("src.tools.llm_client.generate", side_effect=RuntimeError("503")):

        result = propose_tests("test something")

    assert "error" in result
    assert "LLM call failed" in result["error"]
    assert result["test_cases"] == []


# ---------------------------------------------------------------------------
# Test 7: propose_tests() — non-JSON model output returns error dict
# ---------------------------------------------------------------------------

def test_propose_tests_non_json_response_returns_error():
    """propose_tests() returns an error dict when the model returns non-JSON text."""
    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{GOAL}} {{CONTEXT}}"), \
         patch("src.tools.llm_client.generate", return_value="Sorry, I cannot help."):

        result = propose_tests("test something")

    assert "error" in result
    assert "non-JSON" in result["error"]
    assert result["test_cases"] == []


# ---------------------------------------------------------------------------
# Test 8: propose_tests() strips markdown fences before parsing
# ---------------------------------------------------------------------------

def test_propose_tests_strips_markdown_fences():
    """propose_tests() handles model output wrapped in ```json ... ``` fences."""
    fenced = f"```json\n{VALID_PLAN_JSON}\n```"

    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{GOAL}} {{CONTEXT}}"), \
         patch("src.tools.llm_client.generate", return_value=fenced):

        result = propose_tests("test the RAG retriever")

    assert "error" not in result
    assert len(result["test_cases"]) == 1


# ---------------------------------------------------------------------------
# Test 9: run_tests() happy path — exit 0, counts parsed correctly
# ---------------------------------------------------------------------------

def test_run_tests_happy_path():
    """run_tests() returns status='passed' and correct counts on exit 0."""
    mock_client, mock_container = _mock_docker_success(
        exit_code=0,
        logs=b"test_addition PASSED\n\n1 passed in 0.10s\n",
    )

    with patch("src.tools.docker.from_env", return_value=mock_client):
        result = run_tests(VALID_TEST_CODE, timeout=10)

    assert result["status"] == "passed"
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["exit_code"] == 0


# ---------------------------------------------------------------------------
# Test 10: run_tests() — non-zero exit code → status='failed'
# ---------------------------------------------------------------------------

def test_run_tests_failed_exit_code():
    """run_tests() returns status='failed' when pytest exits non-zero."""
    mock_client, mock_container = _mock_docker_success(
        exit_code=1,
        logs=b"test_addition FAILED\n\n1 failed in 0.12s\n",
    )

    with patch("src.tools.docker.from_env", return_value=mock_client):
        result = run_tests(VALID_TEST_CODE, timeout=10)

    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["exit_code"] == 1


# ---------------------------------------------------------------------------
# Test 11: run_tests() — SyntaxError in test code → status='error'
# ---------------------------------------------------------------------------

def test_run_tests_syntax_error_returns_error():
    """run_tests() returns status='error' without touching Docker on SyntaxError."""
    bad_code = "def test_broken(\n    # unclosed\n"

    with patch("src.tools.docker.from_env") as mock_docker:
        result = run_tests(bad_code)

    assert result["status"] == "error"
    assert "Syntax error" in result["stderr"]
    mock_docker.assert_not_called()   # Docker should never be reached


# ---------------------------------------------------------------------------
# Test 12: run_tests() — Docker unavailable → status='error'
# ---------------------------------------------------------------------------

def test_run_tests_docker_unavailable_returns_error():
    """run_tests() returns status='error' when Docker daemon is not running."""
    import docker.errors

    with patch(
        "src.tools.docker.from_env",
        side_effect=docker.errors.DockerException("Cannot connect to Docker daemon"),
    ):
        result = run_tests(VALID_TEST_CODE, timeout=10)

    assert result["status"] == "error"
    assert "Docker is unavailable" in result["stderr"]


# ---------------------------------------------------------------------------
# Test 13: run_tests() — container.wait() times out → status='error'
# ---------------------------------------------------------------------------

def test_run_tests_timeout_returns_error():
    """run_tests() returns status='error' when the container exceeds the timeout."""
    container = MagicMock()
    container.wait.side_effect = Exception("timed out waiting for container")

    mock_client = MagicMock()
    mock_client.containers.run.return_value = container

    with patch("src.tools.docker.from_env", return_value=mock_client):
        result = run_tests(VALID_TEST_CODE, timeout=1)

    assert result["status"] == "error"
    assert "timed out" in result["stderr"].lower()


# ---------------------------------------------------------------------------
# Test 14: run_tests() — container.remove() is always called (try/finally)
# ---------------------------------------------------------------------------

def test_run_tests_container_always_removed():
    """Container.remove(force=True) is called even when container.wait() raises."""
    container = MagicMock()
    container.wait.side_effect = Exception("unexpected crash")

    mock_client = MagicMock()
    mock_client.containers.run.return_value = container

    with patch("src.tools.docker.from_env", return_value=mock_client):
        result = run_tests(VALID_TEST_CODE, timeout=10)

    container.remove.assert_called_once_with(force=True)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Test 15: _strip_fences() removes markdown code fences
# ---------------------------------------------------------------------------

def test_strip_fences_removes_json_fences():
    """_strip_fences() strips ```json ... ``` from model output."""
    fenced = "```json\n{\"key\": \"value\"}\n```"
    assert _strip_fences(fenced) == '{"key": "value"}'


def test_strip_fences_leaves_plain_json_unchanged():
    """_strip_fences() leaves text without fences untouched."""
    plain = '{"key": "value"}'
    assert _strip_fences(plain) == plain


# ---------------------------------------------------------------------------
# Test 16: _parse_pytest_counts() parses summary lines correctly
# ---------------------------------------------------------------------------

def test_parse_pytest_counts_passed_only():
    output = "test_foo PASSED\n\n3 passed in 0.12s\n"
    assert _parse_pytest_counts(output) == (3, 0)


def test_parse_pytest_counts_mixed():
    output = "test_foo PASSED\ntest_bar FAILED\n\n1 failed, 2 passed in 0.20s\n"
    assert _parse_pytest_counts(output) == (2, 1)


def test_parse_pytest_counts_no_summary():
    """Returns (0, 0) gracefully when no summary line is present."""
    assert _parse_pytest_counts("no pytest output here") == (0, 0)


# ===========================================================================
# Task 5 tests — analyze_failures and draft_issue
# ===========================================================================

from src.tools import analyze_failures, draft_issue, _fallback_body, _fallback_title

SAMPLE_TEST_RESULTS_FAILED = {
    "status": "failed",
    "passed": 0,
    "failed": 1,
    "stdout": "FAILED tests/test_rag.py::test_retrieve - AssertionError\n1 failed in 0.12s",
    "stderr": "",
    "exit_code": 1,
}

SAMPLE_TEST_RESULTS_PASSED = {
    "status": "passed",
    "passed": 3,
    "failed": 0,
    "stdout": "3 passed in 0.10s",
    "stderr": "",
    "exit_code": 0,
}

SAMPLE_ANALYSIS = {
    "summary": "retrieve() raises when collection is empty.",
    "root_cause": "rag.retrieve() does not guard against empty collection before querying.",
    "affected_files": ["src/rag.py"],
    "suggested_fix": "Add `if collection.count() == 0: return []` before the query call.",
    "confidence": "high",
}

VALID_DRAFT_JSON = json.dumps({
    "title": "rag: retrieve() raises on empty collection",
    "body": "## Summary\n\nretrieve() raises when the collection is empty.\n",
})


# ---------------------------------------------------------------------------
# Test 17: analyze_failures() — happy path, returns structured analysis
# ---------------------------------------------------------------------------

def test_analyze_failures_happy_path():
    """analyze_failures() returns all required keys on a successful LLM call."""
    analysis_json = json.dumps({
        "summary": "One test failed due to missing guard.",
        "root_cause": "retrieve() in src/rag.py line 42.",
        "affected_files": ["src/rag.py"],
        "suggested_fix": "Add empty-collection check.",
        "confidence": "high",
    })

    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{SOURCE_CONTEXT}} {{TEST_RESULTS}}"), \
         patch("src.tools.llm_client.generate", return_value=analysis_json):

        result = analyze_failures(SAMPLE_TEST_RESULTS_FAILED)

    assert "error" not in result
    assert result["summary"] != ""
    assert result["root_cause"] != ""
    assert isinstance(result["affected_files"], list)
    assert result["confidence"] in ("high", "medium", "low")


# ---------------------------------------------------------------------------
# Test 18: analyze_failures() — short-circuits when all tests passed
# ---------------------------------------------------------------------------

def test_analyze_failures_short_circuits_on_passed():
    """analyze_failures() returns a no-failure summary without calling LLM."""
    with patch("src.tools.llm_client.generate") as mock_llm:
        result = analyze_failures(SAMPLE_TEST_RESULTS_PASSED)

    mock_llm.assert_not_called()
    assert "All tests passed" in result["summary"]
    assert result["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 19: analyze_failures() — LLM failure returns error dict, does not raise
# ---------------------------------------------------------------------------

def test_analyze_failures_llm_failure_returns_error():
    """analyze_failures() returns an error dict when the LLM call raises."""
    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{SOURCE_CONTEXT}} {{TEST_RESULTS}}"), \
         patch("src.tools.llm_client.generate", side_effect=RuntimeError("503")):

        result = analyze_failures(SAMPLE_TEST_RESULTS_FAILED)

    assert "error" in result
    assert "LLM call failed" in result["error"]


# ---------------------------------------------------------------------------
# Test 20: analyze_failures() — non-JSON model output returns error dict
# ---------------------------------------------------------------------------

def test_analyze_failures_non_json_response_returns_error():
    """analyze_failures() returns an error dict when the model returns non-JSON."""
    with patch("src.tools.rag.retrieve", return_value=[]), \
         patch("src.tools.load_prompt", return_value="{{SOURCE_CONTEXT}} {{TEST_RESULTS}}"), \
         patch("src.tools.llm_client.generate", return_value="I cannot help with that."):

        result = analyze_failures(SAMPLE_TEST_RESULTS_FAILED)

    assert "error" in result
    assert "non-JSON" in result["error"]


# ---------------------------------------------------------------------------
# Test 21: draft_issue() — approved=False returns draft without posting
# ---------------------------------------------------------------------------

def test_draft_issue_no_post_when_not_approved():
    """draft_issue() returns draft dict and does NOT call PyGithub when approved=False."""
    with patch("src.tools.load_prompt", return_value="{{ANALYSIS}} {{GOAL}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_DRAFT_JSON), \
         patch("src.tools.Github") as mock_gh:

        result = draft_issue(SAMPLE_ANALYSIS, approved=False)

    mock_gh.assert_not_called()
    assert result["github_url"] is None
    assert result["title"] != ""
    assert result["body"] != ""
    assert "bug" in result["labels"]
    assert "error" not in result


# ---------------------------------------------------------------------------
# Test 22: draft_issue() — issue body is well-formed markdown
# ---------------------------------------------------------------------------

def test_draft_issue_body_is_markdown():
    """draft_issue() body contains markdown headings."""
    with patch("src.tools.load_prompt", return_value="{{ANALYSIS}} {{GOAL}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_DRAFT_JSON):

        result = draft_issue(SAMPLE_ANALYSIS, approved=False)

    assert "##" in result["body"]


# ---------------------------------------------------------------------------
# Test 23: draft_issue() — approved=True posts to GitHub and returns URL
# ---------------------------------------------------------------------------

def test_draft_issue_posts_when_approved(monkeypatch):
    """draft_issue() calls repo.create_issue and returns github_url when approved=True."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPO", "org/repo")

    mock_issue = MagicMock()
    mock_issue.html_url = "https://github.com/org/repo/issues/42"

    mock_repo = MagicMock()
    mock_repo.create_issue.return_value = mock_issue

    mock_gh_instance = MagicMock()
    mock_gh_instance.get_repo.return_value = mock_repo

    with patch("src.tools.load_prompt", return_value="{{ANALYSIS}} {{GOAL}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_DRAFT_JSON), \
         patch("src.tools.Github", return_value=mock_gh_instance):

        result = draft_issue(SAMPLE_ANALYSIS, approved=True, goal="test RAG module")

    mock_repo.create_issue.assert_called_once()
    assert result["github_url"] == "https://github.com/org/repo/issues/42"
    assert "error" not in result


# ---------------------------------------------------------------------------
# Test 24: draft_issue() — auth failure (401) returns descriptive error
# ---------------------------------------------------------------------------

def test_draft_issue_auth_failure_returns_error(monkeypatch):
    """draft_issue() returns an error dict with a clear message on 401."""
    from github import GithubException

    monkeypatch.setenv("GITHUB_TOKEN", "bad-token")
    monkeypatch.setenv("GITHUB_REPO", "org/repo")

    mock_repo = MagicMock()
    mock_repo.create_issue.side_effect = GithubException(401, "Bad credentials", None)

    mock_gh_instance = MagicMock()
    mock_gh_instance.get_repo.return_value = mock_repo

    with patch("src.tools.load_prompt", return_value="{{ANALYSIS}} {{GOAL}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_DRAFT_JSON), \
         patch("src.tools.Github", return_value=mock_gh_instance):

        result = draft_issue(SAMPLE_ANALYSIS, approved=True)

    assert "error" in result
    assert "authentication failed" in result["error"]
    assert result["github_url"] is None


# ---------------------------------------------------------------------------
# Test 25: draft_issue() — missing GITHUB_TOKEN returns error without posting
# ---------------------------------------------------------------------------

def test_draft_issue_missing_token_returns_error(monkeypatch):
    """draft_issue() returns an error dict when GITHUB_TOKEN is not set."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPO", "org/repo")

    with patch("src.tools.load_prompt", return_value="{{ANALYSIS}} {{GOAL}}"), \
         patch("src.tools.llm_client.generate", return_value=VALID_DRAFT_JSON), \
         patch("src.tools.Github") as mock_gh:

        result = draft_issue(SAMPLE_ANALYSIS, approved=True)

    mock_gh.assert_not_called()
    assert "error" in result
    assert "GITHUB_TOKEN" in result["error"]


# ---------------------------------------------------------------------------
# Test 26: draft_issue() — LLM failure uses fallback body (no crash)
# ---------------------------------------------------------------------------

def test_draft_issue_uses_fallback_when_llm_fails():
    """draft_issue() returns a valid draft using fallback formatters when LLM fails."""
    with patch("src.tools.load_prompt", side_effect=FileNotFoundError("not found")):
        result = draft_issue(SAMPLE_ANALYSIS, approved=False, goal="test RAG")

    assert result["title"] != ""
    assert "## Summary" in result["body"]
    assert result["github_url"] is None
    assert "error" not in result


# ---------------------------------------------------------------------------
# Test 27: _fallback_body() — contains all required markdown sections
# ---------------------------------------------------------------------------

def test_fallback_body_contains_required_sections():
    """_fallback_body() generates a body with all six markdown sections."""
    body = _fallback_body(SAMPLE_ANALYSIS, "test the RAG module")

    for section in ("## Summary", "## Root Cause", "## Affected Files",
                    "## Suggested Fix", "## Confidence", "## Context"):
        assert section in body, f"Missing section: {section}"

    assert "src/rag.py" in body
    assert "QA Agent" in body


# ---------------------------------------------------------------------------
# Test 28: _fallback_title() — truncates long summaries to 72 chars
# ---------------------------------------------------------------------------

def test_fallback_title_truncates_long_summary():
    """_fallback_title() keeps the title under 72 characters."""
    long_analysis = {
        "summary": "This is a very long summary that goes well beyond the "
                   "seventy-two character limit set for GitHub issue titles."
    }
    title = _fallback_title(long_analysis)
    assert len(title) <= 72
    assert title.endswith("...")
