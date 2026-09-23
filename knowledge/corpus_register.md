# Corpus Register

**Project:** Software Engineering QA Agent  
**Last updated:** 2026-09-14  
**Indexed by:** `src/rag.py` — `build_index()`  
**ChromaDB collection:** `qa_agent_corpus`  
**Storage path:** `evidence/chroma_db/`  
**Embedding model:** `models/text-embedding-004` (768-dim, cosine similarity)

Provenance metadata stored per chunk: `file_path`, `chunk_type`, `chunk_name`, `last_modified`.  
Chunk types: `function` | `class` | `section` | `file`  
Chunk size limits: 40 chars minimum, 6 000 chars maximum.

---

## Indexed Files

### src/ — Python source modules

| File | Chunk strategy | Est. chunks | Notes |
|---|---|---|---|
| `src/llm_client.py` | AST — functions + preamble | ~6 | `_get_client`, `generate`, `embed`, `_hash`, preamble |
| `src/prompt_loader.py` | AST — functions + preamble | ~8 | `load_prompt`, `load_versioned_prompt`, `fill_template`, helpers, preamble |
| `src/rag.py` | AST — functions + preamble | ~12 | `build_index`, `retrieve`, `_chunk_python`, `_chunk_markdown`, helpers |
| `src/agents.py` | AST — file (empty) | 0 | Skipped — below minimum chunk size |
| `src/tools.py` | AST — file (empty) | 0 | Skipped — below minimum chunk size |
| `src/main.py` | AST — file (empty) | 0 | Skipped — below minimum chunk size |
| `src/__init__.py` | AST — file (empty) | 0 | Skipped — below minimum chunk size |

### tests/ — Python test modules

| File | Chunk strategy | Est. chunks | Notes |
|---|---|---|---|
| `tests/test_llm_client.py` | AST — functions + preamble | ~8 | 6 test functions + helpers + preamble |
| `tests/test_prompt_loader.py` | AST — functions + preamble | ~13 | 11 test functions + fixture + preamble |
| `tests/test_agent.py` | AST — file (empty) | 0 | Skipped — below minimum chunk size |

### prompts/ — Prompt specifications

| File | Chunk strategy | Est. chunks | Notes |
|---|---|---|---|
| `prompts/test_planner.txt` | Markdown sections | ~5 | ROLE, TASK, CONSTRAINTS, NEGATIVE EXAMPLE, OUTPUT FORMAT sections |
| `prompts/failure_analyzer.txt` | Markdown sections | ~5 | ROLE, TASK, CONSTRAINTS, NEGATIVE EXAMPLE, OUTPUT FORMAT sections |
| `prompts/test_runner.txt` | Markdown sections / file | 0 | Skipped — file is empty |

> Note: `prompts/versions/` is excluded from indexing (excluded pattern: `versions`). Versioned copies are archives and would duplicate current prompt content.

### docs/ — Architecture and evaluation documents

| File | Chunk strategy | Est. chunks | Notes |
|---|---|---|---|
| `docs/architecture/foundation_model_selection.md` | Markdown sections | ~7 | Decision Summary, Evaluation Criteria, Candidates, Capability Assessment, Privacy, Cost |
| `docs/architecture/architectural_diagrams.md` | Markdown sections | ~1 | Diagram stubs |
| `docs/evaluation/evaluation_plan.md` | Markdown sections | ~2 | Evaluation plan sections |
| `docs/evaluation/prompt_eval_table.md` | Markdown sections | ~4 | test_planner cases, failure_analyzer cases, summary |
| `docs/requirements/user_stories.md` | Markdown sections | ~2 | User story sections |
| `docs/weekly_reports/week1/charter.md` | Markdown sections | ~6 | Problem, Users, AI Value, Scope, Assumptions, Constraints |
| `docs/weekly_reports/week1/week_1.md` | Markdown sections | ~3 | Work completed, decisions, plan |
| `docs/weekly_reports/week2/week_2.md` | Markdown sections | ~4 | Work completed, decisions, failures, plan |

### knowledge/ — Knowledge base

| File | Chunk strategy | Est. chunks | Notes |
|---|---|---|---|
| `knowledge/our_data.md` | Markdown sections / file | 0 | Skipped — file is empty |

---

## Summary

| Directory | Files scanned | Files indexed | Est. total chunks |
|---|---|---|---|
| `src/` | 7 | 3 | ~26 |
| `tests/` | 3 | 2 | ~21 |
| `prompts/` | 3 | 2 | ~10 |
| `docs/` | 8 | 8 | ~29 |
| `knowledge/` | 1 | 0 | 0 |
| **Total** | **22** | **15** | **~86** |

---

## Exclusions

| Pattern | Reason |
|---|---|
| `__pycache__` | Compiled bytecode — not source |
| `.pytest_cache` | Test runner cache — not source |
| `prompts/versions/` | Archive copies — duplicates current prompt content |
| Files < 40 chars | Below minimum chunk size — no useful retrieval signal |
| Empty files | `agents.py`, `tools.py`, `main.py`, `test_agent.py`, `our_data.md` skipped |

---

## Provenance Notes

- All indexed content is team-owned or team-created. No third-party or confidential data is included.
- `last_modified` timestamps are taken from filesystem `mtime` at index build time and stored as ISO 8601 UTC strings.
- Chunk IDs are deterministic SHA-256 hashes of `file_path::chunk_type::chunk_name::text[:64]`. Re-running `build_index()` upserts in place without duplicating unchanged chunks.
- To rebuild from scratch: call `build_index(force=True)`, which deletes and recreates the collection.
