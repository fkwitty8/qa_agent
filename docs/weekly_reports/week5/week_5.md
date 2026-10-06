# Week 5 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 2nd October 2026

---

## Work Completed Against Weekly Objectives

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

Full test suite: **63/63 passing** across all four test files.

---

## Key Engineering Decisions

**RAG failure is non-fatal in `analyze_failures`.** Unlike `propose_tests` where no context means no useful test plan, `analyze_failures` can still produce a reasonable root-cause analysis from the raw pytest output alone. Making RAG failure fatal here would unnecessarily break the agent loop when the ChromaDB index is stale or unavailable. The trade-off is lower `confidence` in the analysis, which is reflected in the model's self-reported confidence field.

**Fallback formatters for `draft_issue` — draft always returned.** The issue body is a UI-facing artefact. If the LLM call fails, it is better to show the developer a plain but complete draft than to return an error with no content. The fallback uses the same six-section structure as the LLM-generated body, so the output is consistent regardless of which path was taken.

**`goal` parameter added to `draft_issue`.** The original spec only had `analysis` and `approved`. Adding `goal` lets the agent pass the original user intent into the issue body's Context section, giving the developer reading the issue enough information to understand what triggered it without having to check logs.

**Human approval gate at function level, not call site.** `approved=False` is the default in both the function signature and the TOOL_REGISTRY schema. There is no code path through `draft_issue` that posts to GitHub without `approved=True` being explicitly passed — the gate is structural, not a runtime check that could accidentally be skipped.

---

## Failures / Challenges and Current Response

**No failures this week.** All 12 new tests passed first time. The pattern established in Task 4 — tools return error dicts, never raise — made the test structure straightforward to follow consistently.

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
| Project/Requirements Lead | Jim | Reviewed `draft_issue` approval gate against the AI Boundary Matrix; confirmed the "no auto-submit" boundary constraint is structurally enforced; updated ClickUp with Week 5 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `analyze_failures()` and `draft_issue()` in `src/tools.py`; wrote `prompts/issue_drafter.txt` v1.0 and archived versioned copy; updated TOOL_REGISTRY schemas for both tools. |
| AI Engineering Lead | Brenda | Updated `docs/architecture/tool_catalogue.md` with full schemas, step-by-step flows, and expanded error taxonomy for Tools 3 and 4; verified prompt structure consistency with existing prompts. |
| Quality/Security Lead | Dorcus | Wrote `tests/test_tools.py` tests 17–28 (12 new cases); verified 63/63 full suite passing; authored this progress report. |

---

## Plan for Next Week (Week 6 — Bounded Agent Loop)

- Implement `src/agents.py`: `QAAgent` class with `run(goal)` method wiring all four tools into the SENSE→PLAN→ACT→OBSERVE→DECIDE loop.
- Read `MAX_AGENT_ITERATIONS` from `.env` (default 3); stop and return a hand-off message when limit is reached.
- Stop conditions: all tests pass, Docker unavailable (unrecoverable), iteration limit hit.
- Capture a full execution trace per run — each step name, tool called, input summary, output summary, decision, timestamp — saved as JSON to `evidence/traces/<timestamp>.json`.
- Write `docs/architecture/agent_task_contract.md`: goal definition, tools, state fields, iteration limit, stop conditions, hand-off behaviour.
- Write unit tests for `QAAgent` with all tools mocked: assert loop stops at `MAX_AGENT_ITERATIONS`, assert trace JSON is written, assert hand-off message returned on limit, assert early exit when all tests pass.
