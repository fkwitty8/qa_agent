"""
rag.py — RAG pipeline for the QA Agent.

Responsibilities:
  - Crawl src/, tests/, prompts/, docs/ for .py and .md files (and .txt for prompts).
  - Chunk each file by logical unit: Python functions/classes, Markdown sections.
  - Embed each chunk using Gemini text-embedding-004 via llm_client.embed().
  - Store chunks in a persistent ChromaDB collection with provenance metadata.
  - Expose retrieve(query, top_k) that returns ranked chunks with source paths.
  - Support indexing external codebases (any language) via index_external_codebase().

ChromaDB collection name: "qa_agent_corpus"
Persistent storage:        evidence/chroma_db/  (auto-created)

Usage:
    from src.rag import build_index, retrieve, index_external_codebase

    build_index()                           # ingest / re-ingest own corpus
    index_external_codebase(               # ingest any external project
        root="C:/path/to/other-project",
        label="meeting-planner",
        extensions=[".java", ".md"],
    )
    chunks = retrieve("how does Calendar work?", top_k=5)
    for c in chunks:
        print(c["file_path"], c["chunk_type"], c["text"][:120])
"""

import ast
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from src import llm_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Project root — one level above this file (src/)
_PROJECT_ROOT = Path(__file__).parent.parent

# Directories to crawl — each entry is (path, extensions, recursive).
# recursive=False limits the crawl to the top level of that directory only.
_CRAWL_DIRS: list[tuple[Path, list[str], bool]] = [
    (_PROJECT_ROOT,               [".md"],         False),  # root .md only
    (_PROJECT_ROOT / "src",       [".py"],         True),
    (_PROJECT_ROOT / "tests",     [".py"],         True),
    (_PROJECT_ROOT / "prompts",   [".txt", ".md"], True),
    (_PROJECT_ROOT / "docs",      [".md"],         True),
    (_PROJECT_ROOT / "knowledge", [".md"],         True),
]

# Exclude patterns (substring match against full path)
_EXCLUDE_PATTERNS = ["__pycache__", ".pytest_cache", "versions"]

# ChromaDB persistent storage
_CHROMA_DIR      = _PROJECT_ROOT / "evidence" / "chroma_db"
_COLLECTION_NAME = "qa_agent_corpus"

# Chunk size limits (characters)
_MAX_CHUNK_CHARS = 6000
_MIN_CHUNK_CHARS = 40

# FC-01: filter out low-relevance results
_MAX_SIMILARITY_DISTANCE = 0.7

# Module-level ChromaDB client — initialised lazily
_chroma_client: Optional[chromadb.ClientAPI] = None
_collection:    Optional[chromadb.Collection] = None

# Module-level RAG retrieval cache: query string -> retrieved chunks
_retrieve_cache: dict[str, list[dict]] = {}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build_index(force: bool = False) -> int:
    """Crawl the own corpus, embed every chunk, and upsert into ChromaDB.

    Idempotent — re-running updates changed chunks in place.

    Args:
        force: Delete and recreate the collection before ingesting.

    Returns:
        Total number of chunks upserted.
    """
    if force:
        _invalidate_retrieve_cache()
    collection = _get_collection(force=force)
    total = 0

    for directory, extensions, recursive in _CRAWL_DIRS:
        if not directory.is_dir():
            logger.debug("Skipping missing directory: %s", directory)
            continue

        for ext in extensions:
            pattern   = f"*{ext}"
            file_iter = sorted(
                directory.rglob(pattern) if recursive else directory.glob(pattern)
            )
            for filepath in file_iter:
                if _should_exclude(filepath):
                    continue

                chunks = _chunk_file(filepath)
                if not chunks:
                    continue

                for chunk in chunks:
                    _upsert_chunk(collection, chunk)
                    total += 1

                logger.info(
                    "Indexed file",
                    extra={
                        "event":  "rag_file_indexed",
                        "file":   str(filepath.relative_to(_PROJECT_ROOT)),
                        "chunks": len(chunks),
                    },
                )

    logger.info(
        "Index build complete",
        extra={"event": "rag_index_complete", "total_chunks": total},
    )
    return total


def index_external_codebase(
    root: str | Path,
    label: str = "",
    extensions: list[str] | None = None,
) -> int:
    """Index an external codebase into the same ChromaDB collection.

    Works with any language. Each chunk's ``file_path`` is prefixed with
    ``[label]/`` so retrieval results clearly show which codebase they came
    from, e.g. ``[meeting-planner]/src/main/java/.../Calendar.java``.

    Idempotent — re-running updates changed chunks in place.

    Args:
        root:       Path to the root of the external project. Must be a
                    directory. Can be anywhere on the filesystem.
        label:      Short name for this codebase, e.g. ``"meeting-planner"``.
                    Defaults to the directory name.
        extensions: File extensions to crawl. Defaults to
                    ``[".py", ".java", ".js", ".ts", ".md", ".txt"]``.

    Returns:
        Number of chunks upserted.

    Raises:
        ValueError: If *root* does not exist or is not a directory.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise ValueError(
            f"Path not found: {root_path}\n"
            f"Tip: enter the root folder of the project, not a single file."
        )
    if not root_path.is_dir():
        raise ValueError(
            f"Path is a file, not a folder: {root_path}\n"
            f"Tip: enter the root folder of the project. "
            f"For example: {root_path.parent}"
        )

    label = label.strip() or root_path.name
    exts  = extensions or [".py", ".java", ".js", ".ts", ".md", ".txt"]

    collection = _get_collection()
    total      = 0

    for ext in exts:
        for filepath in sorted(root_path.rglob(f"*{ext}")):
            if _should_exclude(filepath):
                continue

            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.warning("Could not read %s: %s", filepath, exc)
                continue

            if not text.strip():
                continue

            # Labelled relative path  →  [meeting-planner]/src/Calendar.java
            try:
                rel = filepath.relative_to(root_path)
            except ValueError:
                rel = filepath
            labelled_path = f"[{label}]/{rel}".replace("\\", "/")

            last_modified = datetime.fromtimestamp(
                filepath.stat().st_mtime, tz=timezone.utc
            ).isoformat()

            # Chunk strategy: Python gets AST chunking; everything else gets
            # section/paragraph chunking (works well for Java, JS, Markdown)
            if filepath.suffix == ".py":
                chunks = _chunk_python(text, labelled_path, last_modified)
            else:
                chunks = _chunk_text(text, labelled_path, last_modified)

            for chunk in chunks:
                _upsert_chunk(collection, chunk)
                total += 1

            if chunks:
                logger.info(
                    "Indexed external file",
                    extra={
                        "event":  "rag_external_indexed",
                        "label":  label,
                        "file":   labelled_path,
                        "chunks": len(chunks),
                    },
                )

    _invalidate_retrieve_cache()
    logger.info(
        "External index complete",
        extra={"event": "rag_external_complete", "label": label, "total_chunks": total},
    )
    return total


def list_indexed_codebases() -> list[str]:
    """Return the distinct codebase labels currently in the collection.

    Returns a sorted list where built-in chunks are labelled ``"built-in"``
    and external codebases appear as ``"[label]"``.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []

    results = collection.get(include=["metadatas"])
    labels: set[str] = set()
    for meta in results.get("metadatas", []):
        fp = meta.get("file_path", "")
        if fp.startswith("["):
            lbl = fp.split("]")[0].lstrip("[")
            labels.add(f"[{lbl}]")
        else:
            labels.add("built-in")
    return sorted(labels)


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Retrieve the top-k most relevant chunks for *query*.

    Args:
        query:  The natural-language search query.
        top_k:  Number of results to return (default 5).

    Returns:
        A list of dicts with keys: text, file_path, chunk_type, chunk_name, score.
        Returns [] with a warning when the collection is empty.

    Raises:
        ValueError: If *query* is empty.
    """
    if not query or not query.strip():
        raise ValueError("Query must not be empty.")

    # Check RAG cache
    if query in _retrieve_cache:
        logger.debug("RAG cache hit", extra={"event": "rag_cache_hit", "query_length": len(query)})
        return _retrieve_cache[query]

    collection = _get_collection()

    if collection.count() == 0:
        logger.warning(
            "RAG collection is empty — run build_index() first.",
            extra={"event": "rag_empty_collection"},
        )
        return []

    t0 = time.monotonic()
    query_vector = llm_client.embed(query)
    latency_ms   = int((time.monotonic() - t0) * 1000)

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    chunks    = []
    docs      = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas",  [[]])[0]
    distances = results.get("distances",  [[]])[0]

    for doc, meta, dist in zip(docs, metadatas, distances):
        if dist > _MAX_SIMILARITY_DISTANCE:
            continue  # FC-01: filter low-relevance results
        chunks.append({
            "text":       doc,
            "file_path":  meta.get("file_path", ""),
            "chunk_type": meta.get("chunk_type", ""),
            "chunk_name": meta.get("chunk_name", ""),
            "score":      round(dist, 6),
        })

    if not chunks:
        logger.warning(
            "All retrieved chunks below similarity threshold (distance > %.2f).",
            _MAX_SIMILARITY_DISTANCE,
            extra={"event": "rag_no_relevant_context"},
        )

    logger.info(
        "Retrieval complete",
        extra={
            "event":            "rag_retrieve",
            "query_length":     len(query),
            "top_k":            top_k,
            "results":          len(chunks),
            "embed_latency_ms": latency_ms,
        },
    )
    _retrieve_cache[query] = chunks
    return chunks


def _invalidate_retrieve_cache() -> None:
    """Clear the retrieval cache (called after index changes)."""
    _retrieve_cache.clear()
    logger.debug("RAG cache invalidated", extra={"event": "rag_cache_invalidated"})


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def _get_collection(force: bool = False) -> chromadb.Collection:
    """Return (or lazily create) the persistent ChromaDB collection."""
    global _chroma_client, _collection

    if _chroma_client is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        logger.debug("ChromaDB client initialised at %s", _CHROMA_DIR)

    if force and _collection is not None:
        try:
            _chroma_client.delete_collection(_COLLECTION_NAME)
        except Exception:
            pass
        _collection = None

    if _collection is None:
        _collection = _chroma_client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.debug(
            "Using collection '%s' (%d chunks)",
            _COLLECTION_NAME, _collection.count(),
        )

    return _collection


def _upsert_chunk(collection: chromadb.Collection, chunk: dict) -> None:
    """Embed *chunk* and upsert it into *collection*."""
    vector = llm_client.embed(chunk["text"])
    collection.upsert(
        ids=[chunk["id"]],
        embeddings=[vector],
        documents=[chunk["text"]],
        metadatas=[{
            "file_path":     chunk["file_path"],
            "chunk_type":    chunk["chunk_type"],
            "chunk_name":    chunk["chunk_name"],
            "last_modified": chunk["last_modified"],
        }],
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _should_exclude(path: Path) -> bool:
    """Return True if *path* matches any exclusion pattern."""
    path_str = str(path)
    return any(pattern in path_str for pattern in _EXCLUDE_PATTERNS)


def _chunk_file(filepath: Path) -> list[dict]:
    """Dispatch to the appropriate chunker for built-in corpus files."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read %s: %s", filepath, exc)
        return []

    if not text.strip():
        return []

    rel_path      = str(filepath.relative_to(_PROJECT_ROOT))
    last_modified = datetime.fromtimestamp(
        filepath.stat().st_mtime, tz=timezone.utc
    ).isoformat()

    if filepath.suffix == ".py":
        return _chunk_python(text, rel_path, last_modified)
    return _chunk_markdown(text, rel_path, last_modified)


def _chunk_python(text: str, rel_path: str, last_modified: str) -> list[dict]:
    """Chunk a Python file by top-level function and class definitions.

    Falls back to a single whole-file chunk on SyntaxError.
    """
    chunks = []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _make_chunks(
            [(text, "file", Path(rel_path).stem)], rel_path, last_modified
        )

    lines     = text.splitlines(keepends=True)
    top_level = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and _is_top_level(node, tree)
    ]

    if not top_level:
        return _make_chunks(
            [(text, "file", Path(rel_path).stem)], rel_path, last_modified
        )

    top_level.sort(key=lambda n: n.lineno)

    preamble_end = top_level[0].lineno - 1
    preamble     = "".join(lines[:preamble_end]).strip()
    if len(preamble) >= _MIN_CHUNK_CHARS:
        chunks.append(
            _make_chunk(preamble, "file", Path(rel_path).stem, rel_path, last_modified)
        )

    for node in top_level:
        node_text = "".join(lines[node.lineno - 1 : node.end_lineno]).strip()
        if len(node_text) < _MIN_CHUNK_CHARS:
            continue
        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
        for sub in _split_text(node_text):
            chunks.append(
                _make_chunk(sub, chunk_type, node.name, rel_path, last_modified)
            )

    return chunks


def _chunk_markdown(text: str, rel_path: str, last_modified: str) -> list[dict]:
    """Chunk a Markdown or text file by headings; falls back to file chunk."""
    return _chunk_text(text, rel_path, last_modified)


def _chunk_text(text: str, rel_path: str, last_modified: str) -> list[dict]:
    """General-purpose chunker for any text file (Markdown, Java, JS, etc.).

    Splits on Markdown headings if present, otherwise on double newlines
    (blank lines between methods / blocks). This works reasonably well for
    most structured source files even without language-specific parsing.
    """
    sections    = re.split(r"(?m)^(?=#{1,6} )", text)
    has_headings = len(sections) > 1
    raw_chunks: list[tuple[str, str, str]] = []

    if has_headings:
        for section in sections:
            section = section.strip()
            if len(section) < _MIN_CHUNK_CHARS:
                continue
            first_line = section.splitlines()[0]
            name       = re.sub(r"^#+\s*", "", first_line).strip() or Path(rel_path).stem
            for sub in _split_text(section):
                raw_chunks.append((sub, "section", name))

    if not raw_chunks:
        # No headings — split on blank lines (works for Java class bodies etc.)
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if len(paragraphs) > 1:
            for para in paragraphs:
                if len(para) >= _MIN_CHUNK_CHARS:
                    # Use first non-empty line as chunk name
                    name = para.splitlines()[0].strip()[:60] or Path(rel_path).stem
                    for sub in _split_text(para):
                        raw_chunks.append((sub, "section", name))

    if not raw_chunks:
        for sub in _split_text(text.strip()):
            raw_chunks.append((sub, "file", Path(rel_path).stem))

    return _make_chunks(raw_chunks, rel_path, last_modified)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _is_top_level(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if *node* is a direct child of the module body."""
    return node in tree.body


def _split_text(text: str) -> list[str]:
    """Split *text* into sub-chunks of at most _MAX_CHUNK_CHARS each."""
    if len(text) <= _MAX_CHUNK_CHARS:
        return [text]

    parts, current = [], ""
    for paragraph in re.split(r"\n{2,}", text):
        if len(current) + len(paragraph) + 2 <= _MAX_CHUNK_CHARS:
            current = (current + "\n\n" + paragraph).lstrip()
        else:
            if current:
                parts.append(current)
            while len(paragraph) > _MAX_CHUNK_CHARS:
                parts.append(paragraph[:_MAX_CHUNK_CHARS])
                paragraph = paragraph[_MAX_CHUNK_CHARS:]
            current = paragraph
    if current:
        parts.append(current)
    return parts or [text[:_MAX_CHUNK_CHARS]]


def _make_chunk(
    text: str, chunk_type: str, chunk_name: str,
    file_path: str, last_modified: str,
) -> dict:
    """Build a chunk dict with a deterministic SHA-256 ID."""
    chunk_id = hashlib.sha256(
        f"{file_path}::{chunk_type}::{chunk_name}::{text[:64]}".encode()
    ).hexdigest()[:16]
    return {
        "id":            chunk_id,
        "text":          text,
        "file_path":     file_path,
        "chunk_type":    chunk_type,
        "chunk_name":    chunk_name,
        "last_modified": last_modified,
    }


def _make_chunks(
    raw: list[tuple[str, str, str]],
    file_path: str,
    last_modified: str,
) -> list[dict]:
    """Convert (text, chunk_type, chunk_name) tuples into chunk dicts."""
    return [
        _make_chunk(text, chunk_type, name, file_path, last_modified)
        for text, chunk_type, name in raw
        if len(text) >= _MIN_CHUNK_CHARS
    ]
