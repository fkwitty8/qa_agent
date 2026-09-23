# Week 2 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 11th September 2026

---

## Work Completed Against Weekly Objectives

### Model Selection and Integration

Selected **Gemini 2.0 Flash** (`gemini-2.0-flash`) via Google AI Studio as the foundation model. The decision supersedes the DeepSeek assumption from Week 1 (see Key Decisions below). The model is integrated through `src/llm_client.py`, a thin wrapper around the `google-genai` 2.x SDK exposing two public functions:

- `generate(prompt, system_prompt, model, temperature)` — returns the model's text response with structured logging of model ID, prompt hash, token counts, and latency.
- `embed(text, model)` — returns a 768-dimensional float vector using `text-embedding-004`, ready for ChromaDB ingestion in Week 3.

Both functions raise typed, descriptive exceptions (`EnvironmentError` for missing or invalid API keys, `ValueError` for empty responses, `RuntimeError` for other API errors).

### Prompt Engineering

Two prompt specifications were written, evaluated, and versioned:

**`prompts/test_planner.txt`** — Given a goal description and retrieved code context, returns a structured JSON test plan (list of pytest test cases with name, type, description, and executable code string). Constraints include 3–8 test cases per plan, no real network calls in generated test code, and no placeholder assertions.

**`prompts/failure_analyzer.txt`** — Given raw pytest stdout/stderr and relevant source context, returns a structured JSON root-cause analysis with fields: `summary`, `root_cause`, `affected_files`, `suggested_fix`, and `confidence` (high/medium/low).

Both prompts follow a consistent structure: ROLE → TASK → CONSTRAINTS → NEGATIVE EXAMPLE → OUTPUT FORMAT.

### Prompt Versioning

A `prompts/versions/` directory was created to archive every meaningful iteration. Current contents:

```
prompts/versions/
  test_planner_v1.0.txt
  test_planner_v1.1.txt
  failure_analyzer_v1.0.txt
  failure_analyzer_v1.1.txt
```

The `src/prompt_loader.py` module was implemented to load prompts from disk by name and version, with a `fill_template()` function for `{{PLACEHOLDER}}` substitution. This gives the agent a single, tested interface for all prompt access rather than raw file reads scattered through the codebase.

### Prompt Evaluation

Ten manual test cases were run against both prompts (5 each) using `gemini-2.0-flash`. Full results are in `docs/evaluation/prompt_eval_table.md`.

| Prompt | Version tested | Cases | Passed |
|---|---|---|---|
| test_planner | v1.0 | 5 | 4 (80%) |
| failure_analyzer | v1.0 | 5 | 5 (100%) |
| **Total** | | **10** | **9 (90%)** |

---

## Key Engineering Decisions

**Gemini 2.0 Flash selected over DeepSeek.** The Week 1 charter assumed DeepSeek free credits as the model source. During Week 2 integration, DeepSeek's free-tier API showed frequent 503 errors during off-peak testing and has no co-located embedding model, which would have required a second provider for the RAG pipeline in Week 3. Gemini 2.0 Flash was selected instead: stable free tier, native function calling, 1M-token context window, and `text-embedding-004` available under the same API key. The `llm_client.py` wrapper abstracts the SDK so a future model swap requires only an environment variable change.

**Structured JSON output enforced at the prompt level.** Both prompts instruct the model to return a raw JSON object with no surrounding text. This was chosen over post-processing with a regex extractor because if the model returns malformed JSON, `json.loads()` raises immediately and the agent loop handles it cleanly rather than silently losing data.

**Negative example added to prompt constraints.** Initial testing showed the model would occasionally wrap JSON output in markdown code fences despite an explicit constraint against it (case TP-04). A NEGATIVE EXAMPLE block showing the disallowed format — immediately before the OUTPUT FORMAT section — proved more reliable than a constraint statement alone. This pattern was applied to both prompts in v1.1.

---

## Failures / Challenges and Current Response

**TP-04 — Markdown fence wrapping (test_planner v1.0):** On a longer-context input the model wrapped the JSON output in triple-backtick fences despite the constraint. Root cause: a negative instruction is weaker than a concrete counter-example showing the disallowed format. Fix: added a NEGATIVE EXAMPLE block to both prompts in v1.1. Re-tested and confirmed clean JSON output.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent  |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 2 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed prompt specifications against user stories and acceptance criteria; validated that `test_planner.txt` output format matches US-9 traceability requirement; updated ClickUp with Week 2 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/llm_client.py` (Gemini SDK wrapper, `generate()` and `embed()` functions, structured logging); confirmed model selection and committed `docs/architecture/foundation_model_selection.md`. |
| AI Engineering Lead | Brenda | Authored `prompts/test_planner.txt` and `prompts/failure_analyzer.txt` v1.0 and v1.1; ran all 10 prompt evaluation cases; documented failures and v1.1 changes in `docs/evaluation/prompt_eval_table.md`. |
| Quality/Security Lead | Dorcus | Implemented `src/prompt_loader.py`; wrote `tests/test_prompt_loader.py` (11 test cases) and verified all 17 tests pass; set up `prompts/versions/` versioning convention; authored this progress report. |

---

## Plan for Next Week (Week 3 — Context Engineering and RAG)

- Implement `src/rag.py`: crawl `src/`, `tests/`, `prompts/`, and `docs/` for `.py` and `.md` files; chunk by function/section; embed with `text-embedding-004`; store in a persistent ChromaDB collection with provenance metadata (`file_path`, `chunk_type`, `last_modified`).
- Expose `retrieve(query, top_k=5)` returning ranked chunks with source file paths.
- Create `knowledge/corpus_register.md` listing every indexed file and chunk count.
- Write 15 RAG evaluation questions (5 answerable, 5 partially answerable, 5 deliberately unanswerable) and document at least 3 retrieval failures with root causes.
- Write unit tests for `rag.py` with mocked ChromaDB.
- Demo target: query "how does the agent loop work?" and print the top 5 retrieved chunks with source file paths to the console.
