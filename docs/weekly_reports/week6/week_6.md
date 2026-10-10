# Week 6 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 9th October 2026

---

## Work Completed Against Weekly Objectives

### Persistent Memory — src/memory.py

`MemoryStore` was implemented as a SQLite-backed store at `evidence/qa_agent.db`. It exposes two logical domains:

**Run history:**
- `save_run(run_id, goal, trace, status)` — persists a completed agent run
- `get_run(run_id)` → `dict | None` — retrieves a single run with full trace
- `list_runs()` → `list[dict]` — all runs newest-first, trace omitted for performance
- `delete_run(run_id)` → `bool` — removes run and its associated drafted issues

**Drafted issues:**
- `save_draft(issue_id, run_id, title, body)` — persists a draft before approval
- `get_draft(issue_id)` → `dict | None` — retrieves a single draft
- `update_draft_url(issue_id, github_url)` → `bool` — records the URL after posting
- `get_drafts_for_run(run_id)` → `list[dict]` — all drafts for a run

The database is initialised with `CREATE TABLE IF NOT EXISTS` — safe to call on every startup. A `contextmanager`-based connection wrapper ensures transactions are committed or rolled back cleanly.

### Streamlit UI — src/main.py

The full Streamlit interface was implemented with two pages:

**New Run page:**
- Text area for the goal (max 2 000 chars, with placeholder)
- "Run Agent" button — disabled when goal is empty
- Live step progress display — each `_record()` call in `agents.py` is intercepted to stream step names as they complete
- Execution trace viewer — expandable per-step cards showing input, output, decision, and timestamp
- Issue draft preview with a "Submit to GitHub" approval form — posts only on explicit user click
- All run results are immediately persisted to `MemoryStore`

**Past Runs page:**
- Pandas DataFrame table of all runs (run_id, time, goal, status)
- Selectbox to load any past run back into the New Run view
- Trace and draft restore from memory — the page reload case is fully handled

**Sidebar:**
- Shows the last 10 runs with status icons (✅ ⚠️ ❌)
- Each run is a clickable button that reloads the run into the main panel
- `st.session_state` wires current run, selected run, and active page so widget interactions don't reset state

### State Model and Memory Design Note

`docs/architecture/memory_design.md` documents:
- The distinction between ephemeral session state (in-process) and persistent memory (SQLite)
- Full schema for both tables with field-level justification
- What is explicitly **not** stored (raw Docker output, analysis dict, API keys, PII)
- Justified memory use cases (sidebar history, draft preservation, audit trail)
- Access controls (local-only file, `.gitignore` requirement)
- Retention policy (manual delete only — no auto-expiry)
- No-PII statement

### MCP-Style Interface Specification

`docs/architecture/mcp_interface.md` documents all four tools as an MCP-style interface, covering:
- JSON input/output schemas with types, required fields, and constraints
- Permissions per tool
- Side effects (which tools write to disk or make network calls, and under what conditions)
- Security boundaries (sandboxing, token handling, approval gate)
- Uniform error handling convention

This satisfies the capstone Week 6 "Integration or MCP-style interface specification" deliverable using the GitHub integration that already exists as the external integration point, with the tool interface formally specified.

### Test Coverage

15 unit tests in `tests/test_memory.py`:

| Tests | Scenario |
|---|---|
| 1–2 | `save_run`/`get_run` round-trip; unknown run_id returns None |
| 3–5 | `list_runs` reverse-chronological order; empty list; trace omitted |
| 6 | `INSERT OR REPLACE` updates existing run |
| 7–8 | `save_draft`/`get_draft` round-trip; unknown issue_id returns None |
| 9–10 | `update_draft_url` sets url+timestamp; returns False for unknown ID |
| 11–12 | `get_drafts_for_run` returns all drafts; empty list case |
| 13–14 | `delete_run` removes run and drafts; returns False for unknown ID |
| 15 | `MemoryStore` creates the SQLite file and both tables on init |

Full test suite: **96/96 passing** across all six test files.

---

## Key Engineering Decisions

**`list_runs()` omits trace JSON for performance.** The trace for a single run can be several kilobytes of JSON. Deserialising every trace on every sidebar render would be wasteful. The list query returns only `run_id`, `timestamp`, `goal`, and `status` — the trace is loaded only when a specific run is selected via `get_run()`.

**Live step streaming via `_record()` monkey-patch.** The agent loop calls the module-level `_record()` function on every step. The Streamlit UI temporarily replaces this function with a wrapper that also updates a `st.empty()` placeholder — this gives live feedback during the run without requiring the agent to know anything about Streamlit. The original function is always restored in a `try/finally` block.

**Draft always persisted, github_url nullable.** Drafts are saved to memory immediately after generation, before the user sees the approval form. The `github_url` column is NULL until the user approves. This means a draft survives a page reload between generation and approval, and the database always reflects the true approval state.

**MCP interface spec documents the GitHub integration as the external integration.** The `draft_issue` tool already integrates with GitHub's API via PyGithub. Documenting it as an MCP-style interface satisfies the capstone requirement without building a separate integration for its own sake.

---

## Failures / Challenges and Current Response

**`evidence/qa_agent.db` must be gitignored.** Run history contains goal strings that could reveal project internals if the repository were public. Added a note to `memory_design.md` and the README (to be completed in Week 8). The `.env` already correctly gitignores secrets.

**Streamlit does not natively support streaming mid-run.** The `st.empty()` + monkey-patch pattern works reliably for the current single-threaded use case, but would not scale to concurrent users. This is an acceptable limitation for a single-developer QA tool and is documented as a known limitation.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 6 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed `memory_design.md` no-PII policy and retention rules; confirmed memory use cases are justified without silently controlling decisions; updated ClickUp with Week 6 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/memory.py` (SQLite store, all public methods); implemented `src/main.py` (Streamlit UI — both pages, sidebar, live streaming, approval form); wired memory into agent run flow. |
| AI Engineering Lead | Brenda | Authored `docs/architecture/mcp_interface.md` (MCP-style tool interface spec for all four tools); authored `docs/architecture/memory_design.md` (state model and data handling note). |
| Quality/Security Lead | Dorcus | Wrote `tests/test_memory.py` (15 test cases, 96/96 full suite passing); reviewed `.gitignore` status of `evidence/qa_agent.db`; authored this progress report. |

---

## Plan for Next Week (Week 7 — Evaluation, Observability and Guardrails)

- Write `scripts/generate_eval_scenarios.py` to auto-generate 30 evaluation scenarios across 6 categories (normal, edge, incorrect-information, adversarial, tool-failure, unauthorized-action).
- Store scenarios in `docs/evaluation/eval_scenarios.json`.
- Add JSON-lines structured logging to `evidence/traces/app.log` for every tool call, LLM call, agent decision, and error.
- Add output schema validation guardrails to `src/agents.py`.
- Create `docs/evaluation/observability_notes.md`.
- Run all 30 scenarios and record results in `docs/evaluation/eval_results.md`.
- Create `docs/evaluation/failure_catalogue.md` with at least 5 genuine failures.
- Achieve ≥70% line coverage with `pytest --cov`.
