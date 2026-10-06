"""
tools.py — Tool implementations for the QA Agent.

Each tool is defined by a schema dict (TOOL_REGISTRY) and a callable.
Only tools listed in TOOL_REGISTRY are permitted; calls to any other
tool name are rejected with a ToolNotAllowedError.

Tools implemented here (Tasks 4 & 5):
  - propose_tests    — RAG + Gemini → structured test plan
  - run_tests        — syntax check → Docker → pytest results
  - analyze_failures — Gemini → structured root-cause analysis
  - draft_issue      — GitHub issue draft; posts only when approved=True

Usage:
    from src.tools import call_tool

    plan     = call_tool("propose_tests", {"issue_description": "test the RAG module"})
    result   = call_tool("run_tests", {"test_code": plan["test_cases"][0]["code"]})
    analysis = call_tool("analyze_failures", {"test_results": result})
    draft    = call_tool("draft_issue", {"analysis": analysis})              # draft only
    posted   = call_tool("draft_issue", {"analysis": analysis, "approved": True})  # posts
"""

import ast
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import docker
import docker.errors
from dotenv import load_dotenv
from github import Github, GithubException

from src import llm_client, rag
from src.prompt_loader import fill_template, load_prompt

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool registry — ONLY these names are permitted by the allow-list
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, dict] = {
    "propose_tests": {
        "name": "propose_tests",
        "description": (
            "Retrieve relevant code context via RAG and use Gemini to generate "
            "a structured pytest test plan for the given goal description."
        ),
        "input_schema": {
            "issue_description": {
                "type": "string",
                "required": True,
                "description": "Short description of the behaviour or module to test.",
            }
        },
        "output_schema": {
            "goal": "string",
            "context_chunks": "list[dict]  — top RAG chunks used",
            "test_cases": (
                "list[dict{name, type, description, code}]"
            ),
        },
        "permissions": ["read:corpus", "call:llm"],
        "failure_behaviour": (
            "Returns a dict with 'error' key on RAG or LLM failure. "
            "Never raises to caller — errors are captured and returned."
        ),
    },
    "run_tests": {
        "name": "run_tests",
        "description": (
            "Validate generated test code with ast.parse(), write it to a "
            "temp file, run pytest inside a Docker sandbox, and return "
            "structured pass/fail results."
        ),
        "input_schema": {
            "test_code": {
                "type": "string",
                "required": True,
                "description": "Complete pytest test module as a Python string.",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": 30,
                "description": "Max seconds for the Docker container to run.",
            },
        },
        "output_schema": {
            "status": "string — 'passed' | 'failed' | 'error'",
            "passed": "int",
            "failed": "int",
            "stdout": "string",
            "stderr": "string",
            "exit_code": "int",
        },
        "permissions": ["execute:docker_sandbox"],
        "failure_behaviour": (
            "Returns status='error' with a descriptive message for syntax "
            "errors, Docker unavailability, and timeouts. Container is always "
            "cleaned up via try/finally even on exception."
        ),
    },
    "analyze_failures": {
        "name": "analyze_failures",
        "description": (
            "Retrieve relevant source context via RAG, then call Gemini with "
            "the failure_analyzer prompt to produce a structured root-cause "
            "analysis of pytest failure output."
        ),
        "input_schema": {
            "test_results": {
                "type": "dict",
                "required": True,
                "description": "Output dict from run_tests (must contain 'stdout' and 'status').",
            }
        },
        "output_schema": {
            "summary": "string",
            "root_cause": "string",
            "affected_files": "list[string]",
            "suggested_fix": "string",
            "confidence": "string — high | medium | low",
        },
        "permissions": ["read:corpus", "call:llm"],
        "failure_behaviour": "Returns dict with 'error' key on LLM failure. Never raises.",
    },
    "draft_issue": {
        "name": "draft_issue",
        "description": (
            "Format a failure analysis into a GitHub issue using the "
            "issue_drafter prompt, then optionally post it via PyGithub. "
            "The GitHub API is only called when approved=True."
        ),
        "input_schema": {
            "analysis": {
                "type": "dict",
                "required": True,
                "description": "Output dict from analyze_failures.",
            },
            "approved": {
                "type": "bool",
                "required": False,
                "default": False,
                "description": "Set True to post to GitHub. Default False (draft only).",
            },
            "goal": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Original agent goal string for traceability in the issue body.",
            },
        },
        "output_schema": {
            "title": "string — max 72 chars",
            "body": "string — markdown issue body",
            "labels": "list[string] — ['bug', 'qa-agent']",
            "github_url": "string | None — None until approved=True and posted",
        },
        "permissions": ["call:llm", "call:github_api (only when approved=True)"],
        "failure_behaviour": (
            "Returns dict with 'error' key on auth or API failure. "
            "Draft is always returned regardless of posting outcome. "
            "Never raises to caller."
        ),
    },
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ToolNotAllowedError(Exception):
    """Raised when a caller requests a tool not in TOOL_REGISTRY."""


# ---------------------------------------------------------------------------
# Allow-list enforcer
# ---------------------------------------------------------------------------

def call_tool(name: str, params: dict[str, Any]) -> dict:
    """Dispatch a tool call through the allow-list.

    Args:
        name:   The tool name. Must be a key in TOOL_REGISTRY.
        params: Keyword arguments forwarded to the tool function.

    Returns:
        The tool's result dict.

    Raises:
        ToolNotAllowedError: If *name* is not in TOOL_REGISTRY.
        ValueError:          If a required parameter is missing.
    """
    if name not in TOOL_REGISTRY:
        raise ToolNotAllowedError(
            f"Tool '{name}' is not in the allow-list. "
            f"Permitted tools: {list(TOOL_REGISTRY.keys())}"
        )

    schema = TOOL_REGISTRY[name]
    _validate_params(name, params, schema["input_schema"])

    logger.info(
        "Tool call",
        extra={"event": "tool_call", "tool": name, "params_keys": list(params.keys())},
    )

    _TOOL_FUNCTIONS = {
        "propose_tests":    propose_tests,
        "run_tests":        run_tests,
        "analyze_failures": analyze_failures,
        "draft_issue":      draft_issue,
    }

    t0 = time.monotonic()
    result = _TOOL_FUNCTIONS[name](**params)
    latency_ms = int((time.monotonic() - t0) * 1000)

    logger.info(
        "Tool call complete",
        extra={
            "event": "tool_call_complete",
            "tool": name,
            "latency_ms": latency_ms,
            "status": result.get("status", result.get("error", "ok")),
        },
    )

    return result


def _validate_params(tool_name: str, params: dict, schema: dict) -> None:
    """Raise ValueError if any required param is missing from *params*."""
    for field, spec in schema.items():
        if isinstance(spec, dict) and spec.get("required", False):
            if field not in params:
                raise ValueError(
                    f"Tool '{tool_name}' requires parameter '{field}'. "
                    f"Provided: {list(params.keys())}"
                )


# ---------------------------------------------------------------------------
# Tool 1: propose_tests
# ---------------------------------------------------------------------------

def propose_tests(issue_description: str) -> dict:
    """Retrieve relevant context via RAG and generate a pytest test plan.

    Args:
        issue_description: Short description of the behaviour to test.

    Returns:
        dict with keys:
          - ``goal``           (str)        — the original issue_description
          - ``context_chunks`` (list[dict]) — top RAG results used
          - ``test_cases``     (list[dict]) — each with name/type/description/code
          - ``error``          (str)        — present only on failure
    """
    # --- SENSE: retrieve relevant context ---
    try:
        chunks = rag.retrieve(issue_description, top_k=5)
    except Exception as exc:
        logger.error("RAG retrieval failed in propose_tests: %s", exc)
        return {"goal": issue_description, "context_chunks": [], "test_cases": [],
                "error": f"RAG retrieval failed: {exc}"}

    context_text = _format_context(chunks)

    # --- PLAN: call Gemini with test_planner prompt ---
    try:
        template = load_prompt("test_planner")
        prompt = fill_template(template, {
            "GOAL":    issue_description,
            "CONTEXT": context_text,
        })
        raw = llm_client.generate(prompt)
    except Exception as exc:
        logger.error("LLM call failed in propose_tests: %s", exc)
        return {"goal": issue_description, "context_chunks": chunks, "test_cases": [],
                "error": f"LLM call failed: {exc}"}

    # --- Parse JSON response ---
    try:
        # Strip accidental markdown fences (defensive — v1.1 prompt should prevent this)
        clean = _strip_fences(raw)
        plan = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed in propose_tests: %s", exc)
        return {"goal": issue_description, "context_chunks": chunks, "test_cases": [],
                "error": f"Model returned non-JSON output: {exc}. Raw: {raw[:200]}"}

    return {
        "goal":           issue_description,
        "context_chunks": chunks,
        "test_cases":     plan.get("test_cases", []),
    }


# ---------------------------------------------------------------------------
# Tool 2: run_tests
# ---------------------------------------------------------------------------

def run_tests(test_code: str, timeout: int = 30) -> dict:
    """Validate and execute *test_code* inside a Docker sandbox.

    Steps:
      1. Validate Python syntax with ast.parse().
      2. Write test code to a temp file.
      3. Start a Docker container (python:3.11-slim).
      4. Copy the temp file into the container and run pytest.
      5. Capture stdout/stderr and return structured results.
      6. Always remove the container (try/finally).

    Args:
        test_code: Complete pytest module as a Python string.
        timeout:   Max seconds for the container to run (default 30).

    Returns:
        dict with keys: status, passed, failed, stdout, stderr, exit_code.
        status is one of: 'passed' | 'failed' | 'error'
    """
    # Step 1: syntax validation
    try:
        ast.parse(test_code)
    except SyntaxError as exc:
        logger.error("Syntax error in generated test code: %s", exc)
        return _error_result(f"Syntax error in test code: {exc}")

    docker_image = os.getenv("DOCKER_IMAGE", "python:3.11-slim")
    container = None

    # Step 2: write to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="qa_test_", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(test_code)
        tmp_path = tmp.name

    try:
        # Step 3: get Docker client
        try:
            client = docker.from_env()
        except docker.errors.DockerException as exc:
            return _error_result(f"Docker is unavailable: {exc}")

        # Step 4: run pytest in container
        # Mount the temp file as /test_module.py inside the container
        tmp_posix = Path(tmp_path).as_posix()
        try:
            container = client.containers.run(
                image=docker_image,
                command=[
                    "sh", "-c",
                    "pip install pytest --quiet --disable-pip-version-check "
                    "&& pytest /test_module.py -v --tb=short 2>&1"
                ],
                volumes={tmp_path: {"bind": "/test_module.py", "mode": "ro"}},
                remove=False,          # we clean up manually to capture logs
                detach=True,
            )

            # Wait for the container with timeout
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", 1)
            stdout = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            stderr = ""

        except docker.errors.ImageNotFound:
            return _error_result(
                f"Docker image '{docker_image}' not found. "
                f"Run: docker pull {docker_image}"
            )
        except docker.errors.APIError as exc:
            return _error_result(f"Docker API error: {exc}")
        except Exception as exc:
            # Catches ReadTimeout from container.wait()
            exc_str = str(exc)
            if "timeout" in exc_str.lower() or "timed out" in exc_str.lower():
                return _error_result(
                    f"Test run timed out after {timeout}s."
                )
            return _error_result(f"Docker run error: {exc}")

        # Step 5: parse pytest output
        passed, failed = _parse_pytest_counts(stdout)
        status = "passed" if exit_code == 0 else "failed"

        logger.info(
            "Docker test run complete",
            extra={
                "event": "run_tests_complete",
                "status": status,
                "passed": passed,
                "failed": failed,
                "exit_code": exit_code,
            },
        )

        return {
            "status":    status,
            "passed":    passed,
            "failed":    failed,
            "stdout":    stdout,
            "stderr":    stderr,
            "exit_code": exit_code,
        }

    finally:
        # Step 6: always clean up container and temp file
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tool 3: analyze_failures
# ---------------------------------------------------------------------------

def analyze_failures(test_results: dict) -> dict:
    """Analyse pytest failure output and return a structured root-cause summary.

    Retrieves relevant source context via RAG using the failed test output as
    the query, then calls Gemini with the failure_analyzer prompt.

    Args:
        test_results: Output dict from run_tests — must contain 'stdout'.

    Returns:
        dict with keys: summary, root_cause, affected_files, suggested_fix,
        confidence. An 'error' key is present only on failure.
    """
    stdout = test_results.get("stdout", "")
    status = test_results.get("status", "")

    # If tests passed there is nothing to analyse
    if status == "passed":
        return {
            "summary":       "All tests passed. No failures to analyse.",
            "root_cause":    "None identified.",
            "affected_files": [],
            "suggested_fix": "No fix required.",
            "confidence":    "high",
        }

    # Retrieve relevant source context using the failure output as the query
    try:
        query = stdout[:500] if stdout else "test failure analysis"
        chunks = rag.retrieve(query, top_k=5)
    except Exception as exc:
        logger.warning("RAG retrieval skipped in analyze_failures: %s", exc)
        chunks = []

    source_context = _format_context(chunks)

    # Call Gemini with the failure_analyzer prompt
    try:
        template = load_prompt("failure_analyzer")
        prompt = fill_template(template, {
            "SOURCE_CONTEXT": source_context,
            "TEST_RESULTS":   stdout,
        })
        raw = llm_client.generate(prompt)
    except Exception as exc:
        logger.error("LLM call failed in analyze_failures: %s", exc)
        return {
            "summary": "", "root_cause": "", "affected_files": [],
            "suggested_fix": "", "confidence": "low",
            "error": f"LLM call failed: {exc}",
        }

    try:
        clean = _strip_fences(raw)
        analysis = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed in analyze_failures: %s", exc)
        return {
            "summary": "", "root_cause": "", "affected_files": [],
            "suggested_fix": "", "confidence": "low",
            "error": f"Model returned non-JSON output: {exc}. Raw: {raw[:200]}",
        }

    # Normalise — ensure all expected keys are present
    return {
        "summary":        analysis.get("summary", ""),
        "root_cause":     analysis.get("root_cause", ""),
        "affected_files": analysis.get("affected_files", []),
        "suggested_fix":  analysis.get("suggested_fix", ""),
        "confidence":     analysis.get("confidence", "low"),
    }


# ---------------------------------------------------------------------------
# Tool 4: draft_issue
# ---------------------------------------------------------------------------

def draft_issue(
    analysis: dict,
    approved: bool = False,
    goal: str = "",
) -> dict:
    """Draft a GitHub issue from a failure analysis; post only when approved.

    The issue body is generated by calling Gemini with the issue_drafter prompt,
    which formats the analysis into readable markdown.  The draft is always
    returned for UI preview.  The GitHub API is called ONLY when approved=True.

    Args:
        analysis: Output dict from analyze_failures.
        approved: If True, post the issue to GitHub via PyGithub.
                  Defaults to False — draft only, no network call.
        goal:     The original agent goal string, included in the issue body
                  for traceability.

    Returns:
        dict with keys: title, body, labels, github_url.
        github_url is None until approved=True and the post succeeds.
        An 'error' key is present only on failure.
    """
    labels = ["bug", "qa-agent"]

    # --- Generate markdown body via Gemini ---
    try:
        template = load_prompt("issue_drafter")
        prompt = fill_template(template, {
            "ANALYSIS": json.dumps(analysis, indent=2),
            "GOAL":     goal or "Not specified.",
        })
        raw = llm_client.generate(prompt)
        clean = _strip_fences(raw)
        drafted = json.loads(clean)
        title = drafted.get("title", "QA Agent: test failure detected")[:72]
        body  = drafted.get("body", _fallback_body(analysis, goal))
    except Exception as exc:
        logger.warning(
            "LLM issue drafting failed, using fallback body: %s", exc
        )
        title = _fallback_title(analysis)
        body  = _fallback_body(analysis, goal)

    draft = {
        "title":      title,
        "body":       body,
        "labels":     labels,
        "github_url": None,
    }

    # --- Human approval gate ---
    if not approved:
        logger.info(
            "Issue draft ready — awaiting human approval before posting.",
            extra={"event": "draft_issue_pending", "title": title},
        )
        return draft

    # --- Post to GitHub (only reached when approved=True) ---
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo_name = os.getenv("GITHUB_REPO", "").strip()

    if not token:
        return {**draft, "error": (
            "GITHUB_TOKEN is not set. "
            "Add it to .env to enable GitHub issue posting."
        )}
    if not repo_name:
        return {**draft, "error": (
            "GITHUB_REPO is not set. "
            "Add it to .env in 'owner/repo' format."
        )}

    try:
        gh = Github(token)
        repo = gh.get_repo(repo_name)
        issue = repo.create_issue(
            title=title,
            body=body,
            labels=labels,
        )
        github_url = issue.html_url
        logger.info(
            "Issue posted to GitHub",
            extra={"event": "draft_issue_posted", "url": github_url},
        )
        return {**draft, "github_url": github_url}

    except GithubException as exc:
        status_code = getattr(exc, "status", None)
        if status_code in (401, 403):
            msg = (
                f"GitHub authentication failed (HTTP {status_code}). "
                "Check that GITHUB_TOKEN is valid and has 'repo' scope."
            )
        elif status_code == 404:
            msg = (
                f"GitHub repo '{repo_name}' not found (HTTP 404). "
                "Check GITHUB_REPO is in 'owner/repo' format and the token has access."
            )
        else:
            msg = f"GitHub API error (HTTP {status_code}): {exc}"
        logger.error("GitHub issue post failed: %s", msg)
        return {**draft, "error": msg}
    except Exception as exc:
        msg = f"Unexpected error posting GitHub issue: {exc}"
        logger.error(msg)
        return {**draft, "error": msg}


# ---------------------------------------------------------------------------
# Fallback formatters (used when issue_drafter LLM call fails)
# ---------------------------------------------------------------------------

def _fallback_title(analysis: dict) -> str:
    """Generate a plain-text issue title from the analysis summary."""
    summary = analysis.get("summary", "")
    if summary:
        # Truncate to 72 chars, trim at last word boundary
        title = f"QA Agent: {summary}"
        if len(title) > 72:
            title = title[:69].rsplit(" ", 1)[0] + "..."
        return title
    return "QA Agent: test failure detected"


def _fallback_body(analysis: dict, goal: str) -> str:
    """Generate a markdown issue body directly from the analysis dict."""
    affected = "\n".join(
        f"- `{f}`" for f in analysis.get("affected_files", [])
    ) or "- Not identified"

    return (
        "## Summary\n\n"
        f"{analysis.get('summary', '_Not available._')}\n\n"
        "## Root Cause\n\n"
        f"{analysis.get('root_cause', '_Not identified._')}\n\n"
        "## Affected Files\n\n"
        f"{affected}\n\n"
        "## Suggested Fix\n\n"
        f"{analysis.get('suggested_fix', '_Not available._')}\n\n"
        "## Confidence\n\n"
        f"{analysis.get('confidence', 'low').capitalize()}\n\n"
        "## Context\n\n"
        f"**Goal:** {goal or 'Not specified.'}\n\n"
        "_This issue was drafted automatically by the QA Agent "
        "and requires human review before any action is taken._\n"
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _format_context(chunks: list[dict]) -> str:
    """Format RAG chunks into a numbered context block for the prompt."""
    if not chunks:
        return "No relevant context retrieved from the corpus."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['file_path']} ({c['chunk_type']}: {c['chunk_name']})\n"
            f"{c['text']}"
        )
    return "\n\n---\n\n".join(parts)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from *text* if present."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    """Extract passed/failed counts from pytest summary line.

    Handles lines like: '3 passed', '1 failed, 2 passed', '5 passed in 0.12s'
    Returns (passed, failed) as ints; defaults to (0, 0) if not found.
    """
    import re
    passed = failed = 0
    # Look for the final summary line
    for line in reversed(output.splitlines()):
        p = re.search(r"(\d+) passed", line)
        f = re.search(r"(\d+) failed", line)
        if p or f:
            passed = int(p.group(1)) if p else 0
            failed = int(f.group(1)) if f else 0
            break
    return passed, failed


def _error_result(message: str) -> dict:
    """Return a standardised error result dict for run_tests."""
    return {
        "status":    "error",
        "passed":    0,
        "failed":    0,
        "stdout":    "",
        "stderr":    message,
        "exit_code": -1,
    }
