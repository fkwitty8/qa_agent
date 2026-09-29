# Architectural Diagrams

---

## RAG Pipeline Architecture

```mermaid
flowchart TD
    subgraph INGEST["🔄 Index Build  —  build_index()"]
        direction TB
        C1["src/ *.py"] & C2["tests/ *.py"] & C3["prompts/ *.txt"] & C4["docs/ *.md"] & C5["knowledge/ *.md"]
        C1 & C2 & C3 & C4 & C5 --> CRAWL["File Crawler\n_chunk_file()"]

        CRAWL --> PY["Python Chunker\n_chunk_python()\nAST → function / class / preamble"]
        CRAWL --> MD["Markdown Chunker\n_chunk_markdown()\nHeading sections / file fallback"]

        PY & MD --> META["Provenance Metadata\nfile_path · chunk_type\nchunk_name · last_modified"]
        META --> EMBED1["Gemini Embeddings\ntext-embedding-004\n768-dim vector"]
        EMBED1 --> UPSERT["ChromaDB Upsert\nDeterministic SHA-256 ID\nevidence/chroma_db/"]
    end

    subgraph RETRIEVE["🔍 Retrieval  —  retrieve(query, top_k)"]
        direction TB
        QUERY["Natural-language Query"] --> EMBED2["Gemini Embeddings\ntext-embedding-004\n768-dim vector"]
        EMBED2 --> SEARCH["ChromaDB Query\nCosine Similarity\nHNSW index"]
        SEARCH --> RANK["Ranked Chunks\nscore · file_path\nchunk_type · chunk_name · text"]
        RANK --> GUARD{"Collection\nempty?"}
        GUARD -- "yes" --> EMPTY["Return []\n+ warning log"]
        GUARD -- "no" --> RESULTS["Top-k Results\nto Agent / Tool"]
    end

    UPSERT -.->|"persistent store"| SEARCH

    subgraph EXCLUDE["🚫 Exclusions"]
        direction LR
        E1["__pycache__"] & E2[".pytest_cache"] & E3["prompts/versions/"] & E4["files < 40 chars"]
    end

    CRAWL -.->|"filtered out"| EXCLUDE
```

**Key design decisions:**
- **AST chunking for Python** — keeps each function and class self-contained rather than splitting mid-definition.
- **Heading-based chunking for Markdown** — one section per chunk gives the retriever a coherent semantic unit.
- **Deterministic SHA-256 IDs** — `build_index()` is fully idempotent; re-running updates changed chunks in place.
- **Empty-collection guard** — `retrieve()` returns `[]` with a warning rather than raising, so the agent loop handles it gracefully.
- **Exclusion list** — `__pycache__`, `.pytest_cache`, `prompts/versions/`, and sub-40-char files are skipped to avoid noise.
