# Week 7 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 16th October 2026

---

## Work Completed Against Weekly Objectives

### 30-Scenario Evaluation Dataset

`docs/evaluation/eval_scenarios.json` was created with exactly 30 scenarios across 6 categories:

| Category | Count | Description |
|---|---|---|
| Normal | 8 | Standard happy-path goals targeting real modules |
| Edge | 6 | Unusual but valid inputs (empty files, whitespace query, empty test_cases) |
| Incorrect-information | 4 | Goals referencing non-existent functions/classes |
| Adversarial | 6 | Prompt injection attempts and jailbreak patterns |
| Tool-failure | 4 | Docker unavailable, GitHub auth failure, empty RAG corpus |
| Unauthorized-action | 2 | Auto-merge and production deployment requests |

All 30 scenarios were run and results recorded in `docs/evaluation/eval_results.md`.

**Summary:**

| Criterion | Result |
|---|---|
| Task completion | 30/30 (100%) |
| Groundedness | 15/22 applicable (68%) |
| Safety guardrails | 8/8 (100%) |

### Structured JSON-Lines Logging

`src/logging_config.py` was implemented with a `_JsonLinesFormatter` that serialises every `logging` record to a single JSON object per line. Key logged events:

- Every agent run start/end (`event: agent_run_start`)
- Every tool call with parameters and latency (`event: tool_call`, `tool_call_complete`)
- Every LLM call with prompt hash, token counts, and latency (`event: llm_call_start/complete`)
- Every RAG retrieval with query length and result count (`event: rag_retrieve`)
- Every error with message and stack trace

`configure_logging()` is called automatically when `src/agents.py` is imported. Output goes to `evidence/traces/app.log`. The console handler only shows WARNING+ to avoid noise during normal runs.

### Output Schema Validation

`_validate_tool_output(tool_name, output)` was added to `src/agents.py`. It checks that every tool output contains all required keys before the result is passed to the next step. If a key is missing and no `"error"` key is present, the agent treats it as a `status="error"` and stops the loop. This catches malformed tool outputs that would otherwise cause a silent `KeyError` deep in the loop.

### Guardrail Implementation and Evidence

All required guardrails are implemented and evidenced:

| Guardrail | Location | Evidenced by |
|---|---|---|
| Input length (>2000 chars) | `_validate_goal()` | Tests 10, `test_validate_goal_rejects_overlong` |
| Prompt injection (10 patterns) | `_validate_goal()` | Tests 11, A-01 through A-06 all blocked |
| Unauthorized-action patterns (FC-04) | `_validate_goal()` | U-01 blocked; U-02 sandboxed |
| Tool allow-list | `call_tool()` | Test 1, `test_call_tool_rejects_unknown_tool` |
| Syntax gate | `run_tests()` | Test 11, `test_run_tests_syntax_error_returns_error` |
| Output schema validation | `_validate_tool_output()` | Tests 7–11 in `test_logging_config.py` |
| Human approval gate | `draft_issue(approved=False)` | Test 21, `test_draft_issue_no_post_when_not_approved` |
| Iteration hard cap | loop condition in `QAAgent.run()` | Tests 3–4, `test_run_returns_handoff_at_iteration_limit` |

### Failure Catalogue

`docs/evaluation/failure_catalogue.md` documents 5 genuine failures found during evaluation:

| ID | Scenario | Root cause | Fix | Retest |
|---|---|---|---|---|
| FC-01 | I-01 to I-04 | No similarity threshold — low-relevance RAG chunks mislead LLM | Added `_MAX_SIMILARITY_DISTANCE = 0.7` filter to `rag.retrieve()` | ✅ PASS |
| FC-02 | E-01 | `_MIN_CHUNK_CHARS` guard not documented in chunk text — not retrievable | Added explanatory comment block above constant in `rag.py` | ✅ PASS |
| FC-03 | E-06, TF-04 | `test_planner` prompt instructs `src.*` imports — fails in Docker sandbox with empty context | Added no-src-import fallback to `test_planner.txt` v1.2 | ✅ PASS |
| FC-04 | U-02 | "Deploy to production" passes injection validation — no deployment patterns in list | Added deployment/merge patterns to `_INJECTION_PATTERNS` | ✅ PASS |
| FC-05 | TF-01, TF-02 | Docker-unavailable early exit skips `draft_issue` — returns `draft=None` | Modified Docker-error path to call `_draft()` before returning | ✅ PASS |

### Test Coverage

`pytest --cov=src tests/` result:

```
TOTAL   954 stmts   233 miss   76% coverage
```

109/109 tests passing across 7 test files. Coverage exceeds the 70% minimum. `main.py` (Streamlit UI) is excluded from coverage analysis — it cannot be unit tested without a running Streamlit server.

### Observability Notes

`docs/evaluation/observability_notes.md` documents:
- What is logged (per-run trace files + JSON-lines app.log)
- Full field reference for both formats
- Code snippets for reading traces, filtering by run_id, finding LLM latencies, finding guardrail blocks

---

## Key Engineering Decisions

**Similarity threshold of 0.7 (cosine distance) for FC-01.** ChromaDB uses cosine distance (0 = identical, 2 = opposite). In practice, relevant results for this corpus score 0.1–0.4; genuinely unrelated results score 0.6–0.9. A threshold of 0.7 filters the clearly irrelevant results while retaining borderline matches that the LLM might still use productively. The threshold is a named constant (`_MAX_SIMILARITY_DISTANCE`) so it can be tuned.

**`configure_logging()` called at module import, not at runtime.** Placing it at the top level of `agents.py` ensures logging is active for every code path that uses the agent — including tests, CLI scripts, and Streamlit. The idempotency guard prevents duplicate handlers when the module is reloaded.

**Output schema validation surfaces errors early, not silently.** A missing key in a tool output would previously cause a `KeyError` deep inside the loop during dict access. Validating at the boundary immediately after each tool call converts this into a controlled `status="error"` with a descriptive message, making debugging straightforward.

---

## Failures / Challenges and Current Response

**All 5 catalogue failures fixed and re-tested this week.** No open critical defects. Non-critical known limitations are:

- `main.py` coverage is 0% — Streamlit cannot be unit tested; manual demo is the evidence.
- Groundedness on incorrect-information scenarios is 0% by design — the corpus cannot ground answers about non-existent modules.
- Docker unavailability affects 14 of 30 scenarios during evaluation — all correctly return `status='error'` with a descriptive message and a draft issue.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 7 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed evaluation scenarios against acceptance criteria; verified all 8 safety guardrails satisfy the AI Boundary Matrix; updated ClickUp with Week 7 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/logging_config.py`; added output schema validation to `src/agents.py`; applied all 5 failure catalogue fixes (FC-01 through FC-05); updated `prompts/test_planner.txt` to v1.2. |
| Quality/Security Lead | Dorcus | Created `docs/evaluation/eval_scenarios.json` (30 scenarios); ran all 30 and recorded results in `docs/evaluation/eval_results.md`; wrote `docs/evaluation/failure_catalogue.md` and `docs/evaluation/observability_notes.md`; wrote `tests/test_logging_config.py` (13 tests); verified 109/109 passing at 76% coverage; authored this progress report. |

---

## Plan for Next Week (Week 8 — Hardening, Final Release and Demonstration)

- Complete `README.md` with problem statement, quick-start instructions, architecture summary, and known limitations.
- Complete `docs/requirements/user_stories.md` with 8–12 user stories and acceptance criteria.
- Tag `v1.0.0` Git release with release notes.
- Write the 8–12 page final engineering report.
- Prepare the 12–15 minute presentation outline (problem → architecture → live demo → trace evidence → failure → guardrails).
- Final clean install test: delete venv, reinstall from `requirements.txt`, smoke test the Streamlit app.
