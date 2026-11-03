"""
agents.py — Bounded QA Agent loop.

Implements QAAgent, a goal-directed agent that wires all four tools into a
SENSE → PLAN → ACT → OBSERVE → DECIDE loop with explicit iteration limits,
stop conditions, and human hand-off.

Loop steps:
  SENSE   — retrieve relevant code context via RAG
  PLAN    — propose test cases (Gemini + RAG context)
  ACT     — execute tests in Docker sandbox
  OBSERVE — analyse failures (Gemini) if any tests failed
  DECIDE  — pass → draft issue; fail + iterations remain → retry; limit hit → hand off

Stop conditions (first reached wins):
  1. All tests pass            → status "passed",  draft issue
  2. Docker unavailable        → status "error",   hand off immediately
  3. Iteration limit reached   → status "handoff", hand off with last analysis

Configuration (read from .env):
  MAX_AGENT_ITERATIONS  int  — maximum PLAN→ACT→OBSERVE cycles (default 3)

Trace output:
  Every run writes a JSON trace to evidence/traces/<run_id>.json
  Each step is recorded with: step, tool, input_summary, output_summary,
  decision, timestamp_utc.

Usage:
    from src.agents import QAAgent
    agent = QAAgent()
    result = agent.run("test that rag.retrieve() handles an empty corpus")
    print(result["status"], result["draft"])
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src import rag
from src.logging_config import configure_logging
from src.tools import call_tool

load_dotenv()
configure_logging()   # activate JSON-lines logging for every agent run

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_TRACES_DIR   = _PROJECT_ROOT / "evidence" / "traces"

# Prompt injection patterns — reject goals containing these strings
_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard your",
    "you are now",
    "forget your instructions",
    "new persona",
    "jailbreak",
    "bypass your",
    # Unauthorized-action patterns (FC-04)
    "deploy to production",
    "restart the server",
    "push to main",
    "merge to main",
]

_MAX_GOAL_CHARS = 2000


class QAAgent:
    """Bounded QA agent: SENSE → PLAN → ACT → OBSERVE → DECIDE.

    Args:
        max_iterations: Override MAX_AGENT_ITERATIONS from .env.
    """

    def __init__(self, max_iterations: Optional[int] = None):
        env_max = int(os.getenv("MAX_AGENT_ITERATIONS", "3"))
        self.max_iterations = max_iterations if max_iterations is not None else env_max

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, goal: str) -> dict:
        """Run the agent loop for *goal* and return a result dict.

        Args:
            goal: Natural-language description of what to test.

        Returns:
            dict with keys:
              - run_id        (str)         — unique run identifier
              - goal          (str)         — the original goal
              - status        (str)         — "passed" | "failed" | "handoff" | "error"
              - iterations    (int)         — number of PLAN→ACT cycles completed
              - test_results  (dict | None) — last run_tests output
              - analysis      (dict | None) — last analyze_failures output
              - draft         (dict | None) — draft_issue output (present when status="passed")
              - handoff_msg   (str | None)  — human hand-off message (present when status="handoff")
              - trace         (list[dict])  — full step-by-step execution trace
              - trace_path    (str | None)  — path to the saved trace JSON file
        """
        run_id   = uuid.uuid4().hex[:12]
        trace    = []
        started  = _now()

        logger.info("Agent run started", extra={
            "event": "agent_run_start", "run_id": run_id, "goal": goal[:120],
        })

        # --- Input validation ---
        validation_error = self._validate_goal(goal)
        if validation_error:
            result = self._build_result(
                run_id=run_id, goal=goal, status="error",
                iterations=0, test_results=None, analysis=None,
                draft=None, handoff_msg=validation_error, trace=trace,
            )
            self._save_trace(run_id, goal, trace, started)
            return result

        # --- SENSE: retrieve relevant context once at the start ---
        context_chunks = self._sense(goal, trace)

        # --- Main loop ---
        test_results = None
        analysis     = None
        iteration    = 0

        while iteration < self.max_iterations:
            iteration += 1
            logger.info("Agent iteration %d/%d", iteration, self.max_iterations,
                        extra={"event": "agent_iteration", "run_id": run_id,
                               "iteration": iteration})

            # PLAN
            plan = self._plan(goal, context_chunks, iteration, trace)
            if "error" in plan:
                plan_error = plan["error"]
                if iteration < self.max_iterations:
                    # Transient LLM/RAG failure — retry if iterations remain
                    logger.warning(
                        "Planning failed on iteration %d/%d, retrying: %s",
                        iteration, self.max_iterations, plan_error[:120],
                        extra={"event": "plan_retry", "run_id": run_id,
                               "iteration": iteration, "error": plan_error},
                    )
                    _record(trace, "PLAN", "propose_tests", goal[:80],
                            f"error: {plan_error[:80]}", "retry")
                    continue
                # Final iteration — surface as error
                result = self._build_result(
                    run_id=run_id, goal=goal, status="error",
                    iterations=iteration, test_results=None, analysis=None,
                    draft=None, handoff_msg=f"Planning failed: {plan_error}", trace=trace,
                )
                self._save_trace(run_id, goal, trace, started)
                return result

            test_cases = plan.get("test_cases", [])
            if not test_cases:
                _record(trace, "PLAN", "propose_tests", goal[:80],
                        "No test cases generated", "retry or hand off")
                if iteration >= self.max_iterations:
                    break
                continue

            # ACT — run each test case; stop at first Docker-unavailable error
            test_results, docker_error = self._act(test_cases, iteration, trace)
            if docker_error:
                # FC-05: Draft a minimal issue describing the Docker configuration problem
                docker_analysis = {
                    "summary": "Docker daemon is unavailable — tests could not be executed.",
                    "root_cause": docker_error,
                    "affected_files": [],
                    "suggested_fix": "Ensure Docker Desktop is running before starting the agent.",
                    "confidence": "high",
                }
                docker_draft = self._draft(goal, docker_analysis, iteration, trace)
                result = self._build_result(
                    run_id=run_id, goal=goal, status="error",
                    iterations=iteration, test_results=test_results,
                    analysis=docker_analysis, draft=docker_draft,
                    handoff_msg=f"Docker unavailable: {docker_error}", trace=trace,
                )
                self._save_trace(run_id, goal, trace, started)
                return result

            # DECIDE after ACT — did everything pass?
            if test_results and test_results.get("status") == "passed":
                pass_summary = _build_pass_summary(test_results, iteration)
                _record(trace, "DECIDE", None,
                        f"iteration {iteration}",
                        pass_summary,
                        "all tests passed — summary generated, no issue drafted")
                result = self._build_result(
                    run_id=run_id, goal=goal, status="passed",
                    iterations=iteration, test_results=test_results,
                    analysis=None, draft=None, handoff_msg=None, trace=trace,
                    pass_summary=pass_summary,
                )
                self._save_trace(run_id, goal, trace, started)
                return result

            # OBSERVE — analyse failures
            analysis = self._observe(test_results, iteration, trace)

            # DECIDE after OBSERVE
            if iteration >= self.max_iterations:
                break  # hand off below

            # Failures remain and iterations left — loop back to PLAN
            _record(trace, "DECIDE", None, f"iteration {iteration}",
                    f"{test_results.get('failed', '?')} test(s) still failing",
                    f"retry (iteration {iteration + 1}/{self.max_iterations})")

        # --- Iteration limit reached: hand off ---
        failure_summary = ""
        if analysis:
            failure_summary = analysis.get("summary", "")
        elif test_results:
            failure_summary = test_results.get("stdout", "")[:300]

        handoff_msg = (
            f"Iteration limit reached ({self.max_iterations}). "
            f"Tests still failing after {iteration} attempt(s). "
            f"Human review required.\n\n"
            f"Last failure summary: {failure_summary}"
        )
        _record(trace, "DECIDE", None, f"iteration {iteration}",
                "iteration limit reached", "hand off to human")

        # Still draft the issue even on hand-off — gives the human a starting point
        draft = self._draft(goal, analysis, iteration, trace)

        result = self._build_result(
            run_id=run_id, goal=goal, status="handoff",
            iterations=iteration, test_results=test_results,
            analysis=analysis, draft=draft, handoff_msg=handoff_msg, trace=trace,
        )
        self._save_trace(run_id, goal, trace, started)
        return result

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _validate_goal(self, goal: str) -> Optional[str]:
        """Return an error string if *goal* is invalid, else None."""
        if not goal or not goal.strip():
            return "Goal must not be empty."
        if len(goal) > _MAX_GOAL_CHARS:
            return (
                f"Goal exceeds maximum length ({len(goal)} > {_MAX_GOAL_CHARS} chars). "
                "Please shorten the goal description."
            )
        lower = goal.lower()
        for pattern in _INJECTION_PATTERNS:
            if pattern in lower:
                return (
                    f"Goal rejected: possible prompt injection detected "
                    f"(matched pattern: '{pattern}'). "
                    "Please rephrase your goal."
                )
        return None

    def _sense(self, goal: str, trace: list) -> list:
        """SENSE — retrieve relevant RAG context for *goal*."""
        try:
            chunks = rag.retrieve(goal, top_k=5)
        except Exception as exc:
            logger.warning("RAG retrieval failed in SENSE: %s", exc)
            chunks = []

        _record(trace, "SENSE", "rag.retrieve", goal[:80],
                f"{len(chunks)} chunk(s) retrieved",
                "context ready for planning")
        return chunks

    def _plan(self, goal: str, context_chunks: list, iteration: int,
              trace: list) -> dict:
        """PLAN — propose test cases using RAG context."""
        plan = call_tool("propose_tests", {"issue_description": goal})

        # Output schema validation
        schema_error = _validate_tool_output("propose_tests", plan)
        if schema_error:
            logger.error("Schema validation failed for propose_tests: %s", schema_error)
            plan["error"] = schema_error

        test_cases = plan.get("test_cases", [])
        has_error   = "error" in plan

        decision = (
            f"{len(test_cases)} test case(s) proposed"
            if not has_error
            else f"planning error: {plan['error'][:80]}"
        )

        # Store test case names + code in the trace so they are always retrievable
        test_case_summary = [
            {"name": tc.get("name", ""), "type": tc.get("type", ""),
             "description": tc.get("description", ""), "code": tc.get("code", "")}
            for tc in test_cases
        ] if test_cases else []

        _record(trace, "PLAN", "propose_tests", goal[:80],
                f"test_cases={len(test_cases)}", decision,
                extra={"test_cases": test_case_summary})
        return plan

    def _act(self, test_cases: list, iteration: int,
             trace: list) -> tuple[Optional[dict], Optional[str]]:
        """ACT — run all generated test cases; return (results, docker_error)."""
        # Concatenate all test case code into one module
        combined_code = "\n\n".join(
            tc.get("code", "") for tc in test_cases if tc.get("code")
        )
        if not combined_code.strip():
            _record(trace, "ACT", "run_tests", "combined test code",
                    "no executable code found", "skip run")
            return None, None

        results = call_tool("run_tests", {"test_code": combined_code})

        # Output schema validation
        schema_error = _validate_tool_output("run_tests", results)
        if schema_error:
            logger.error("Schema validation failed for run_tests: %s", schema_error)
            return {"status": "error", "passed": 0, "failed": 0,
                    "stdout": "", "stderr": schema_error, "exit_code": -1}, schema_error

        # Docker-unavailable is an unrecoverable error — signal it separately
        if results.get("status") == "error" and "Docker is unavailable" in results.get("stderr", ""):
            _record(trace, "ACT", "run_tests",
                    f"{len(test_cases)} test case(s)",
                    results["stderr"][:120], "unrecoverable — hand off")
            return results, results["stderr"]

        _record(trace, "ACT", "run_tests",
                f"{len(test_cases)} test case(s)",
                f"status={results.get('status')} passed={results.get('passed')} "
                f"failed={results.get('failed')}",
                "observe failures" if results.get("status") != "passed" else "all passed")
        return results, None

    def _observe(self, test_results: Optional[dict], iteration: int,
                 trace: list) -> dict:
        """OBSERVE — analyse failures with Gemini."""
        if not test_results:
            return {}

        # Short-circuit obvious syntax/import errors without LLM
        obvious = _detect_obvious_error(test_results)
        if obvious is not None:
            _record(trace, "OBSERVE", "analyze_failures (skipped — obvious error)",
                    f"stdout={len(test_results.get('stdout', ''))} chars",
                    f"root_cause={obvious['root_cause'][:80]}",
                    "skip LLM, root cause recorded")
            return obvious

        analysis = call_tool("analyze_failures", {"test_results": test_results})

        # Output schema validation
        schema_error = _validate_tool_output("analyze_failures", analysis)
        if schema_error:
            logger.error("Schema validation failed for analyze_failures: %s", schema_error)
            analysis["error"] = schema_error

        _record(trace, "OBSERVE", "analyze_failures",
                f"stdout={len(test_results.get('stdout', ''))} chars",
                f"confidence={analysis.get('confidence', '?')} "
                f"root_cause={analysis.get('root_cause', '')[:80]}",
                "decide next action")
        return analysis

    def _draft(self, goal: str, analysis: Optional[dict], iteration: int,
               trace: list) -> dict:
        """DECIDE/draft — create a GitHub issue draft (not posted)."""
        params: dict = {
            "analysis":  analysis or {},
            "approved":  False,
            "goal":      goal,
        }
        draft = call_tool("draft_issue", params)

        # Output schema validation
        schema_error = _validate_tool_output("draft_issue", draft)
        if schema_error:
            logger.error("Schema validation failed for draft_issue: %s", schema_error)
            draft["error"] = schema_error

        _record(trace, "DECIDE", "draft_issue", "analysis + goal",
                f"title={draft.get('title', '')[:60]}",
                "draft ready for human review")
        return draft

    # ------------------------------------------------------------------
    # Trace helpers
    # ------------------------------------------------------------------

    def _save_trace(self, run_id: str, goal: str,
                    trace: list, started: str) -> Optional[str]:
        """Write the full execution trace to evidence/traces/<run_id>.json."""
        _TRACES_DIR.mkdir(parents=True, exist_ok=True)
        trace_path = _TRACES_DIR / f"{run_id}.json"
        payload = {
            "run_id":    run_id,
            "goal":      goal,
            "started":   started,
            "completed": _now(),
            "steps":     trace,
        }
        try:
            trace_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Trace saved to %s", trace_path,
                        extra={"event": "trace_saved", "run_id": run_id,
                               "path": str(trace_path)})
            return str(trace_path)
        except OSError as exc:
            logger.error("Failed to save trace: %s", exc)
            return None

    @staticmethod
    def _build_result(
        run_id: str, goal: str, status: str, iterations: int,
        test_results: Optional[dict], analysis: Optional[dict],
        draft: Optional[dict], handoff_msg: Optional[str], trace: list,
        pass_summary: Optional[str] = None,
    ) -> dict:
        return {
            "run_id":       run_id,
            "goal":         goal,
            "status":       status,
            "iterations":   iterations,
            "test_results": test_results,
            "analysis":     analysis,
            "draft":        draft,
            "handoff_msg":  handoff_msg,
            "trace":        trace,
            "pass_summary": pass_summary,
        }


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _validate_goal(goal: str) -> Optional[str]:
    """Module-level alias for QAAgent._validate_goal — used by tests."""
    return QAAgent()._validate_goal(goal)


# ---------------------------------------------------------------------------
# Output schema validation
# ---------------------------------------------------------------------------

_TOOL_OUTPUT_SCHEMAS: dict[str, set[str]] = {
    "propose_tests":    {"goal", "context_chunks", "test_cases"},
    "run_tests":        {"status", "passed", "failed", "stdout", "stderr", "exit_code"},
    "analyze_failures": {"summary", "root_cause", "affected_files", "suggested_fix", "confidence"},
    "draft_issue":      {"title", "body", "labels", "github_url"},
}


def _validate_tool_output(tool_name: str, output: dict) -> Optional[str]:
    """Return an error string if *output* is missing required keys, else None.

    Only validates when the output does not already contain an 'error' key
    (error dicts are intentionally incomplete).
    """
    if "error" in output:
        return None   # error dicts are valid — missing keys are expected
    required = _TOOL_OUTPUT_SCHEMAS.get(tool_name, set())
    missing = required - output.keys()
    if missing:
        return (
            f"Tool '{tool_name}' output missing required keys: {sorted(missing)}. "
            f"Got: {sorted(output.keys())}"
        )
    return None


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _record(trace: list, step: str, tool: Optional[str],
            input_summary: str, output_summary: str, decision: str,
            extra: dict | None = None) -> None:
    """Append a step record to *trace*."""
    entry = {
        "step":           step,
        "tool":           tool,
        "input_summary":  input_summary,
        "output_summary": output_summary,
        "decision":       decision,
        "timestamp_utc":  _now(),
    }
    if extra:
        entry.update(extra)
    trace.append(entry)


# ---------------------------------------------------------------------------
# Obvious-error detection and pass-summary helpers
# ---------------------------------------------------------------------------

_OBVIOUS_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # (substring to match, human label, suggested fix)
    ("SyntaxError", "syntax error",
     "Fix the syntax error reported in the test code."),
    ("ModuleNotFoundError", "import error",
     "Ensure the imported module is available; use unittest.mock "
     "for external dependencies."),
    ("ImportError", "import error",
     "Ensure the imported module is available; use unittest.mock "
     "for external dependencies."),
]


def _extract_error_line(text: str, pattern: str) -> str:
    """Return the first line containing *pattern*, truncated to 200 chars."""
    for line in text.splitlines():
        if pattern in line:
            return line[:200]
    return pattern  # fallback: return the pattern itself


def _detect_obvious_error(test_results: Optional[dict]) -> Optional[dict]:
    """Return a minimal analysis dict for obvious syntax/import failures.

    Returns None when:
      - test_results is None/empty, or
      - the run did not fail (status != "failed"), or
      - no obvious-error pattern is present (i.e. assertion/runtime errors).

    The returned dict is schema-compatible with analyze_failures output.
    """
    if not test_results:
        return None
    if test_results.get("status") != "failed":
        return None

    combined = (
        test_results.get("stdout", "") + "\n" + test_results.get("stderr", "")
    )
    for pattern, label, suggested_fix in _OBVIOUS_ERROR_PATTERNS:
        if pattern in combined:
            return {
                "summary": f"Test failure caused by a {label}.",
                "root_cause": _extract_error_line(combined, pattern),
                "affected_files": [],
                "suggested_fix": suggested_fix,
                "confidence": "high",
                "skipped_llm": True,
            }
    return None


def _build_pass_summary(test_results: Optional[dict], iteration: int) -> str:
    """Return a one-line summary note for an all-pass run.

    Example: 'All 3 test(s) passed in 1 iteration(s).'
    """
    passed = 0
    if test_results:
        passed = test_results.get("passed", 0) or 0
    return f"All {passed} test(s) passed in {iteration} iteration(s)."
