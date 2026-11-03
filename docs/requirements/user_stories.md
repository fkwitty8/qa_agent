# User Stories and Acceptance Criteria

**Project:** Software Engineering QA Agent  
**Version:** 1.0  
**Last updated:** 2026-10-23

---

## US-01 — Goal-Directed Test Generation

**As a** software developer,  
**I want to** describe a module or behaviour in plain English and receive a runnable pytest test plan,  
**so that** I can verify code quality without writing tests manually.

**Acceptance criteria:**
- AC-01.1: Given a valid goal string (≤2000 chars, no injection patterns), the agent calls `propose_tests` and returns a list of ≥1 test case dicts.
- AC-01.2: Each test case contains `name`, `type`, `description`, and `code` fields.
- AC-01.3: The `code` field is syntactically valid Python 3.11+.
- AC-01.4: The agent completes within `MAX_AGENT_ITERATIONS` cycles.

---

## US-02 — Sandboxed Test Execution

**As a** software developer,  
**I want** generated tests to run in an isolated environment,  
**so that** test execution cannot damage my local machine or access production systems.

**Acceptance criteria:**
- AC-02.1: Tests run inside a `python:3.11-slim` Docker container.
- AC-02.2: The test file is mounted read-only; the container has no write access to the host filesystem.
- AC-02.3: The container is always removed after the run, including on timeout or error.
- AC-02.4: A configurable timeout (default 30s) prevents runaway containers.
- AC-02.5: Syntactically invalid code is rejected before Docker is started.

---

## US-03 — Failure Root-Cause Analysis

**As a** software developer,  
**I want** the agent to explain why tests failed in plain English,  
**so that** I can understand the defect without reading raw pytest output.

**Acceptance criteria:**
- AC-03.1: When `run_tests` returns `status='failed'`, the agent calls `analyze_failures`.
- AC-03.2: The analysis returns `summary`, `root_cause`, `affected_files`, `suggested_fix`, and `confidence`.
- AC-03.3: The analysis cites specific function or variable names from the source code.
- AC-03.4: If all tests pass, `analyze_failures` is not called (short-circuit).

---

## US-04 — Human-Approved GitHub Issue Drafting

**As a** software developer,  
**I want** the agent to draft a GitHub issue from the failure analysis and wait for my approval before posting,  
**so that** no issue is ever created without my review.

**Acceptance criteria:**
- AC-04.1: `draft_issue` is always called with `approved=False` by the agent — never `approved=True`.
- AC-04.2: The draft is displayed in the UI for review before any GitHub API call is made.
- AC-04.3: The issue is posted only after the user explicitly clicks "Submit to GitHub".
- AC-04.4: The issue body contains Summary, Root Cause, Affected Files, Suggested Fix, Confidence, and Context sections.
- AC-04.5: `github_url` is populated in the result after a successful post.

---

## US-05 — Iteration Limit and Human Hand-Off

**As a** software developer,  
**I want** the agent to stop retrying and hand off to me when it cannot fix failing tests,  
**so that** the agent never runs indefinitely or makes autonomous decisions beyond its capability.

**Acceptance criteria:**
- AC-05.1: The agent stops after `MAX_AGENT_ITERATIONS` iterations (default 3, configurable via `.env`).
- AC-05.2: When the limit is reached, the agent returns `status='handoff'` with a `handoff_msg` explaining what failed.
- AC-05.3: A draft issue is still produced on hand-off so the developer has a starting point.
- AC-05.4: The iteration limit cannot be bypassed by tool output (enforced by loop condition).

---

## US-06 — Run History and Trace Inspection

**As a** software developer,  
**I want** to view past agent runs and their execution traces,  
**so that** I can audit what the agent did and reproduce any result.

**Acceptance criteria:**
- AC-06.1: Every completed run is persisted to SQLite (`evidence/qa_agent.db`).
- AC-06.2: The sidebar shows the last 10 runs with status icons, newest first.
- AC-06.3: Clicking a past run restores its trace and draft to the main panel.
- AC-06.4: Each run's full step-by-step trace is viewable as expandable cards and raw JSON.
- AC-06.5: Run history persists across page reloads and app restarts.

---

## US-07 — Prompt Injection and Input Guardrails

**As a** system operator,  
**I want** the agent to reject malicious or oversized goal inputs before calling any tool,  
**so that** the agent cannot be manipulated into unsafe behaviour.

**Acceptance criteria:**
- AC-07.1: Goals containing known injection patterns (e.g. "ignore previous instructions", "you are now") are rejected with `status='error'` before any tool is called.
- AC-07.2: Goals exceeding 2000 characters are rejected with a clear error message.
- AC-07.3: Goals describing prohibited actions (e.g. "deploy to production", "merge to main") are rejected.
- AC-07.4: All 6 adversarial scenarios in `eval_scenarios.json` are blocked by the guardrail.

---

## US-08 — Contextually Grounded Test Plans

**As a** software developer,  
**I want** generated tests to be grounded in the actual source code,  
**so that** tests reference real functions and classes rather than invented ones.

**Acceptance criteria:**
- AC-08.1: `propose_tests` retrieves ≥1 relevant RAG chunk before calling the LLM when the corpus is indexed.
- AC-08.2: Retrieved chunks are passed as context to the test planner prompt.
- AC-08.3: When no relevant context is retrieved (score > 0.7 threshold), the LLM is explicitly told no context is available and generates self-contained tests.
- AC-08.4: Source file paths of retrieved chunks are included in the trace.

---

## US-09 — Traceability from Test to Requirement

**As a** quality engineer,  
**I want** every proposed test to reference the goal that triggered it,  
**so that** coverage claims can be verified against requirements.

**Acceptance criteria:**
- AC-09.1: The `goal` field in the `propose_tests` output matches the original user input exactly.
- AC-09.2: The trace JSON records the goal string at the SENSE step.
- AC-09.3: The drafted GitHub issue body includes the original goal in the Context section.

---

## US-10 — Observability and Structured Logging

**As a** quality engineer,  
**I want** every tool call, LLM call, and agent decision to be logged in a structured format,  
**so that** I can diagnose failures and measure system performance.

**Acceptance criteria:**
- AC-10.1: Every agent run writes structured JSON-lines to `evidence/traces/app.log`.
- AC-10.2: Each log entry contains `timestamp`, `level`, `logger`, `message`, and an `event` field.
- AC-10.3: LLM calls log `prompt_hash`, `input_tokens`, `output_tokens`, and `latency_ms`.
- AC-10.4: Tool calls log `tool`, `params_keys`, `latency_ms`, and `status`.
- AC-10.5: Every run also writes a standalone JSON trace to `evidence/traces/<run_id>.json`.

---

## US-11 — Reproducible Setup

**As a** new team member,  
**I want** to be able to run the full system from a fresh clone in under 10 minutes,  
**so that** the project is not dependent on undocumented local configuration.

**Acceptance criteria:**
- AC-11.1: `requirements.txt` contains all Python dependencies with pinned versions.
- AC-11.2: `.env.example` documents every required environment variable with a description.
- AC-11.3: `README.md` provides a complete quick-start from `git clone` to running UI.
- AC-11.4: `docker pull python:3.11-slim` is the only Docker setup step required.
- AC-11.5: `pytest tests/` passes on a clean install with no additional configuration.

---

## US-12 — No Secrets in Version Control

**As a** security-conscious developer,  
**I want** all secrets to be excluded from the repository,  
**so that** API keys and tokens are never accidentally exposed.

**Acceptance criteria:**
- AC-12.1: `.env` is listed in `.gitignore` and never committed.
- AC-12.2: `evidence/qa_agent.db` (run history) is listed in `.gitignore`.
- AC-12.3: `evidence/chroma_db/` (vector store) is listed in `.gitignore`.
- AC-12.4: `.env.example` contains only placeholder values — no real keys.
- AC-12.5: No `src.*` file reads from the filesystem for secrets outside of `dotenv` loading.
