"""
tests/test_rag.py — Unit tests for src/rag.py.

All ChromaDB and llm_client interactions are mocked so no real API calls or
disk writes are made. The module-level _chroma_client and _collection globals
are reset between tests to prevent state leakage.

Test cases:
  1.  build_index() — upserts chunks for a .py file with AST chunking.
  2.  build_index() — upserts chunks for a .md file with section chunking.
  3.  build_index() — skips files below minimum chunk size.
  4.  build_index() — skips excluded paths (__pycache__, versions).
  5.  build_index(force=True) — deletes and recreates the collection.
  6.  retrieve() — returns ranked results from a non-empty collection.
  7.  retrieve() — returns empty list with warning when collection is empty.
  8.  retrieve() — raises ValueError on empty query string.
  9.  _chunk_python() — produces function/class chunks from valid Python.
  10. _chunk_python() — falls back to file chunk on SyntaxError.
  11. _chunk_markdown() — splits on ## headings into section chunks.
  12. _chunk_markdown() — produces a single file chunk when no headings exist.
  13. _split_text() — returns text as-is when under the size limit.
  14. _split_text() — splits oversized text at paragraph boundaries.
  15. Chunk IDs are deterministic — same input always produces same ID.
"""

import importlib
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import src.rag as rag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_rag_globals():
    """Reset module-level ChromaDB state so each test starts clean."""
    rag._chroma_client = None
    rag._collection = None


def _make_mock_collection(count: int = 5):
    """Return a mock ChromaDB collection."""
    col = MagicMock()
    col.count.return_value = count
    col.query.return_value = {
        "documents": [["chunk text A", "chunk text B"]],
        "metadatas": [
            [
                {"file_path": "src/foo.py", "chunk_type": "function",
                 "chunk_name": "foo", "last_modified": "2026-09-14T00:00:00+00:00"},
                {"file_path": "docs/bar.md", "chunk_type": "section",
                 "chunk_name": "Overview", "last_modified": "2026-09-14T00:00:00+00:00"},
            ]
        ],
        "distances": [[0.12, 0.34]],
    }
    return col


def _make_mock_chroma_client(collection):
    """Return a mock chromadb.PersistentClient."""
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_globals():
    """Reset RAG module globals before and after every test."""
    _reset_rag_globals()
    yield
    _reset_rag_globals()


@pytest.fixture()
def tmp_corpus(tmp_path):
    """Create a minimal corpus directory tree in tmp_path."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "example.py").write_text(
        'def add(a, b):\n    """Add two numbers."""\n    return a + b\n\n'
        'def subtract(a, b):\n    return a - b\n',
        encoding="utf-8",
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "overview.md").write_text(
        "# Overview\n\nThis is the overview section.\n\n"
        "## Details\n\nThis is the details section with enough content.\n",
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: build_index() upserts chunks from a .py file
# ---------------------------------------------------------------------------

def test_build_index_indexes_python_file(tmp_corpus, monkeypatch):
    """build_index() calls upsert for each chunk extracted from a .py file."""
    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    monkeypatch.setattr(rag, "_PROJECT_ROOT", tmp_corpus)
    monkeypatch.setattr(
        rag, "_CRAWL_DIRS",
        [(tmp_corpus / "src", [".py"], True)],
    )

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.1] * 768):
        total = rag.build_index()

    assert total > 0
    assert collection.upsert.called


# ---------------------------------------------------------------------------
# Test 2: build_index() upserts chunks from a .md file
# ---------------------------------------------------------------------------

def test_build_index_indexes_markdown_file(tmp_corpus, monkeypatch):
    """build_index() calls upsert for each section chunk from a .md file."""
    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    monkeypatch.setattr(rag, "_PROJECT_ROOT", tmp_corpus)
    monkeypatch.setattr(
        rag, "_CRAWL_DIRS",
        [(tmp_corpus / "docs", [".md"], True)],
    )

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.2] * 768):
        total = rag.build_index()

    assert total > 0
    assert collection.upsert.called


# ---------------------------------------------------------------------------
# Test 3: build_index() skips files below the minimum chunk size
# ---------------------------------------------------------------------------

def test_build_index_skips_tiny_files(tmp_path, monkeypatch):
    """build_index() skips files whose content is below _MIN_CHUNK_CHARS."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "empty.py").write_text("", encoding="utf-8")
    (src_dir / "tiny.py").write_text("x = 1\n", encoding="utf-8")

    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    monkeypatch.setattr(rag, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rag, "_CRAWL_DIRS", [(src_dir, [".py"], True)])

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.0] * 768):
        total = rag.build_index()

    assert total == 0
    collection.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: build_index() skips excluded paths
# ---------------------------------------------------------------------------

def test_build_index_skips_excluded_paths(tmp_path, monkeypatch):
    """build_index() does not index files inside __pycache__ or versions/."""
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-311.pyc").write_text("fake bytecode" * 10, encoding="utf-8")

    versions_dir = tmp_path / "prompts" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "test_planner_v1.0.txt").write_text(
        "## Version: 1.0\n" + "x" * 200, encoding="utf-8"
    )

    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    monkeypatch.setattr(rag, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        rag, "_CRAWL_DIRS",
        [
            (cache_dir, [".py"], True),
            (versions_dir, [".txt"], True),
        ],
    )

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.0] * 768):
        total = rag.build_index()

    assert total == 0


# ---------------------------------------------------------------------------
# Test 5: build_index(force=True) deletes and recreates the collection
# ---------------------------------------------------------------------------

def test_build_index_force_deletes_collection(tmp_path, monkeypatch):
    """build_index(force=True) calls delete_collection before recreating."""
    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    monkeypatch.setattr(rag, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rag, "_CRAWL_DIRS", [])

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client):
        # First call to populate _collection
        rag.build_index()
        # Second call with force=True
        rag.build_index(force=True)

    mock_client.delete_collection.assert_called_once_with(rag._COLLECTION_NAME)


# ---------------------------------------------------------------------------
# Test 6: retrieve() returns ranked results from a non-empty collection
# ---------------------------------------------------------------------------

def test_retrieve_returns_ranked_results(monkeypatch):
    """retrieve() returns a list of dicts with the expected keys."""
    collection = _make_mock_collection(count=5)
    mock_client = _make_mock_chroma_client(collection)

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.5] * 768):
        results = rag.retrieve("how does embed work?", top_k=2)

    assert isinstance(results, list)
    assert len(results) == 2
    for r in results:
        assert "text" in r
        assert "file_path" in r
        assert "chunk_type" in r
        assert "chunk_name" in r
        assert "score" in r


# ---------------------------------------------------------------------------
# Test 7: retrieve() returns empty list when collection is empty
# ---------------------------------------------------------------------------

def test_retrieve_empty_collection_returns_empty_list(monkeypatch):
    """retrieve() returns [] and logs a warning when the collection is empty."""
    collection = _make_mock_collection(count=0)
    mock_client = _make_mock_chroma_client(collection)

    with patch("src.rag.chromadb.PersistentClient", return_value=mock_client), \
         patch("src.rag.llm_client.embed", return_value=[0.0] * 768):
        results = rag.retrieve("anything")

    assert results == []
    # embed should NOT have been called (no point embedding if collection empty)
    # — collection.count() check happens before embed call in retrieve()


# ---------------------------------------------------------------------------
# Test 8: retrieve() raises ValueError on empty query
# ---------------------------------------------------------------------------

def test_retrieve_empty_query_raises():
    """retrieve() raises ValueError when query is empty or whitespace."""
    with pytest.raises(ValueError, match="must not be empty"):
        rag.retrieve("")

    with pytest.raises(ValueError, match="must not be empty"):
        rag.retrieve("   ")


# ---------------------------------------------------------------------------
# Test 9: _chunk_python() produces function/class chunks
# ---------------------------------------------------------------------------

def test_chunk_python_produces_function_chunks():
    """_chunk_python() returns one chunk per top-level function/class."""
    source = (
        '"""Module docstring."""\n'
        "import os\n\n"
        "def foo():\n"
        '    """Foo does something."""\n'
        "    return 42\n\n"
        "class Bar:\n"
        '    """Bar class."""\n'
        "    def method(self):\n"
        "        pass\n"
    )
    chunks = rag._chunk_python(source, "src/example.py", "2026-09-14T00:00:00+00:00")

    types_found = {c["chunk_type"] for c in chunks}
    names_found = {c["chunk_name"] for c in chunks}

    assert "function" in types_found
    assert "class" in types_found
    assert "foo" in names_found
    assert "Bar" in names_found
    for c in chunks:
        assert c["file_path"] == "src/example.py"
        assert len(c["text"]) >= rag._MIN_CHUNK_CHARS


# ---------------------------------------------------------------------------
# Test 10: _chunk_python() falls back to file chunk on SyntaxError
# ---------------------------------------------------------------------------

def test_chunk_python_fallback_on_syntax_error():
    """_chunk_python() returns a single file-type chunk for invalid Python."""
    bad_source = "def foo(\n    # unclosed parenthesis\n" + "x" * 100
    chunks = rag._chunk_python(bad_source, "src/broken.py", "2026-09-14T00:00:00+00:00")

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "file"


# ---------------------------------------------------------------------------
# Test 11: _chunk_markdown() splits on ## headings
# ---------------------------------------------------------------------------

def test_chunk_markdown_splits_on_headings():
    """_chunk_markdown() returns one chunk per heading section."""
    md = (
        "# Top-level heading\n\n"
        "Intro paragraph with enough content to be indexed.\n\n"
        "## Section One\n\n"
        "Content of section one that is long enough to pass the minimum size check.\n\n"
        "## Section Two\n\n"
        "Content of section two that is also long enough to pass the minimum check.\n"
    )
    chunks = rag._chunk_markdown(md, "docs/guide.md", "2026-09-14T00:00:00+00:00")

    assert len(chunks) >= 2
    chunk_types = {c["chunk_type"] for c in chunks}
    assert "section" in chunk_types
    for c in chunks:
        assert c["file_path"] == "docs/guide.md"


# ---------------------------------------------------------------------------
# Test 12: _chunk_markdown() produces a file chunk when no headings exist
# ---------------------------------------------------------------------------

def test_chunk_markdown_no_headings_produces_file_chunk():
    """_chunk_markdown() returns a single file chunk for heading-free content."""
    plain = "This is a plain text file with no headings.\n" * 5
    chunks = rag._chunk_markdown(plain, "knowledge/notes.md", "2026-09-14T00:00:00+00:00")

    assert len(chunks) >= 1
    assert all(c["chunk_type"] == "file" for c in chunks)


# ---------------------------------------------------------------------------
# Test 13: _split_text() returns text as-is when under size limit
# ---------------------------------------------------------------------------

def test_split_text_short_text_unchanged():
    """_split_text() returns the original text in a single-element list."""
    short = "This is a short text that fits within the limit."
    result = rag._split_text(short)
    assert result == [short]


# ---------------------------------------------------------------------------
# Test 14: _split_text() splits oversized text at paragraph boundaries
# ---------------------------------------------------------------------------

def test_split_text_splits_long_text():
    """_split_text() splits text larger than _MAX_CHUNK_CHARS into parts."""
    # Create text larger than the limit using paragraph-separated chunks
    paragraph = "word " * 200  # ~1000 chars each
    big_text = ("\n\n" + paragraph) * 10  # ~10000 chars total
    parts = rag._split_text(big_text)

    assert len(parts) > 1
    for part in parts:
        assert len(part) <= rag._MAX_CHUNK_CHARS


# ---------------------------------------------------------------------------
# Test 15: Chunk IDs are deterministic
# ---------------------------------------------------------------------------

def test_chunk_ids_are_deterministic():
    """The same input always produces the same chunk ID."""
    chunk_a = rag._make_chunk("some text", "function", "my_func", "src/a.py", "2026-09-14")
    chunk_b = rag._make_chunk("some text", "function", "my_func", "src/a.py", "2026-09-14")
    assert chunk_a["id"] == chunk_b["id"]

    # Different text → different ID
    chunk_c = rag._make_chunk("different text", "function", "my_func", "src/a.py", "2026-09-14")
    assert chunk_a["id"] != chunk_c["id"]
