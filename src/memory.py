"""
memory.py — Persistent memory for the QA Agent.

Provides a SQLite-backed store for run history and drafted issues.
All access is local-only (file path in evidence/qa_agent.db).
No PII is stored — only agent-generated content and goal strings.

Tables:
  runs(run_id, timestamp, goal, trace_json, status)
  drafted_issues(issue_id, run_id, title, body, github_url, submitted_at)

Usage:
    from src.memory import MemoryStore
    mem = MemoryStore()
    mem.save_run(run_id, goal, trace, status)
    runs = mem.list_runs()           # most recent first
    run  = mem.get_run(run_id)       # None if not found
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "evidence" / "qa_agent.db"

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    goal        TEXT NOT NULL,
    trace_json  TEXT NOT NULL,
    status      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafted_issues (
    issue_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    github_url   TEXT,
    submitted_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


class MemoryStore:
    """SQLite-backed persistent memory store.

    Args:
        db_path: Path to the SQLite database file.
                 Defaults to evidence/qa_agent.db.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript(_DDL)

    @contextmanager
    def _conn(self):
        """Yield a SQLite connection with row_factory set."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    def save_run(
        self,
        run_id: str,
        goal: str,
        trace: list,
        status: str,
    ) -> None:
        """Persist a completed agent run.

        Args:
            run_id: Unique run identifier.
            goal:   The original goal string.
            trace:  The list of step dicts from QAAgent.run().
            status: "passed" | "failed" | "handoff" | "error"
        """
        trace_json = json.dumps(trace, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, timestamp, goal, trace_json, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, self._now(), goal, trace_json, status),
            )
        logger.debug("Saved run %s (status=%s)", run_id, status)

    def get_run(self, run_id: str) -> Optional[dict]:
        """Return a single run by ID, or None if not found.

        Returns:
            dict with keys: run_id, timestamp, goal, trace, status
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id":    row["run_id"],
            "timestamp": row["timestamp"],
            "goal":      row["goal"],
            "trace":     json.loads(row["trace_json"]),
            "status":    row["status"],
        }

    def list_runs(self) -> list[dict]:
        """Return all runs, most recent first.

        Returns:
            list of dicts with keys: run_id, timestamp, goal, status
            (trace is omitted for performance — use get_run() to load it)
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, timestamp, goal, status FROM runs "
                "ORDER BY timestamp DESC"
            ).fetchall()
        return [
            {
                "run_id":    r["run_id"],
                "timestamp": r["timestamp"],
                "goal":      r["goal"],
                "status":    r["status"],
            }
            for r in rows
        ]

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its associated drafted issues.

        Returns True if a row was deleted, False if run_id was not found.
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM drafted_issues WHERE run_id = ?", (run_id,)
            )
            cursor = conn.execute(
                "DELETE FROM runs WHERE run_id = ?", (run_id,)
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Drafted issues
    # ------------------------------------------------------------------

    def save_draft(
        self,
        issue_id: str,
        run_id: str,
        title: str,
        body: str,
    ) -> None:
        """Persist a drafted (not yet posted) GitHub issue.

        Args:
            issue_id: Unique issue identifier (caller-generated).
            run_id:   The run that produced this draft.
            title:    Issue title.
            body:     Issue markdown body.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drafted_issues
                    (issue_id, run_id, title, body, github_url, submitted_at)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (issue_id, run_id, title, body),
            )
        logger.debug("Saved draft issue %s for run %s", issue_id, run_id)

    def update_draft_url(self, issue_id: str, github_url: str) -> bool:
        """Record the GitHub URL after an issue is approved and posted.

        Returns True if the issue was found and updated, False otherwise.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE drafted_issues
                SET github_url = ?, submitted_at = ?
                WHERE issue_id = ?
                """,
                (github_url, self._now(), issue_id),
            )
        return cursor.rowcount > 0

    def get_draft(self, issue_id: str) -> Optional[dict]:
        """Return a single drafted issue by ID, or None if not found.

        Returns:
            dict with keys: issue_id, run_id, title, body,
                            github_url, submitted_at
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM drafted_issues WHERE issue_id = ?", (issue_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_drafts_for_run(self, run_id: str) -> list[dict]:
        """Return all drafted issues associated with *run_id*."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM drafted_issues WHERE run_id = ? "
                "ORDER BY submitted_at DESC",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]
