# Observability and Tracing Notes

**Project:** Software Engineering QA Agent  
**Last updated:** 2026-10-16

---

## What Is Logged

The system produces two kinds of output: per-run JSON trace files and a persistent JSON-lines application log.

### 1. Per-run JSON traces — `evidence/traces/<run_id>.json`

Written atomically at the end of every `QAAgent.run()` call (including error/handoff runs). One file per run, named by the 12-char hex run ID.

**Schema:**
```json
{
  "run_id":    "string — 12-char hex UUID",
  "goal":      "string — original user goal",
  "started":   "ISO 8601 UTC",
  "completed": "ISO 8601 UTC",
  "steps": [
    {
      "step":           "SENSE | PLAN | ACT | OBSERVE | DECIDE",
      "tool":           "string | null — tool name if a tool was called",
      "input_summary":  "string — abbreviated input description",
      "output_summary": "string — abbreviated output description",
      "decision":       "string — what the agent decided to do next",
      "timestamp_utc":  "ISO 8601 UTC"
    }
  ]
}
```

Each step record is written by `_record()` immediately after the step completes — the list is append-only during the run. The file is written once at the end.

### 2. JSON-lines application log — `evidence/traces/app.log`

A persistent append-only log file. Every log call from any `src.*` module is written as a single JSON object per line. Initialised by `src/logging_config.configure_logging()`, which is called automatically when `src/agents.py` is imported.

**Fields present on every line:**

| Field | Type | Description |
|---|---|---|
| `timestamp` | ISO 8601 UTC | Log record creation time |
| `level` | string | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `logger` | string | Logger name, e.g. `src.agents`, `src.tools`, `src.rag` |
| `message` | string | Human-readable log message |

**Additional fields from `extra=` dict (where present):**

| Field | Logged by | Description |
|---|---|---|
| `event` | All modules | Structured event name, e.g. `llm_call_start`, `tool_call`, `rag_retrieve` |
| `run_id` | `src.agents` | Agent run identifier |
| `tool` | `src.tools` | Tool name |
| `params_keys` | `src.tools` | List of parameter keys passed to the tool |
| `latency_ms` | `src.tools`, `src.llm_client` | Wall-clock time for the call |
| `prompt_hash` | `src.llm_client` | First 8 chars of SHA-256 of the prompt |
| `input_tokens` | `src.llm_client` | Gemini prompt token count |
| `output_tokens` | `src.llm_client` | Gemini candidate token count |
| `model` | `src.llm_client` | Gemini model ID |
| `query_length` | `src.rag` | Character length of the retrieval query |
| `results` | `src.rag` | Number of chunks returned |
| `total_chunks` | `src.rag` | Total chunks after `build_index()` |
| `status` | `src.tools` | Tool result status |

---

## Log Format

```
{"timestamp": "2026-10-16T10:22:01.441+00:00", "level": "INFO", "logger": "src.agents", "message": "Agent run started", "event": "agent_run_start", "run_id": "a3f1c9e82b04", "goal": "test that rag.retrieve() handles..."}
{"timestamp": "2026-10-16T10:22:04.112+00:00", "level": "INFO", "logger": "src.tools", "message": "Tool call", "event": "tool_call", "tool": "propose_tests", "params_keys": ["issue_description"]}
{"timestamp": "2026-10-16T10:22:11.887+00:00", "level": "INFO", "logger": "src.llm_client", "message": "LLM call start", "event": "llm_call_start", "model": "gemini-3.8-flash", "prompt_hash": "a3f1c9e8", "prompt_length": 2341}
{"timestamp": "2026-10-16T10:22:14.203+00:00", "level": "INFO", "logger": "src.llm_client", "message": "LLM call complete", "event": "llm_call_complete", "model": "gemini-3.8-flash", "prompt_hash": "a3f1c9e8", "input_tokens": 512, "output_tokens": 318, "latency_ms": 2316}
{"timestamp": "2026-10-16T10:22:14.251+00:00", "level": "INFO", "logger": "src.tools", "message": "Tool call complete", "event": "tool_call_complete", "tool": "propose_tests", "latency_ms": 10139, "status": "ok"}
```

---

## How to Read Traces

### Reading a per-run trace

```python
import json
from pathlib import Path

trace = json.loads(Path("evidence/traces/a3f1c9e82b04.json").read_text())
print(f"Goal: {trace['goal']}")
print(f"Duration: {trace['started']} → {trace['completed']}")
for step in trace["steps"]:
    print(f"  {step['step']:8s} {step.get('tool') or '—':20s} → {step['decision']}")
```

### Filtering the app.log for a specific run

```python
import json

run_id = "a3f1c9e82b04"
with open("evidence/traces/app.log") as f:
    for line in f:
        record = json.loads(line)
        if record.get("run_id") == run_id or run_id in record.get("message", ""):
            print(record)
```

### Finding all LLM calls and their latencies

```python
import json

with open("evidence/traces/app.log") as f:
    calls = [json.loads(l) for l in f if '"llm_call_complete"' in l]

for c in calls:
    print(f"{c['timestamp']}  {c['model']}  {c['latency_ms']}ms  "
          f"in={c.get('input_tokens')}  out={c.get('output_tokens')}")
```

### Finding guardrail blocks (injection rejections)

```python
import json

with open("evidence/traces/app.log") as f:
    for line in f:
        record = json.loads(line)
        if record.get("level") == "ERROR" or "injection" in record.get("message", "").lower():
            print(record)
```

---

## Guardrails Implemented

| Guardrail | Location | What it blocks | Evidence |
|---|---|---|---|
| Input length limit (>2 000 chars) | `QAAgent._validate_goal()` | Oversized goal strings | `status='error'`, message contains "maximum length" |
| Prompt injection detection | `QAAgent._validate_goal()` | 10 injection patterns including "ignore previous instructions", "you are now", "disregard your", "bypass your", "deploy to production" | `status='error'`, message contains "injection" |
| Tool allow-list | `call_tool()` | Any tool name not in TOOL_REGISTRY | `ToolNotAllowedError` raised immediately |
| Required parameter validation | `call_tool()` | Missing required parameters | `ValueError` raised immediately |
| Syntax gate | `run_tests()` | Syntactically invalid test code | `status='error'` returned before Docker is started |
| Docker sandbox isolation | `run_tests()` container | Host filesystem access; `src.*` imports | Container has no project installation; test file mounted read-only |
| Output schema validation | `_validate_tool_output()` in `agents.py` | Tool outputs missing required keys | `status='error'` with schema error message |
| Human approval gate | `draft_issue(approved=False)` | Autonomous GitHub issue posting | `github_url` remains null; user must explicitly set `approved=True` |
| Iteration hard cap | `while iteration < self.max_iterations` | Infinite loops from tool output | Loop condition cannot be bypassed |
