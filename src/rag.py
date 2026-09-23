"""
rag.py — RAG pipeline for the QA Agent.

Responsibilities:
  - Crawl src/, tests/, prompts/, docs/ for .py and .md files (and .txt for prompts).
  - Chunk each file by logical unit: Python functions/classes, Markdown sections.
  - Embed each chunk using Gemini text-embedding-004 via llm_client.embed().
  - Store chunks in a persistent ChromaDB collection with provenance metadata.
  - Expose retrieve(query, top_k) that returns ranked chunks with source paths.

ChromaDB collection name: "qa_agent_corpus"
Persistent storage:        evidence/chroma_db/  (auto-created)

Usage:
    from src.rag import build_index, retrieve

    build_index()                           # ingest / re-ingest corpus
    chunks = retrieve("how does embed work?", top_k=5)
    for c in chunks:
        print(c["file_path"], c["chunk_type"], c["text"][:120])
"""

import ast
import hashlib
import logging
import os
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

# Directories to crawl, relative to project root
_CRAWL_DIRS: list[tuple[Path, list[str]]] = [
    (_PROJECT_ROOT / "src",     [".py"]),
    (_PROJECT_ROOT / "tests",   [".py"]),
    (_PROJECT_ROOT / "prompts", [".txt", ".md"]),
    (_PROJECT_ROOT / "docs",    [".md"]),
    (_PROJECT_ROOT / "knowledge", [".md"]),
]

# Exclude patterns (substring match against full path)
_EXCLUDE_PATTERNS = ["__pycache__", ".pytest_cache", "versions"]

# ChromaDB persistent storage
_CHROMA_DIR = _PROJECT_ROOT / "evidence" / "chroma_db"
_COLLECTION_NAME = "qa_agent_corpus"

# Chunk size limits (characters) — keeps vectors within embedding model limits
_MAX_CHUNK_CHARS = 6000
_MIN_CHUNK_CHARS = 40          # ignore trivially small chunks (e.g. blank __init__)

# Module-level ChromaDB client — initialised lazily
_chroma_client: Optional[chromadb.ClientAPI] = None
_collection: Optional[chromadb.Collection] = None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build_index(force: bool = False) -> int:
    """Crawl the corpus, embed every chunk, and upsert into ChromaDB.

    This is idempotent: chunks are upserted by deterministic ID so re-running
    updates changed files without duplicating unchanged ones.

    Args:
        force: If True, delete and recreate the collection before ingesting.
               Useful when chunk boundaries or metadata schema change.

    Returns:
        Total number of chunks upserted.
    """
    collection = _get_collection(force=force)
    total = 0

    for directory, extensions in _CRAWL_DIRS:
        if not directory.is_dir():
            logger.debug("Skipping missing directory: %s", directory)
            continue

        for ext in extensions:
            for filepath in sorted(directory.rglob(f"*{ext}")):
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
                        "event": "rag_file_indexed",
                        "file": str(filepath.relative_to(_PROJECT_ROOT)),
                        "chunks": len(chunks),
                    },
                )

    logger.info(
        "Index build complete",
        extra={"event": "rag_index_complete", "total_chunks": total},
    )
    return total


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """Retrieve the top-k most relevant chunks for *query*.

    Args:
        query:  The natural-language search query.
        top_k:  Number of results to return (default 5).

    Returns:
        A list of dicts, each with keys:
          - ``text``        (str)  — the chunk text
          - ``file_path``   (str)  — path relative to project root
          - ``chunk_type``  (str)  — "function" | "class" | "section" | "file"
          - ``chunk_name``  (str)  — function/class/section name or file stem
          - ``score``       (float)— cosine distance (lower = more similar)

        Returns an empty list with a warning log when the collection is empty.

    Raises:
        RuntimeError: If the embedding call fails.
        ValueError:   If *query* is empty.
    """
    if not query or not query.strip():
        raise ValueError("Query must not be empty.")

    collection = _get_collection()

    # Guard: empty collection
    if collection.count() == 0:
        logger.warning(
            "RAG collection is empty — run build_index() first.",
            extra={"event": "rag_empty_collection"},
        )
        return []

    t0 = time.monotonic()
    query_vector = llm_client.embed(query)
    latency_ms = int((time.monotonic() - t0) * 1000)

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    docs      = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metadatas, distances):
        chunks.append(
            {
                "text":       doc,
                "file_path":  meta.get("file_path", ""),
                "chunk_type": meta.get("chunk_type", ""),
                "chunk_name": meta.get("chunk_name", ""),
                "score":      round(dist, 6),
            }
        )

    logger.info(
        "Retrieval complete",
        extra={
            "event": "rag_retrieve",
            "query_length": len(query),
            "top_k": top_k,
            "results": len(chunks),
            "embed_latency_ms": latency_ms,
        },
    )

    return chunks


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
    text = chunk["text"]
    vector = llm_client.embed(text)

    collection.upsert(
        ids=[chunk["id"]],
        embeddings=[vector],
        documents=[text],
        metadatas=[
            {
                "file_path":     chunk["file_path"],
                "chunk_type":    chunk["chunk_type"],
                "chunk_name":    chunk["chunk_name"],
                "last_modified": chunk["last_modified"],
            }
        ],
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _should_exclude(path: Path) -> bool:
    """Return True if *path* matches any exclusion pattern."""
    path_str = str(path)
    return any(pattern in path_str for pattern in _EXCLUDE_PATTERNS)


def _chunk_file(filepath: Path) -> list[dict]:
    """Dispatch to the appropriate chunker based on file extension."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read %s: %s", filepath, exc)
        return []

    if not text.strip():
        return []

    rel_path = str(filepath.relative_to(_PROJECT_ROOT))
    last_modified = datetime.fromtimestamp(
        filepath.stat().st_mtime, tz=timezone.utc
    ).isoformat()

    if filepath.suffix == ".py":
        return _chunk_python(text, rel_path, last_modified)
    else:
        # .md and .txt files — section-based chunking
        return _chunk_markdown(text, rel_path, last_modified)


def _chunk_python(text: str, rel_path: str, last_modified: str) -> list[dict]:
    """Chunk a Python file by top-level function and class definitions.

    Falls back to a single whole-file chunk when the file cannot be parsed
    (e.g. syntax errors) or contains no top-level definitions.
    """
    chunks = []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Unparseable — treat as a single section chunk
        return _make_chunks(
            [(text, "file", Path(rel_path).stem)], rel_path, last_modified
        )

    lines = text.splitlines(keepends=True)

    top_level = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and _is_top_level(node, tree)
    ]

    if not top_level:
        return _make_chunks(
            [(text, "file", Path(rel_path).stem)], rel_path, last_modified
        )

    # Sort by line number
    top_level.sort(key=lambda n: n.lineno)

    # Add a "module docstring / imports" chunk from the top of the file up to
    # the first definition
    preamble_end = top_level[0].lineno - 1
    preamble = "".join(lines[:preamble_end]).strip()
    if len(preamble) >= _MIN_CHUNK_CHARS:
        chunks.append(
            _make_chunk(preamble, "file", Path(rel_path).stem, rel_path, last_modified)
        )

    for node in top_level:
        node_lines = lines[node.lineno - 1 : node.end_lineno]
        node_text = "".join(node_lines).strip()

        if len(node_text) < _MIN_CHUNK_CHARS:
            continue

        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
        name = node.name

        # Split oversized chunks at the _MAX_CHUNK_CHARS boundary
        for sub in _split_text(node_text):
            chunks.append(
                _make_chunk(sub, chunk_type, name, rel_path, last_modified)
            )

    return chunks


def _chunk_markdown(text: str, rel_path: str, last_modified: str) -> list[dict]:
    """Chunk a Markdown or plain-text file by heading (##, ###) or blank lines.

    Each heading and its following content form one chunk. Files with no
    headings are split by double-newline paragraphs.
    """
    # Split on lines that start with one or more # characters
    sections = re.split(r"(?m)^(?=#{1,6} )", text)

    # If split produced only one part the text has no headings — use file fallback
    has_headings = len(sections) > 1

    raw_chunks: list[tuple[str, str, str]] = []

    if has_headings:
        for section in sections:
            section = section.strip()
            if len(section) < _MIN_CHUNK_CHARS:
                continue

            # Extract heading as the chunk name
            first_line = section.splitlines()[0]
            name = re.sub(r"^#+\s*", "", first_line).strip() or Path(rel_path).stem

            for sub in _split_text(section):
                raw_chunks.append((sub, "section", name))

    if not raw_chunks:
        # No headings (or all sections too small) — single chunk for whole file
        for sub in _split_text(text.strip()):
            raw_chunks.append((sub, "file", Path(rel_path).stem))

    return _make_chunks(raw_chunks, rel_path, last_modified)


# ---------------------------------------------------------------------------
# Low-level chunk construction helpers
# ---------------------------------------------------------------------------

def _is_top_level(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if *node* is a direct child of the module body."""
    return node in tree.body


def _split_text(text: str) -> list[str]:
    """Split *text* into sub-chunks of at most _MAX_CHUNK_CHARS characters.

    Splits on paragraph boundaries (double newline) where possible.
    """
    if len(text) <= _MAX_CHUNK_CHARS:
        return [text]

    parts = []
    current = ""
    for paragraph in re.split(r"\n{2,}", text):
        if len(current) + len(paragraph) + 2 <= _MAX_CHUNK_CHARS:
            current = (current + "\n\n" + paragraph).lstrip()
        else:
            if current:
                parts.append(current)
            # Hard-split oversized single paragraphs
            while len(paragraph) > _MAX_CHUNK_CHARS:
                parts.append(paragraph[:_MAX_CHUNK_CHARS])
                paragraph = paragraph[_MAX_CHUNK_CHARS:]
            current = paragraph
    if current:
        parts.append(current)
    return parts or [text[:_MAX_CHUNK_CHARS]]


def _make_chunk(
    text: str,
    chunk_type: str,
    chunk_name: str,
    file_path: str,
    last_modified: str,
) -> dict:
    """Build a chunk dict with a deterministic ID."""
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
    """Convert a list of (text, chunk_type, chunk_name) tuples into chunk dicts."""
    return [
        _make_chunk(text, chunk_type, name, file_path, last_modified)
        for text, chunk_type, name in raw
        if len(text) >= _MIN_CHUNK_CHARS
    ]
