# MCP-Style Interface Specification

**Project:** Software Engineering QA Agent  
**Last updated:** 2026-10-09

This document describes the four agent tools as an MCP-style (Model Context Protocol) interface specification. Each entry defines the capability, inputs, outputs, permissions, and security boundary in a format compatible with the MCP tool-definition convention.

---

## Interface Overview

**Server name:** `qa-agent`  
**Transport:** Local (stdio) — intended for IDE or CLI integration  
**Authentication:** None — local process only  
**Allow-list enforced by:** `src/tools.call_tool()`

---

## Tool 1: propose_tests

```json
{
  "name": "propose_tests",
  "description": "Retrieve relevant source code context via RAG and generate a structured pytest test plan for a given goal description using Gemini.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "issue_description": {
        "type": "string",
        "description": "Short description of the behaviour or module to test.",
        "maxLength": 2000
      }
    },
    "required": ["issue_description"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "goal":           { "type": "string" },
      "context_chunks": { "type": "array", "items": { "type": "object" } },
      "test_cases": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name":        { "type": "string" },
            "type":        { "type": "string", "enum": ["unit", "integration", "edge", "failure"] },
            "description": { "type": "string" },
            "code":        { "type": "string" }
          },
          "required": ["name", "type", "description", "code"]
        }
      },
      "error": { "type": "string", "description": "Present only on failure." }
    }
  }
}
```

**Permissions:** `read:corpus`, `call:llm`  
**Side effects:** None — read-only. Does not write to disk or call external APIs beyond Gemini.  
**Security boundary:** RAG retrieval is limited to the indexed corpus. The LLM receives only the filled prompt template — no raw file paths, API keys, or environment variables are included.

---

## Tool 2: run_tests

```json
{
  "name": "run_tests",
  "description": "Validate generated test code syntactically and execute it inside an isolated Docker sandbox, returning structured pass/fail results.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "test_code": {
        "type": "string",
        "description": "Complete pytest module as a Python string."
      },
      "timeout": {
        "type": "integer",
        "description": "Max seconds for the Docker container to run.",
        "default": 30,
        "minimum": 1,
        "maximum": 300
      }
    },
    "required": ["test_code"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "status":    { "type": "string", "enum": ["passed", "failed", "error"] },
      "passed":    { "type": "integer" },
      "failed":    { "type": "integer" },
      "stdout":    { "type": "string" },
      "stderr":    { "type": "string" },
      "exit_code": { "type": "integer" }
    },
    "required": ["status", "passed", "failed", "stdout", "stderr", "exit_code"]
  }
}
```

**Permissions:** `execute:docker_sandbox`  
**Side effects:** Starts and removes a Docker container. Writes and deletes a temp file. No persistent filesystem changes.  
**Security boundary:**
- Test code is validated with `ast.parse()` before Docker launch — invalid code never reaches the sandbox.
- The temp file is mounted read-only inside the container.
- The container has no access to the host filesystem beyond the mounted test file.
- Container is always removed (`try/finally`) — no orphaned containers.
- Docker image is pinned to `python:3.11-slim` via `DOCKER_IMAGE` env var.

---

## Tool 3: analyze_failures

```json
{
  "name": "analyze_failures",
  "description": "Analyse pytest failure output from run_tests and produce a structured root-cause analysis using Gemini.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "test_results": {
        "type": "object",
        "description": "Output dict from run_tests. Must contain 'status' and 'stdout'.",
        "properties": {
          "status": { "type": "string" },
          "stdout": { "type": "string" }
        },
        "required": ["status"]
      }
    },
    "required": ["test_results"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "summary":        { "type": "string" },
      "root_cause":     { "type": "string" },
      "affected_files": { "type": "array", "items": { "type": "string" } },
      "suggested_fix":  { "type": "string" },
      "confidence":     { "type": "string", "enum": ["high", "medium", "low"] },
      "error":          { "type": "string", "description": "Present only on failure." }
    },
    "required": ["summary", "root_cause", "affected_files", "suggested_fix", "confidence"]
  }
}
```

**Permissions:** `read:corpus`, `call:llm`  
**Side effects:** None — read-only. RAG retrieval and LLM call only.  
**Security boundary:** Short-circuits immediately when `status == "passed"` — no LLM call made. RAG failure is non-fatal; analysis proceeds with empty context. The LLM receives only the filled prompt — no secrets or environment variables are included.

---

## Tool 4: draft_issue

```json
{
  "name": "draft_issue",
  "description": "Format a failure analysis into a GitHub issue using Gemini, and optionally post it to GitHub after explicit human approval.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "analysis": {
        "type": "object",
        "description": "Output dict from analyze_failures."
      },
      "approved": {
        "type": "boolean",
        "description": "Set true to post to GitHub. Default false (draft only).",
        "default": false
      },
      "goal": {
        "type": "string",
        "description": "Original agent goal for traceability in the issue body.",
        "default": ""
      }
    },
    "required": ["analysis"]
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "title":      { "type": "string", "maxLength": 72 },
      "body":       { "type": "string", "description": "Markdown issue body." },
      "labels":     { "type": "array", "items": { "type": "string" } },
      "github_url": { "type": ["string", "null"], "description": "null until approved=true and posted." },
      "error":      { "type": "string", "description": "Present only on posting failure; draft still returned." }
    },
    "required": ["title", "body", "labels", "github_url"]
  }
}
```

**Permissions:** `call:llm`, `call:github_api (approved=true only)`  
**Side effects:** When `approved=true` — creates one GitHub issue via PyGithub. No other side effects.  
**Security boundary:**
- The GitHub API is only called when `approved=true` is explicitly set by the human operator. The default is always `false`.
- `GITHUB_TOKEN` is read from `.env` and never logged, printed, or included in any prompt.
- Token scope should be `repo` (issues read/write only). No write access to code, branches, or settings is required.
- Fallback formatters generate the issue body locally if the LLM call fails — the draft is always returned.

---

## Error Handling

All four tools return error information as a field in the output dict rather than raising exceptions. This keeps the MCP interface uniform — callers always receive a structured dict.

| Tool | Error field | Error type |
|---|---|---|
| `propose_tests` | `"error"` key in output | RAG failure, LLM failure, JSON parse failure |
| `run_tests` | `status = "error"`, `stderr` field | Syntax error, Docker unavailable, timeout |
| `analyze_failures` | `"error"` key in output | LLM failure, JSON parse failure |
| `draft_issue` | `"error"` key in output; draft still returned | LLM failure (uses fallback), GitHub auth/API error |

`ToolNotAllowedError` and `ValueError` (missing params) are the only cases that raise — these are programming errors at the call boundary, not recoverable runtime failures.
