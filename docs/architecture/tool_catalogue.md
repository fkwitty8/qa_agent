# Tool Catalogue

**Project:** Software Engineering QA Agent  
**Last updated:** 2026-09-14  
**Implemented in:** `src/tools.py`  
**Entry point:** `call_tool(name, params)` — enforces the allow-list before dispatching.

---

## Allow-List

Only the following tool names are permitted. Any call to a name not in this list raises `ToolNotAllowedError` immediately, before any parameter validation or execution occurs.

| Tool name | Status |
|---|---|
| `propose_tests` | ✅ Implemented (Task 4) |
| `run_tests` | ✅ Implemented (Task 4) |
| `analyze_failures` | 🔲 Stub — implemented in Task 5 |
| `draft_issue` | 🔲 Stub — implemented in Task 5 |

---

## Tool 1: propose_tests

**Purpose:** Retrieve relevant code context via RAG and generate a structured pytest test plan using Gemini.

### Input Schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `issue_description` | `string` | ✅ Yes | Short description of the behaviour or module to test. |

### Output Schema

```json
{
  "goal":           "string — the original issue_description",
  "context_chunks": "list[dict] — top-5 RAG chunks used as context",
  "test_cases": [
    {
      "name":        "string — snake_case, starts with test_",
      "type":        "string — unit | integration | edge | failure",
      "description": "string — one sentence",
      "code":        "string — complete pytest function"
    }
  ],
  "error": "string — present only on failure"
}
```

### Permissions

| Permission | Reason |
|---|---|
| `read:corpus` | Calls `rag.retrieve()` to fetch relevant chunks from ChromaDB |
| `call:llm` | Calls `llm_client.generate()` with the `test_planner` prompt |

### Failure Behaviour

All failures return a dict with an `"error"` key. The tool never raises to the caller.

| Failure | Response |
|---|---|
| RAG retrieval raises | `{"error": "RAG retrieval failed: ...", "test_cases": []}` |
| LLM call raises | `{"error": "LLM call failed: ...", "test_cases": []}` |
| Model returns non-JSON | `{"error": "Model returned non-JSON output: ...", "test_cases": []}` |
| Model wraps output in fences | Fences are stripped defensively before `json.loads()` |

### Step-by-Step Flow

```
1. rag.retrieve(issue_description, top_k=5)
       ↓ list of context chunks
2. load_prompt("test_planner")
   fill_template(template, {GOAL: ..., CONTEXT: formatted chunks})
       ↓ filled prompt string
3. llm_client.generate(prompt)
       ↓ raw string response
4. _strip_fences(raw) → json.loads(clean)
       ↓ parsed plan dict
5. return {goal, context_chunks, test_cases}
```

---

## Tool 2: run_tests

**Purpose:** Validate generated test code syntactically, then execute it inside a Docker sandbox and return structured pass/fail results.

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `test_code` | `string` | ✅ Yes | — | Complete pytest module as a Python string. |
| `timeout` | `integer` | ❌ No | `30` | Max seconds for the Docker container to run. |

### Output Schema

```json
{
  "status":    "string — 'passed' | 'failed' | 'error'",
  "passed":    "integer — number of passing tests",
  "failed":    "integer — number of failing tests",
  "stdout":    "string — combined container stdout/stderr",
  "stderr":    "string — error description (populated on status='error')",
  "exit_code": "integer — container exit code (-1 on error)"
}
```

### Permissions

| Permission | Reason |
|---|---|
| `execute:docker_sandbox` | Starts a `python:3.11-slim` container with the test file mounted read-only |

### Safety Controls

- **No host filesystem writes inside the container.** The temp file is mounted read-only (`mode: "ro"`).
- **No network access.** The container is started without any network flags; default bridge networking is not needed for unit tests.
- **Timeout enforced.** `container.wait(timeout=N)` raises if the container exceeds the limit.
- **Always cleaned up.** `container.remove(force=True)` is called in a `try/finally` block regardless of outcome.
- **Syntax gate.** `ast.parse()` validates the code before any Docker call; invalid code never reaches the sandbox.

### Failure Behaviour

All failures return `status='error'` with a descriptive `stderr` message. The tool never raises to the caller.

| Failure | `stderr` message |
|---|---|
| `SyntaxError` in test code | `"Syntax error in test code: ..."` |
| Docker daemon not running | `"Docker is unavailable: ..."` |
| Docker image not found | `"Docker image '...' not found. Run: docker pull ..."` |
| Container exceeds timeout | `"Test run timed out after Ns."` |
| Docker API error | `"Docker API error: ..."` |

### Step-by-Step Flow

```
1. ast.parse(test_code)             ← SyntaxError → return error_result
2. tempfile.NamedTemporaryFile()    ← write test_code
3. docker.from_env()                ← DockerException → return error_result
4. client.containers.run(
     image="python:3.11-slim",
     command="pip install pytest && pytest /test_module.py -v --tb=short",
     volumes={tmp_path: {bind: "/test_module.py", mode: "ro"}},
     detach=True
   )
5. container.wait(timeout=N)        ← timeout → return error_result
6. container.logs()                 ← capture stdout/stderr
7. _parse_pytest_counts(stdout)     ← extract passed/failed integers
8. return {status, passed, failed, stdout, stderr, exit_code}
   └─ finally: container.remove(force=True) + tmp file unlink
```

---

## Tool 3: analyze_failures *(stub — Task 5)*

**Purpose:** Call Gemini with the `failure_analyzer` prompt to produce a structured root-cause analysis from pytest output.

### Input Schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `test_results` | `dict` | ✅ Yes | Output dict from `run_tests`. |

### Output Schema

```json
{
  "summary":        "string — 1–3 sentence overview",
  "root_cause":     "string — specific function/line and condition",
  "affected_files": ["string — relative file paths"],
  "suggested_fix":  "string — concrete description or code snippet",
  "confidence":     "string — high | medium | low"
}
```

### Permissions

| Permission | Reason |
|---|---|
| `call:llm` | Calls `llm_client.generate()` with the `failure_analyzer` prompt |

---

## Tool 4: draft_issue *(stub — Task 5)*

**Purpose:** Construct a GitHub issue payload from a failure analysis and optionally post it via the GitHub API after human approval.

### Input Schema

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `analysis` | `dict` | ✅ Yes | — | Output dict from `analyze_failures`. |
| `approved` | `bool` | ❌ No | `False` | Set `True` to post to GitHub. |

### Output Schema

```json
{
  "title":      "string — issue title",
  "body":       "string — markdown issue body",
  "labels":     ["bug", "qa-agent"],
  "github_url": "string | null — null until approved=True and posted"
}
```

### Permissions

| Permission | Reason |
|---|---|
| `call:github_api` | Only exercised when `approved=True`; requires `GITHUB_TOKEN` in `.env` |

### Human Approval Gate

`approved=False` (default): the draft dict is returned for UI display. No GitHub API call is made.  
`approved=True`: `PyGithub` creates the issue and the URL is added to the returned dict.

This gate is enforced inside the tool implementation — the caller cannot bypass it by omitting the parameter, since the default is always `False`.

---

## Error Taxonomy

| Error type | Which tools | Behaviour |
|---|---|---|
| `ToolNotAllowedError` | All (via `call_tool`) | Raised immediately; never suppressed |
| `ValueError` (missing param) | All (via `call_tool`) | Raised immediately; never suppressed |
| RAG failure | `propose_tests` | Returned as `{"error": ...}` dict |
| LLM failure | `propose_tests`, `analyze_failures` | Returned as `{"error": ...}` dict |
| Syntax error | `run_tests` | Returned as `{"status": "error", ...}` dict |
| Docker failure | `run_tests` | Returned as `{"status": "error", ...}` dict |
| GitHub auth error | `draft_issue` | Returned as `{"error": ...}` dict |
