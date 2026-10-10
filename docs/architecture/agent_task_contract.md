# Agent Task Contract

**Project:** Software Engineering QA Agent  
**Component:** `QAAgent` in `src/agents.py`  
**Last updated:** 2026-10-02

---

## Goal Definition

A **goal** is a natural-language string describing a module, function, or behaviour that the agent should generate and run tests for.

**Examples:**
- `"test that rag.retrieve() handles an empty corpus gracefully"`
- `"verify that llm_client.generate() raises EnvironmentError when GEMINI_API_KEY is missing"`
- `"test the propose_tests tool with a mocked Gemini response"`

**Constraints on goal input:**

| Constraint | Rule | Response on violation |
|---|---|---|
| Non-empty | Must contain at least one non-whitespace character | `status="error"` |
| Length | ≤ 2 000 characters | `status="error"` with length message |
| Prompt injection | Must not contain patterns: `"ignore previous instructions"`, `"you are now"`, `"disregard your"`, etc. | `status="error"` with injection-detected message |

---

## Loop Structure

```
┌─────────────────────────────────────────────────────────────────┐
│                         QAAgent.run(goal)                       │
│                                                                 │
│  Validate goal ──── invalid ──────────────────► status="error" │
│       │                                                         │
│  SENSE: rag.retrieve(goal, top_k=5)                             │
│       │                                                         │
│  ┌────▼──────────────────────────────────────────────────┐      │
│  │  iteration = 1 … MAX_AGENT_ITERATIONS                 │      │
│  │                                                       │      │
│  │  PLAN  → propose_tests(goal)                          │      │
│  │            ├─ error? ──────────────► status="error"  │      │
│  │            └─ no test cases? ──────► retry or break  │      │
│  │                                                       │      │
│  │  ACT   → run_tests(combined_code)                     │      │
│  │            └─ Docker unavailable? ► status="error"   │      │
│  │                                                       │      │
│  │  DECIDE: all passed? ─── yes ─────► draft + "passed" │      │
│  │                         no        ▼                   │      │
│  │  OBSERVE → analyze_failures(results)                  │      │
│  │                                                       │      │
│  │  DECIDE: iterations < MAX? ── yes ► loop back         │      │
│  │                              no  ► break              │      │
│  └───────────────────────────────────────────────────────┘      │
│       │                                                         │
│  DECIDE: hand off ──────────────────► draft + "handoff"         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tools Used

| Step | Tool | Purpose |
|---|---|---|
| SENSE | `rag.retrieve()` | Fetch relevant code/doc chunks as context |
| PLAN | `propose_tests` | Generate pytest test cases via Gemini + RAG context |
| ACT | `run_tests` | Execute test code in Docker sandbox |
| OBSERVE | `analyze_failures` | Root-cause analysis of failures via Gemini |
| DECIDE | `draft_issue` | Format failure analysis into a GitHub issue draft |

All tools are called through `call_tool()` which enforces the allow-list. No tool outside this set can be invoked by the agent.

---

## State Fields

The agent loop maintains the following state across iterations:

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | 12-char hex UUID, unique per run |
| `goal` | `str` | Original user goal |
| `iteration` | `int` | Current loop count (1-based) |
| `context_chunks` | `list[dict]` | RAG results from SENSE — constant across iterations |
| `test_results` | `dict \| None` | Last output from `run_tests` |
| `analysis` | `dict \| None` | Last output from `analyze_failures` |
| `draft` | `dict \| None` | Output from `draft_issue` |
| `trace` | `list[dict]` | Append-only step log |

Context chunks are retrieved once at SENSE and reused across all iterations. Re-retrieving on every iteration would re-embed the query on every loop cycle with no benefit since the corpus does not change during a run.

---

## Iteration Limit

- **Default:** `MAX_AGENT_ITERATIONS=3` (read from `.env`)
- **Hard cap:** enforced by the `while iteration < self.max_iterations` loop — a code bug in DECIDE cannot cause an infinite loop.
- **Override:** `QAAgent(max_iterations=N)` constructor argument for testing.

---

## Stop Conditions

| Condition | Trigger | Agent status | Action |
|---|---|---|---|
| All tests pass | `test_results["status"] == "passed"` | `"passed"` | Draft issue, write trace, return |
| Docker unavailable | `run_tests` returns `"Docker is unavailable"` error | `"error"` | Write trace, return immediately — unrecoverable |
| Planning error | `propose_tests` returns `"error"` | `"error"` | Write trace, return immediately |
| Iteration limit | `iteration >= max_iterations` and tests still failing | `"handoff"` | Draft issue, write trace, return hand-off message |
| Invalid goal | Goal fails validation | `"error"` | Return immediately, no tools called |

---

## Human Hand-Off Behaviour

When the iteration limit is reached:

1. A `handoff_msg` is constructed containing the iteration count, number of failing tests, and the last failure summary from `analyze_failures`.
2. `draft_issue` is called with `approved=False` — this produces a reviewable issue draft without posting to GitHub.
3. The result dict contains both `handoff_msg` (for the UI banner) and `draft` (for the issue preview panel).
4. **No further automated action is taken.** The human must review the draft and explicitly click "Submit to GitHub" to post the issue.

This ensures that no issue is ever posted without a human reviewing the failure analysis and approving it.

---

## Execution Trace

Every run writes a JSON trace to `evidence/traces/<run_id>.json`.

**Trace schema:**

```json
{
  "run_id":    "string",
  "goal":      "string",
  "started":   "ISO 8601 UTC",
  "completed": "ISO 8601 UTC",
  "steps": [
    {
      "step":           "SENSE | PLAN | ACT | OBSERVE | DECIDE",
      "tool":           "string | null",
      "input_summary":  "string",
      "output_summary": "string",
      "decision":       "string",
      "timestamp_utc":  "ISO 8601 UTC"
    }
  ]
}
```

Traces are append-only during the run. The file is written atomically at the end of each run (not per step), so a crashed run produces no partial trace file.

---

## Security Boundaries

| Boundary | Enforcement |
|---|---|
| No arbitrary shell commands | Only `run_tests` can execute code; it uses Docker with a read-only mount |
| No production deployment | `run_tests` targets `python:3.11-slim` — not the host environment |
| No secret access | `.env` values are loaded at module init; prompts never include API key values |
| No auto-merge / auto-deploy | Agent only calls `draft_issue(approved=False)` — human must set `approved=True` |
| Prompt injection blocked | `_validate_goal()` rejects goals matching known injection patterns before any tool is called |
| Iteration hard cap | Loop condition is `while iteration < self.max_iterations` — cannot be bypassed by tool output |
