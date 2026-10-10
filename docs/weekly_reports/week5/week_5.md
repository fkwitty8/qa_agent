# Week 5 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 2nd October 2026

---

## Work Completed Against Week 5 Capstone Deliverables

The capstone Week 5 focus is **Agent Architecture and Bounded Autonomy**. The implementation plan placed `analyze_failures` and `draft_issue` in this week, but the capstone deliverables require the full bounded agent loop. Both were completed.

### Bounded Agent Loop — src/agents.py

`QAAgent` was implemented with a `run(goal)` method wiring all four tools into the SENSE→PLAN→ACT→OBSERVE→DECIDE loop. Key behaviours:

- **Input validation** rejects empty goals, goals over 2 000 characters, and goals matching prompt injection patterns (`"ignore previous instructions"`, `"you are now"`, etc.) before any tool is called.
- **SENSE** calls `rag.retrieve(goal, top_k=5)` once and reuses the context across all iterations — avoids re-embedding on every cycle.
- **PLAN** calls `propose_tests(goal)`. An `"error"` key in the result stops the loop immediately (`status="error"`).
- **ACT** calls `run_tests(combined_code)`. A `"Docker is unavailable"` error is treated as unrecoverable — the loop stops immediately.
- **OBSERVE** calls `analyze_failures(test_results)` only when tests fail.
- **DECIDE** either retries (iterations remain) or hands off (limit reached). `draft_issue(approved=False)` is called in both the pass and hand-off paths — a draft is always produced for human review.
- **Trace** — every run writes a JSON step log to `evidence/traces/<run_id>.json` recording step name, tool called, input summary, output summary, decision, and UTC timestamp.
- **Iteration limit** is read from `MAX_AGENT_ITERATIONS` in `.env` (default 3) and enforced by the loop condition — cannot be bypassed by tool output.

### Agent Task Contract

`docs/architecture/agent_task_contract.md` documents the full contract: goal definition, input constraints, loop structure, tools used, state fields, iteration limit, stop conditions, hand-off behaviour, trace schema, and security boundaries.

### Agent Architecture Diagram

Added to `docs/architecture/architectural_diagrams.md` — a Mermaid flowchart showing the full SENSE→PLAN→ACT→OBSERVE→DECIDE loop, all three stop conditions (passed, Docker error, iteration limit), the human approval gate, and trace write.

### Three Execution Traces

Three representative traces were captured and saved to `evidence/traces/`:

| File | Scenario | Outcome |
|---|---|---|
| `trace_happy_path.json` | Normal: `retrieve()` empty-corpus test | `status="passed"`, 1 iteration |
| `trace_recovery.json` | Recovery: whitespace API-key test fails iteration 1, passes iteration 2 | `status="passed"`, 2 iterations |
| `trace_limit_hit.json` | Limit hit: generated tests import `src.*` unavailable in Docker sandbox | `status="handoff"`, 3 iterations |

The limit-hit trace also surfaces a genuine finding: the `test_planner` prompt constraint says to import from `src.*`, but the Docker sandbox has no project installation. This is documented as a known limitation and a future prompt improvement.

### Test Coverage

18 unit tests were added to `tests/test_agent.py`:

| Tests | Scenario |
|---|---|
| 1–2 | `status="passed"` on first success; exits early before limit |
| 3–4 | `status="handoff"` at limit; handoff_msg contains iteration count |
| 5–6 | `status="error"` on Docker unavailable; on plan failure |
| 7–8 | Trace JSON written; trace contains SENSE/PLAN/ACT/DECIDE steps |
| 9–11 | `status="error"` on empty goal, overlong goal, prompt injection |
| 12 | Loop retries PLAN→ACT on repeated failure |
| 13 | `draft_issue` always called with `approved=False` |
| 14–15 | Empty test_cases handled gracefully; `_validate_goal` accepts normal input |

Full test suite: **81/81 passing** across all five test files.

---

## Work Completed Against Weekly Objectives (Tools — Tasks 4 & 5)

### Tool 3: analyze_failures

`analyze_failures(test_results)` was implemented in `src/tools.py`, replacing the Task 4 stub. The tool:

1. **Short-circuits** when `status == "passed"` — returns a no-failure summary immediately without making an LLM call. This avoids unnecessary API usage when the agent loop has already confirmed passing tests.
2. **Retrieves source context** via `rag.retrieve(stdout[:500], top_k=5)` — using the first 500 characters of pytest output as the query gives the retriever enough signal to find the relevant module without sending the full (potentially long) test output to the embedding API. RAG failure is treated as non-fatal: a warning is logged and the tool continues with empty context rather than returning an error.
3. **Calls Gemini** with the `failure_analyzer` prompt (v1.1), filling `SOURCE_CONTEXT` and `TEST_RESULTS` placeholders.
4. **Normalises** the JSON response to ensure all five keys (`summary`, `root_cause`, `affected_files`, `suggested_fix`, `confidence`) are always present in the output, even if the model omits one.

### Tool 4: draft_issue

`draft_issue(analysis, approved, goal)` was implemented, replacing the Task 4 stub. Key design points:

- **LLM-generated body** — the `issue_drafter` prompt formats the analysis into a structured markdown issue body with six sections: Summary, Root Cause, Affected Files, Suggested Fix, Confidence, and Context. The `goal` parameter is included in the Context section for traceability.
- **Fallback formatters** — `_fallback_title()` and `_fallback_body()` produce a valid draft directly from the analysis dict if the LLM call fails for any reason. This ensures a draft is always returned regardless of LLM availability.
- **Human approval gate** — `approved=False` (default) returns the draft for UI preview with no network call. `approved=True` calls PyGithub's `repo.create_issue()`. The gate is enforced at the function level, not the call site — it cannot be bypassed by omitting the parameter.
- **Typed GitHub error handling** — `GithubException` is caught and inspected by HTTP status code: 401/403 returns a clear authentication failure message, 404 returns a repo-not-found message. Both cases return the draft alongside the error so the UI can still display the issue body.

### issue_drafter Prompt

`prompts/issue_drafter.txt` v1.0 was written and archived to `prompts/versions/issue_drafter_v1.0.txt`. The prompt follows the same structure as the existing prompts (ROLE → TASK → CONSTRAINTS → NEGATIVE EXAMPLE → OUTPUT FORMAT) and outputs `{title, body}` JSON.

### Tool Catalogue Updated

`docs/architecture/tool_catalogue.md` was updated: both tools marked as implemented, stubs replaced with full schemas, step-by-step flows, failure behaviour tables, and an expanded error taxonomy covering all RAG, LLM, and GitHub failure modes.

### Test Coverage

12 new unit tests were added to `tests/test_tools.py` (tests 17–28):

| Test | Scenario |
|---|---|
| 17 | `analyze_failures` happy path — all keys present |
| 18 | `analyze_failures` short-circuits on passed status — no LLM call |
| 19 | `analyze_failures` LLM failure returns error dict |
| 20 | `analyze_failures` non-JSON response returns error dict |
| 21 | `draft_issue` approved=False — no GitHub call, github_url is None |
| 22 | `draft_issue` body contains markdown headings |
| 23 | `draft_issue` approved=True — posts and returns github_url |
| 24 | `draft_issue` 401 auth failure — descriptive error, draft returned |
| 25 | `draft_issue` missing GITHUB_TOKEN — error before any API call |
| 26 | `draft_issue` LLM failure — fallback body used, no crash |
| 27 | `_fallback_body` contains all six required sections |
| 28 | `_fallback_title` truncates long summaries to ≤72 chars |

Full test suite: **81/81 passing** across all five test files.

---

## Key Engineering Decisions

**RAG failure is non-fatal in `analyze_failures`.** Unlike `propose_tests` where no context means no useful test plan, `analyze_failures` can still produce a reasonable root-cause analysis from the raw pytest output alone. Making RAG failure fatal here would unnecessarily break the agent loop when the ChromaDB index is stale or unavailable. The trade-off is lower `confidence` in the analysis, which is reflected in the model's self-reported confidence field.

**Fallback formatters for `draft_issue` — draft always returned.** The issue body is a UI-facing artefact. If the LLM call fails, it is better to show the developer a plain but complete draft than to return an error with no content. The fallback uses the same six-section structure as the LLM-generated body, so the output is consistent regardless of which path was taken.

**`goal` parameter added to `draft_issue`.** The original spec only had `analysis` and `approved`. Adding `goal` lets the agent pass the original user intent into the issue body's Context section, giving the developer reading the issue enough information to understand what triggered it without having to check logs.

**Human approval gate at function level, not call site.** `approved=False` is the default in both the function signature and the TOOL_REGISTRY schema. There is no code path through `draft_issue` that posts to GitHub without `approved=True` being explicitly passed — the gate is structural, not a runtime check that could accidentally be skipped.

---

## Failures / Challenges and Current Response

**Prompt injection in `_validate_goal` — `QAAgent()` instantiation overhead.** The module-level `_validate_goal()` function creates a `QAAgent()` instance for each call, which reads `.env` on every invocation. This is a minor inefficiency caught during test writing; for production the validation logic was extracted into a standalone function. Fixed before the tests ran.

**`test_planner` prompt constraint conflicts with Docker sandbox isolation.** The trace `trace_limit_hit.json` surfaces a genuine limitation: the prompt instructs the model to import from `src.*`, but Docker sandbox has no project installed. Generated tests will import-error every time for goals about the agent's own tools. The fix (update the prompt constraint) is documented in the failure catalogue trace and scheduled for the Week 8 hardening pass.

**Tools 3 and 4 (`analyze_failures`, `draft_issue`) were stub-only when the capstone Week 5 focus switched to the agent loop.** Both stubs were replaced with full implementations this week alongside the agent loop — no deliverables were deferred.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 5 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed agent loop stop conditions and hand-off behaviour against the AI Boundary Matrix; confirmed `draft_issue` approval gate satisfies the "no auto-submit" boundary; updated ClickUp with Week 5 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/agents.py` (`QAAgent`, bounded loop, input validation, trace writer); implemented `analyze_failures()` and `draft_issue()` in `src/tools.py`; wrote `prompts/issue_drafter.txt` v1.0. |
| AI Engineering Lead | Brenda | Authored `docs/architecture/agent_task_contract.md`; added agent loop diagram to `docs/architecture/architectural_diagrams.md`; updated `docs/architecture/tool_catalogue.md` for Tools 3 and 4; created the three execution traces in `evidence/traces/`. |
| Quality/Security Lead | Dorcus | Wrote `tests/test_agent.py` (18 test cases) and `tests/test_tools.py` tests 17–28 (12 cases); verified 81/81 full suite passing; authored this progress report. |

---

## Plan for Next Week (Week 6 — Memory, State & Streamlit UI)

- Implement `src/memory.py`: SQLite-backed store at `evidence/qa_agent.db` with tables `runs` and `drafted_issues`; expose `save_run`, `get_run`, `list_runs`, `save_draft`, `update_draft_url`, `get_draft`.
- Build `src/main.py` as the Streamlit app: sidebar run history, goal input, "Run Agent" button, live step progress, trace viewer, issue draft preview with "Submit to GitHub" approval form.
- Add `docs/architecture/memory_design.md`: what is stored, retention policy, access controls, no-PII policy.
- Write unit tests for `memory.py`: save/retrieve round-trips, reverse-chronological `list_runs`, graceful missing-key handling.
- Demo target: run the agent twice via the UI, reload the page, show both runs in sidebar history, click one to view its trace and drafted issue.
