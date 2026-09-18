# Foundation Model Selection

## Decision Summary

**Selected model:** Gemini 2.0 Flash (`gemini-2.0-flash`)  
**Embedding model:** `models/text-embedding-004`  
**Provider:** Google AI Studio (free tier)  
**SDK:** `google-genai` 2.x (`from google import genai`)  
**Decision date:** 2026-09-14

---

## Evaluation Criteria

| Criterion | Weight | Notes |
|---|---|---|
| Function / tool calling support | High | Required for the bounded agent loop |
| Free-tier availability | High | No budget allocated for API costs |
| Context window size | Medium | Must handle full file chunks + prompt |
| Output quality for code tasks | High | Test plan and failure analysis quality |
| Latency | Medium | UI responsiveness; not real-time |
| Privacy / data handling | Medium | No PII in prompts; code only |
| Embedding API available | High | Required for ChromaDB RAG pipeline |

---

## Candidates Considered

### Gemini 2.0 Flash ✅ Selected

| Property | Value |
|---|---|
| Context window | 1 million tokens |
| Free tier | Yes — Google AI Studio, no credit card required |
| Function calling | Native, structured JSON output |
| Embedding model | `text-embedding-004` (768-dim, free tier) |
| Latency (typical) | 500–1500 ms per call |
| Output format control | `response_mime_type`, `response_schema` via SDK |
| Data handling | Prompts may be used to improve Google models on free tier |

**Rationale:** Gemini 2.0 Flash hits all hard requirements — free, function calling,
large context window, and a co-located embedding model that avoids a second provider.
The 1M token window means full file chunks can be passed without aggressive truncation.
The free tier data handling trade-off is acceptable because all content is the agent's
own codebase, not user PII.

---

### GPT-4o (OpenAI) ❌ Not selected

| Property | Value |
|---|---|
| Context window | 128k tokens |
| Free tier | No — paid API only |
| Function calling | Yes |
| Embedding model | Separate (`text-embedding-3-small`) |

**Reason rejected:** No free tier. Would require OpenAI credits for every run, making
the project unsustainable for a student capstone.

---

### Claude 3.5 Sonnet (Anthropic) ❌ Not selected

| Property | Value |
|---|---|
| Context window | 200k tokens |
| Free tier | No — paid API only |
| Function calling | Yes (tool use) |

**Reason rejected:** No free tier. Same cost issue as GPT-4o.

---

### Llama 3.1 70B (local via Ollama) ❌ Not selected

| Property | Value |
|---|---|
| Context window | 128k tokens |
| Free tier | Yes (local) |
| Function calling | Limited / model-dependent |
| Embedding model | Separate setup required |

**Reason rejected:** Local inference on a student laptop is too slow for an interactive
UI and Docker sandbox workflow. Function calling reliability is significantly lower than
hosted models at this parameter count. Setup complexity adds risk with no clear benefit.

---

## Capability Assessment

### Test Plan Generation (`test_planner.txt`)

Gemini 2.0 Flash reliably produces syntactically valid Python pytest code when given
structured prompts with explicit output format constraints. Evaluation across 5 manual
test cases (see `docs/evaluation/prompt_eval_table.md`) showed 4/5 passing on v1.0,
with the one failure caused by the model wrapping output in markdown fences despite
the constraint — fixed in v1.1 by adding an explicit negative example.

### Failure Analysis (`failure_analyzer.txt`)

The model correctly identifies root causes from raw pytest tracebacks in structured JSON.
Confidence scoring is self-reported; spot-checks confirm "high" confidence correlates
with single-failure, complete-traceback inputs and "low" with truncated or multi-module
failures. 5/5 manual cases passed on v1.0.

### Embeddings (`text-embedding-004`)

768-dimensional embeddings. Cosine similarity retrieval via ChromaDB gives semantically
relevant results for code-related queries (function names, module descriptions). No
fine-tuning available on the free tier — zero-shot retrieval only.

---

## Privacy & Safety Notes

- **No PII in prompts.** All prompts contain only source code and test output from
  this project's own codebase. No user data, credentials, or personal information is
  ever included.
- **API key scope.** The `GEMINI_API_KEY` is loaded exclusively from `.env` (gitignored).
  It is never logged, printed, or included in any prompt.
- **Free tier data use.** Google may use prompts sent via AI Studio free tier to improve
  its models. This is acceptable given the content is non-sensitive codebase material.
  If sensitive code were involved, a paid tier or self-hosted model would be required.
- **No auto-execution of model output.** All generated test code is validated with
  `ast.parse()` before execution and runs inside a Docker sandbox, not the host Python
  environment.

---

## Cost Projection

| Scenario | Calls per run | Est. tokens per call | Free tier limit |
|---|---|---|---|
| Test plan generation | 1 | ~2 000 | 1 500 req/day |
| Failure analysis | 1 | ~1 500 | 1 500 req/day |
| Embedding (RAG index) | ~50 (one-off) | ~500 | 1 500 req/day |
| Embedding (retrieval) | 1 | ~200 | 1 500 req/day |

At the expected usage rate (1–5 full agent runs per day during development), the
free tier limit is not a bottleneck.
