# Memory Design and Data Handling Note

**Project:** Software Engineering QA Agent  
**Component:** `src/memory.py` — `MemoryStore`  
**Last updated:** 2026-10-09

---

## State Model

The agent has two distinct kinds of state:

### Ephemeral session state (in-process only)

Held in `QAAgent.run()` local variables and `st.session_state`. Lost when the process exits or the browser tab closes. Not persisted.

| Field | Type | Lifetime | Description |
|---|---|---|---|
| `run_id` | `str` | Single run | 12-char hex UUID |
| `goal` | `str` | Single run | Original user goal |
| `iteration` | `int` | Single run | Current loop count |
| `context_chunks` | `list[dict]` | Single run | RAG results from SENSE |
| `test_results` | `dict` | Single run | Last `run_tests` output |
| `analysis` | `dict` | Single run | Last `analyze_failures` output |
| `draft` | `dict` | Single run | `draft_issue` output |
| `trace` | `list[dict]` | Single run | Append-only step log |
| `st.session_state.current_run` | `dict` | Browser session | Last result for UI display |
| `st.session_state.page` | `str` | Browser session | Active UI page |

### Persistent memory (SQLite)

Held in `evidence/qa_agent.db`. Survives restarts and page reloads. Managed by `MemoryStore`.

---

## Database Schema

```sql
CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,   -- ISO 8601 UTC
    goal        TEXT NOT NULL,
    trace_json  TEXT NOT NULL,   -- JSON-serialised list[dict]
    status      TEXT NOT NULL    -- "passed" | "handoff" | "error" | "failed"
);

CREATE TABLE drafted_issues (
    issue_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,   -- markdown
    github_url   TEXT,            -- NULL until approved and posted
    submitted_at TEXT             -- ISO 8601 UTC, NULL until posted
);
```

---

## What Is Stored and Why

| Table | Field | Why stored |
|---|---|---|
| `runs` | `run_id` | Primary key for retrieval and cross-referencing |
| `runs` | `timestamp` | Enables reverse-chronological listing in sidebar |
| `runs` | `goal` | Shown in sidebar history and Past Runs table |
| `runs` | `trace_json` | Full step-by-step audit trail; loaded on demand for trace viewer |
| `runs` | `status` | Quick status icon in sidebar without deserialising the trace |
| `drafted_issues` | `title`, `body` | Persists draft across page reloads so user can review before approving |
| `drafted_issues` | `github_url` | Records the posted URL after human approval; null until then |
| `drafted_issues` | `submitted_at` | Audit timestamp for when the issue was posted |

---

## What Is NOT Stored

| Item | Reason |
|---|---|
| `test_results["stdout"]` (full Docker output) | Can be large; reconstructible from trace. Stored in trace_json instead. |
| `analysis` dict | Reconstructible from trace; not independently queried |
| GitHub token / API keys | Never stored anywhere; read from `.env` only |
| Personally identifiable information | No user accounts, no email, no names — agent-generated content only |
| Raw Gemini API responses | Only structured parsed output (test plans, analysis JSON) stored |

---

## Justified Memory Use Cases

**1. Run history sidebar (primary justification)**
The Streamlit sidebar shows the last 10 runs with status icons. Without persistent memory this list would reset on every page reload, making it impossible to return to a previous run. Memory enables: reload page → sidebar repopulates → click past run → trace and draft restore exactly.

**2. Draft preservation across reloads**
An issue draft may take 30+ seconds to generate (RAG + LLM). Storing it means the user can close the tab and return without losing the draft.

**3. Audit trail**
The JSON trace in `runs.trace_json` provides a permanent audit of every step the agent took, which tool was called, and what decision was made. This supports the Week 7 evaluation requirement for observability evidence.

Memory improves these tasks without silently controlling decisions — it only stores and retrieves, it never modifies goals, analysis outputs, or approval state.

---

## Access Controls

| Control | Detail |
|---|---|
| Local-only | `evidence/qa_agent.db` is a local SQLite file. No network exposure. |
| No authentication | Single-user local tool; no multi-user access control required. |
| File permissions | Inherits OS file permissions. Should be `600` (owner read/write only) on shared systems. |
| `.gitignore` | `evidence/qa_agent.db` must be added to `.gitignore` to prevent committing run history. |

---

## Retention Policy

| Action | Who can perform it | How |
|---|---|---|
| Delete a single run | User | `MemoryStore.delete_run(run_id)` — also deletes associated drafted_issues |
| Delete all runs | User | Delete `evidence/qa_agent.db` file directly |
| Auto-expiry | None | No automatic expiry. Retention is manual-delete only. |

There is no automatic retention limit. For long-running projects the database will grow proportionally to the number of agent runs. Each run stores at most a few kilobytes of JSON trace data, so even thousands of runs remain manageable.

---

## No-PII Policy

The QA Agent operates exclusively on its own codebase and documentation. It does not process user data, customer records, or any personally identifiable information. The goal strings entered by the user describe software testing objectives (e.g. "test that rag.retrieve() handles an empty corpus") — these are not PII. If a future extension of the system were to process user-provided content, this policy would need to be revisited.
