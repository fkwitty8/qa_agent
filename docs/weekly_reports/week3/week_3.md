# Week 3 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 18th September 2026

---

## Work Completed Against Weekly Objectives

### Corpus Assembly and Ingestion

Assembled the RAG corpus from the project's own codebase and documentation. The corpus covers five directories:

| Directory | Extensions indexed | Files indexed | Est. chunks |
|---|---|---|---|
| `src/` | `.py` | 3 | ~26 |
| `tests/` | `.py` | 2 | ~21 |
| `prompts/` | `.txt`, `.md` | 2 | ~10 |
| `docs/` | `.md` | 8 | ~29 |
| `knowledge/` | `.md` | 0 (empty files skipped) | 0 |
| **Total** | | **15** | **~86** |

Full provenance is recorded in `knowledge/corpus_register.md`: every indexed file, its chunking strategy, estimated chunk count, and exclusion rules.

### Chunking and Embedding

`src/rag.py` implements two chunking strategies:

- **Python files** — AST-based: top-level `def` and `class` nodes are extracted as individual chunks; a preamble chunk covers module docstrings and imports. Falls back to a single file chunk on `SyntaxError`.
- **Markdown and text files** — Heading-based: content is split on `#` headings into section chunks. Files with no headings are stored as a single file chunk.

Each chunk carries provenance metadata: `file_path` (relative to project root), `chunk_type` (`function` | `class` | `section` | `file`), `chunk_name`, and `last_modified` (ISO 8601 UTC from filesystem `mtime`).

Chunks are embedded using `text-embedding-004` (768-dim) via `src/llm_client.embed()` and upserted into a persistent ChromaDB collection (`qa_agent_corpus`) stored at `evidence/chroma_db/`. Chunk IDs are deterministic SHA-256 hashes of the file path, chunk type, name, and first 64 characters of text — making `build_index()` fully idempotent.

### Retrieval

`retrieve(query, top_k=5)` embeds the query with the same model, queries ChromaDB by cosine similarity, and returns a ranked list of dicts:

```
[{"text": "...", "file_path": "src/llm_client.py", "chunk_type": "function",
  "chunk_name": "_get_client", "score": 0.18}, ...]
```

Returns an empty list with a warning log when the collection is empty (graceful fallback — no exception).

### RAG Evaluation

15 evaluation questions were run across three categories, recorded in `docs/evaluation/rag_eval.md`:

| Category | Questions | Result |
|---|---|---|
| A — Answerable | 5 | 5/5 pass |
| B — Partially answerable | 5 | 5/5 partial |
| C — Unanswerable | 5 | 5/5 correctly unanswerable |

Three retrieval failures were identified and documented (see Failures section below).

---

## Key Engineering Decisions

**AST-based chunking for Python files over fixed-size character splitting.** Fixed-size splitting cuts across function boundaries, producing chunks that contain half of one function and half of another. AST chunking keeps each function and class self-contained, which gives the retriever a clean semantic unit and the downstream LLM a coherent code block to reason about.

**Deterministic chunk IDs via SHA-256.** Chunk IDs are computed from content rather than auto-incremented. This makes `build_index()` idempotent — re-running it updates changed chunks in-place without duplicating unchanged ones. This is important for a self-referential corpus where the codebase changes every week.

**`prompts/versions/` excluded from indexing.** Versioned prompt copies are archives of the same content as the canonical `prompts/` files. Indexing them would duplicate every prompt chunk and inflate retrieval scores for prompt-related queries. The exclusion pattern is documented in `knowledge/corpus_register.md`.

**Empty collection returns `[]` instead of raising.** The agent loop needs to handle the case where `build_index()` has not been run yet (e.g. fresh clone). Raising an exception at retrieval time would break the loop before it starts. Returning an empty list with a warning log lets the agent detect the situation and surface a clear message to the user.

---

## Failures / Challenges and Current Response

**RF-01 — Root-level `.md` files outside indexed corpus:** `IMPLEMENTATION_PLAN.md` and `capstone.md` live at the project root, outside all five crawl directories. Queries about the agent loop and weekly deliverables return partial answers from weekly reports rather than the authoritative source. Fix identified: add `(_PROJECT_ROOT, [".md"])` to `_CRAWL_DIRS`, or move these files into `docs/`. Scheduled for the Week 4 hardening pass.

**RF-02 — DeepSeek omitted from model selection doc:** `docs/architecture/foundation_model_selection.md` does not list DeepSeek as a rejected candidate, even though it was evaluated during Week 2 and rejected for reliability and embedding issues. Queries about model selection return this doc first but give an incomplete answer. Fix: update the model selection doc to include DeepSeek. Applied immediately — see the updated file.

**RF-03 — No similarity threshold allows low-relevance chunks through:** ChromaDB always returns `top_k` results regardless of relevance score. Queries about unimplemented modules (`memory.py`, `tools.py`, `main.py`) return distantly related chunks that could mislead the downstream LLM. Fix: add a `min_score` filter to `retrieve()`. Scheduled for the Week 8 hardening pass.

**Bug — `_chunk_markdown()` labelled heading-free text as `section`:** `re.split` on text with no headings returns the whole text as a single element, and the code then entered the heading loop using the first line as a section name. Fixed by gating the heading loop on `len(sections) > 1`. Caught by the test suite and fixed before merging.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | https://github.com/fkwitty8/qa_agent  |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 3 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Reviewed `knowledge/corpus_register.md` against user stories for traceability coverage; verified corpus provenance notes; updated ClickUp with Week 3 tasks and evidence links. |
| Application/Integration Lead | Fahad | Implemented `src/rag.py` (`build_index()`, `retrieve()`, ChromaDB integration, persistent storage at `evidence/chroma_db/`); fixed the `_chunk_markdown()` heading-detection bug. |
| AI Engineering Lead | Brenda | Designed and ran the 15-question RAG evaluation; documented 3 retrieval failure cases in `docs/evaluation/rag_eval.md`; updated `docs/architecture/foundation_model_selection.md` to add DeepSeek as a rejected candidate (RF-02 fix). |
| Quality/Security Lead | Dorcus | Wrote `tests/test_rag.py` (15 test cases, 32/32 passing); authored `knowledge/corpus_register.md`; authored this progress report. |

---

## Plan for Next Week (Week 4 — Tools and Function Calling)

- Implement `src/tools.py` with two tools — `propose_tests` (RAG + Gemini) and `run_tests` (Docker sandbox) — each with a schema dict (`name`, `description`, `input_schema`, `output_schema`, `permissions`, `failure_behaviour`).
- Add a tool allow-list: only `propose_tests`, `run_tests`, `analyze_failures`, `draft_issue` permitted; all other calls rejected.
- Add `(_PROJECT_ROOT, [".md"])` to `_CRAWL_DIRS` to capture `IMPLEMENTATION_PLAN.md` and `capstone.md` (RF-01 fix).
- Write unit tests for both tools with mocked Gemini and mocked Docker client.
- Create `docs/architecture/tool_catalogue.md` with full schemas and permission notes.
- Demo target: given "the RAG retriever should handle empty corpus", the agent proposes 3 test cases and runs them in Docker, returning pass/fail results.
