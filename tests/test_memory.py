"""
tests/test_memory.py — Unit tests for src/memory.py.

All tests use a temporary SQLite database so no real evidence/qa_agent.db
is touched. The MemoryStore is re-created per test via a tmp_path fixture.

Test cases:
  1.  save_run() + get_run() — round-trip returns correct fields.
  2.  get_run() returns None for an unknown run_id.
  3.  list_runs() returns runs in reverse-chronological order.
  4.  list_runs() returns empty list when no runs exist.
  5.  list_runs() omits trace (performance — use get_run for trace).
  6.  save_run() with INSERT OR REPLACE — updates an existing run.
  7.  save_draft() + get_draft() — round-trip returns correct fields.
  8.  get_draft() returns None for an unknown issue_id.
  9.  update_draft_url() sets github_url and submitted_at.
  10. update_draft_url() returns False for unknown issue_id.
  11. get_drafts_for_run() returns all drafts for a run.
  12. get_drafts_for_run() returns empty list when no drafts exist.
  13. delete_run() removes run and its associated drafted issues.
  14. delete_run() returns False for unknown run_id.
  15. MemoryStore creates the database file and tables on init.
"""

import json
import time
from pathlib import Path

import pytest

from src.memory import MemoryStore

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem(tmp_path):
    """Return a MemoryStore backed by a temp SQLite file."""
    return MemoryStore(db_path=tmp_path / "test_qa.db")


SAMPLE_TRACE = [
    {"step": "SENSE", "tool": "rag.retrieve", "input_summary": "test goal",
     "output_summary": "3 chunks", "decision": "proceed", "timestamp_utc": "2026-10-09T10:00:00Z"},
    {"step": "PLAN", "tool": "propose_tests", "input_summary": "test goal",
     "output_summary": "test_cases=2", "decision": "2 proposed", "timestamp_utc": "2026-10-09T10:00:02Z"},
]


# ---------------------------------------------------------------------------
# Test 1: save_run() + get_run() round-trip
# ---------------------------------------------------------------------------

def test_save_and_get_run(mem):
    """get_run() returns all fields saved by save_run()."""
    mem.save_run("abc123", "test the RAG module", SAMPLE_TRACE, "passed")
    result = mem.get_run("abc123")

    assert result is not None
    assert result["run_id"] == "abc123"
    assert result["goal"] == "test the RAG module"
    assert result["status"] == "passed"
    assert isinstance(result["trace"], list)
    assert len(result["trace"]) == 2
    assert result["trace"][0]["step"] == "SENSE"
    assert "timestamp" in result


# ---------------------------------------------------------------------------
# Test 2: get_run() returns None for unknown run_id
# ---------------------------------------------------------------------------

def test_get_run_returns_none_for_missing(mem):
    """get_run() returns None when run_id does not exist."""
    assert mem.get_run("nonexistent") is None


# ---------------------------------------------------------------------------
# Test 3: list_runs() returns runs in reverse-chronological order
# ---------------------------------------------------------------------------

def test_list_runs_reverse_chronological(mem):
    """list_runs() returns runs newest-first."""
    mem.save_run("run1", "goal one", [], "passed")
    time.sleep(0.01)   # ensure distinct timestamps
    mem.save_run("run2", "goal two", [], "handoff")
    time.sleep(0.01)
    mem.save_run("run3", "goal three", [], "error")

    runs = mem.list_runs()
    assert len(runs) == 3
    assert runs[0]["run_id"] == "run3"   # most recent
    assert runs[1]["run_id"] == "run2"
    assert runs[2]["run_id"] == "run1"   # oldest


# ---------------------------------------------------------------------------
# Test 4: list_runs() returns empty list when no runs
# ---------------------------------------------------------------------------

def test_list_runs_empty(mem):
    """list_runs() returns [] when no runs have been saved."""
    assert mem.list_runs() == []


# ---------------------------------------------------------------------------
# Test 5: list_runs() omits trace field
# ---------------------------------------------------------------------------

def test_list_runs_omits_trace(mem):
    """list_runs() results contain run_id, timestamp, goal, status — not trace."""
    mem.save_run("r1", "goal", SAMPLE_TRACE, "passed")
    runs = mem.list_runs()
    assert "trace" not in runs[0]
    assert set(runs[0].keys()) == {"run_id", "timestamp", "goal", "status"}


# ---------------------------------------------------------------------------
# Test 6: save_run() INSERT OR REPLACE updates existing run
# ---------------------------------------------------------------------------

def test_save_run_replace_updates(mem):
    """save_run() with the same run_id replaces the existing record."""
    mem.save_run("r1", "original goal", [], "passed")
    mem.save_run("r1", "updated goal", SAMPLE_TRACE, "handoff")

    result = mem.get_run("r1")
    assert result["goal"] == "updated goal"
    assert result["status"] == "handoff"
    assert len(result["trace"]) == 2


# ---------------------------------------------------------------------------
# Test 7: save_draft() + get_draft() round-trip
# ---------------------------------------------------------------------------

def test_save_and_get_draft(mem):
    """get_draft() returns all fields saved by save_draft()."""
    mem.save_run("r1", "goal", [], "passed")
    mem.save_draft("issue1", "r1", "Bug: retrieve raises", "## Summary\n\nA bug.")

    draft = mem.get_draft("issue1")
    assert draft is not None
    assert draft["issue_id"] == "issue1"
    assert draft["run_id"] == "r1"
    assert draft["title"] == "Bug: retrieve raises"
    assert "## Summary" in draft["body"]
    assert draft["github_url"] is None
    assert draft["submitted_at"] is None


# ---------------------------------------------------------------------------
# Test 8: get_draft() returns None for unknown issue_id
# ---------------------------------------------------------------------------

def test_get_draft_returns_none_for_missing(mem):
    """get_draft() returns None when issue_id does not exist."""
    assert mem.get_draft("ghost") is None


# ---------------------------------------------------------------------------
# Test 9: update_draft_url() sets url and submitted_at
# ---------------------------------------------------------------------------

def test_update_draft_url(mem):
    """update_draft_url() populates github_url and submitted_at."""
    mem.save_run("r1", "goal", [], "passed")
    mem.save_draft("issue1", "r1", "Title", "Body")

    result = mem.update_draft_url("issue1", "https://github.com/org/repo/issues/7")
    assert result is True

    draft = mem.get_draft("issue1")
    assert draft["github_url"] == "https://github.com/org/repo/issues/7"
    assert draft["submitted_at"] is not None


# ---------------------------------------------------------------------------
# Test 10: update_draft_url() returns False for unknown issue_id
# ---------------------------------------------------------------------------

def test_update_draft_url_returns_false_for_missing(mem):
    """update_draft_url() returns False when issue_id does not exist."""
    assert mem.update_draft_url("ghost", "https://github.com/x") is False


# ---------------------------------------------------------------------------
# Test 11: get_drafts_for_run() returns all drafts for a run
# ---------------------------------------------------------------------------

def test_get_drafts_for_run(mem):
    """get_drafts_for_run() returns all drafts associated with a run_id."""
    mem.save_run("r1", "goal", [], "handoff")
    mem.save_draft("i1", "r1", "Title 1", "Body 1")
    mem.save_draft("i2", "r1", "Title 2", "Body 2")

    drafts = mem.get_drafts_for_run("r1")
    assert len(drafts) == 2
    issue_ids = {d["issue_id"] for d in drafts}
    assert {"i1", "i2"} == issue_ids


# ---------------------------------------------------------------------------
# Test 12: get_drafts_for_run() returns [] when no drafts exist
# ---------------------------------------------------------------------------

def test_get_drafts_for_run_empty(mem):
    """get_drafts_for_run() returns [] when no drafts exist for that run."""
    mem.save_run("r1", "goal", [], "passed")
    assert mem.get_drafts_for_run("r1") == []


# ---------------------------------------------------------------------------
# Test 13: delete_run() removes run and associated drafts
# ---------------------------------------------------------------------------

def test_delete_run_removes_run_and_drafts(mem):
    """delete_run() deletes the run row and its drafted_issues rows."""
    mem.save_run("r1", "goal", [], "passed")
    mem.save_draft("i1", "r1", "Title", "Body")

    result = mem.delete_run("r1")
    assert result is True
    assert mem.get_run("r1") is None
    assert mem.get_draft("i1") is None


# ---------------------------------------------------------------------------
# Test 14: delete_run() returns False for unknown run_id
# ---------------------------------------------------------------------------

def test_delete_run_returns_false_for_missing(mem):
    """delete_run() returns False when run_id does not exist."""
    assert mem.delete_run("nonexistent") is False


# ---------------------------------------------------------------------------
# Test 15: MemoryStore creates the DB file on init
# ---------------------------------------------------------------------------

def test_memory_store_creates_db_file(tmp_path):
    """MemoryStore creates the SQLite file and tables on initialisation."""
    db_path = tmp_path / "new_subdir" / "qa.db"
    assert not db_path.exists()

    mem = MemoryStore(db_path=db_path)

    assert db_path.exists()
    # Verify both tables exist by querying sqlite_master
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "runs" in tables
    assert "drafted_issues" in tables
