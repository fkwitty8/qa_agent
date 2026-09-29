# Week 4 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 25th September 2026

---

## Work Completed Against Weekly Objectives

### Tool Definitions and Allow-List

`src/tools.py` was implemented with a complete **TOOL_REGISTRY** — a dict keyed by tool name where each entry carries the full schema: `name`, `description`, `input_schema`, `output_schema`, `permissions`, and `failure_behaviour`. The registry serves as both documentation and the source of truth for the allow-list.

The single entry point `call_tool(name, params)` enforces the allow-list before any parameter validation or execution: calls to any name not in the registry raise `ToolNotAllowedError` immediately. Required parameters are then validated against the input schema before the tool function is dispatched. This means authorization and parameter errors are surfaced at the boundary, not deep inside tool logic.

Four tools are registered:

| Tool | Status |
|---|---|
| `propose_tests` | ✅ Implemented |
| `run_tests` | ✅ Implemented |
| `analyze_failures` | 🔲 Stub (Task 5) |
| `draft_issue` | 🔲 Stub (Task 5) |

### Tool 1: propose_tests

Retrieves the top-5 RAG context chunks for the goal description, fills the `test_planner` prompt template, calls Gemini, and parses the JSON response into a structured test plan. A `_strip_fences()` helper defensively removes markdown code fences before `json.loads()` as a backstop against model formatting drift.

All failures (RAG down, LLM error, non-JSON output) return a dict with an `"error"` key — the tool never raises to the caller, so the agent loop can inspect the result and decide how to handle it.

### Tool 2: run_tests

Executes generated test code through a four-stage safety pipeline:

1. **Syntax gate** — `ast.parse()` validates the code before any Docker call. Invalid code never reaches the sandbox.
2. **Temp file** — test code is written to a named temp file, mounted read-only inside the container.
3. **Docker sandbox** — a `python:3.11-slim` container installs `pytest` and runs the test file with a configurable timeout.
4. **Cleanup** — `container.remove(force=True)` and temp file deletion run in a `try/finally` block, guaranteeing cleanup even on timeout or API error.

`_parse_pytest_counts()` extracts integer pass/fail counts from the pytest summary line so the agent loop can make decisions numerically rather than by string-parsing the full output.

### Failure and Authorization Test Evidence

19 unit tests in `tests/test_tools.py` cover all four required failure scenarios:

| Scenario | Test | Result |
|---|---|---|
| Missing required parameter | `test_call_tool_rejects_missing_required_param` | ✅ PASS |
| Syntax-invalid test code | `test_run_tests_syntax_error_returns_error` | ✅ PASS |
| Docker unavailable | `test_run_tests_docker_unavailable_returns_error` | ✅ PASS |
| Timeout exceeded | `test_run_tests_timeout_returns_error` | ✅ PASS |
| Unlisted tool name (authorization) | `test_call_tool_rejects_unknown_tool` | ✅ PASS |

Full test run: **51/51 passing** across all four test files.

### Updated Architecture Diagram

The RAG pipeline architecture diagram was added to `docs/architecture/architectural_diagrams.md`, showing both the index-build and retrieval paths with chunking strategies, embedding, ChromaDB storage, and the empty-collection guard.

### RF-01 Fix Applied

The Week 3 report identified that `IMPLEMENTATION_PLAN.md`, `capstone.md`, and `README.md` were outside all crawl directories and therefore missing from the RAG index. This was fixed in `src/rag.py` by adding a non-recursive root-level entry to `_CRAWL_DIRS`:

```python
(_PROJECT_ROOT, [".md"], False),  # root .md only — non-recursive
```

The `recursive` flag (third tuple element) was added to the `_CRAWL_DIRS` schema to allow per-directory control. All existing test fixtures were updated to the new 3-tuple format.

---

## Key Engineering Decisions

**Allow-list enforced at the call boundary, not inside each tool.** Centralising the check in `call_tool()` means adding a new tool is safe by default — it must be explicitly registered to be callable. There is no way to call a tool function directly through the public interface without passing through the allow-list.

**Tools return error dicts, never raise.** The agent loop receives a uniform dict from every tool call. It can check for an `"error"` key or inspect `status` without wrapping every call in a try/except. This keeps the loop logic clean and avoids silent failures from uncaught exceptions.

**Human approval gate pre-registered for `draft_issue`.** The stub for `draft_issue` already has `approved=False` as the default in both the schema and the function signature. This means even before the tool is fully implemented, the contract is clear: nothing posts to GitHub unless the caller explicitly sets `approved=True`. The gate is part of the tool's design, not an afterthought.

**Non-recursive root crawl for RF-01 fix.** Using `directory.glob(pattern)` instead of `directory.rglob(pattern)` for the root entry ensures only top-level `.md` files are picked up, avoiding double-indexing of `docs/`, `src/`, and other subdirectories that are already covered by their own entries.

---

## Failures / Challenges and Current Response

**`analyze_failures` and `draft_issue` are stubs.** Both are registered in the allow-list and return a clear `"not implemented"` error dict. The agent loop will not crash when it calls them — it will receive an error it can log and report. Full implementations are scheduled for Task 5 (Week 5).

**Docker image must be pre-pulled.** `run_tests` requires `python:3.11-slim` to be available locally. On a machine where the image has not been pulled, the tool returns `status='error'` with a helpful message (`"Docker image not found. Run: docker pull python:3.11-slim"`). This is documented in `docs/architecture/tool_catalogue.md`.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 4 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed tool schemas against user stories and the AI Boundary Matrix; verified that `draft_issue` human approval gate satisfies the "no auto-submit" boundary constraint; updated ClickUp with Week 4 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/tools.py` (`call_tool`, `TOOL_REGISTRY`, `propose_tests`, `run_tests`, stubs); applied RF-01 fix to `src/rag.py` (3-tuple `_CRAWL_DIRS`, non-recursive root crawl). |
| AI Engineering Lead | Brenda | Authored `docs/architecture/tool_catalogue.md` with full schemas, permission notes, step-by-step flows, and error taxonomy; added RAG architecture diagram to `docs/architecture/architectural_diagrams.md`. |
| Quality/Security Lead | Dorcus | Wrote `tests/test_tools.py` (19 test cases, all 4 failure scenarios); updated `tests/test_rag.py` fixtures to the new 3-tuple `_CRAWL_DIRS` format; verified 51/51 tests passing; authored this progress report. |

---

## Plan for Next Week (Week 5 — Tools: Failure Analyzer & Issue Drafter)

- Implement `analyze_failures(test_results)` in `src/tools.py`: call Gemini with `failure_analyzer.txt`, return structured JSON `{summary, root_cause, affected_files, suggested_fix, confidence}`.
- Implement `draft_issue(analysis, approved)` in `src/tools.py`: construct GitHub issue payload; post via PyGithub only when `approved=True`.
- Add `prompts/issue_drafter.txt` v1.0 for formatting the issue body markdown.
- Write unit tests mocking PyGithub: assert no post on `approved=False`, assert auth errors surface cleanly, assert issue body is well-formed markdown.
- Update `docs/architecture/tool_catalogue.md` to mark both tools as fully implemented.
- Demo target: run a failing test, get an analysis summary, see the draft issue printed to console, call with `approved=True`, verify it appears in the GitHub repo.
