from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job_sequence ON events(job_id, sequence);
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        job["request"] = json.loads(job.pop("request_json"))
        raw_result = job.pop("result_json")
        job["result"] = json.loads(raw_result) if raw_result else None
        return job

    def create(self, job_id: str, request: dict[str, Any]) -> dict[str, Any]:
        created = now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO jobs(id,status,created_at,request_json) VALUES(?,?,?,?)",
                (job_id, "queued", created, json.dumps(request, sort_keys=True)),
            )
            connection.execute(
                "INSERT INTO events(job_id,created_at,level,message) VALUES(?,?,?,?)",
                (job_id, created, "info", "Job queued"),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._job(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._job(row) for row in rows]  # type: ignore[misc]

    def event(self, job_id: str, message: str, level: str = "info") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO events(job_id,created_at,level,message) VALUES(?,?,?,?)",
                (job_id, now(), level, message.rstrip()),
            )

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT sequence,created_at,level,message FROM events WHERE job_id=? AND sequence>? ORDER BY sequence",
                (job_id, after),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                connection.commit()
                return None
            started = now()
            connection.execute(
                "UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'",
                (started, row["id"]),
            )
            connection.execute(
                "INSERT INTO events(job_id,created_at,level,message) VALUES(?,?,?,?)",
                (row["id"], started, "info", "Worker started job"),
            )
            connection.commit()
        return self.get(row["id"])

    def status(self, job_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row["status"] if row else None

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END, "
                "finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END "
                "WHERE id=? AND status IN ('queued','running')",
                (now(), job_id),
            )
            changed = cursor.rowcount > 0
        if changed:
            self.event(job_id, "Cancellation requested", "warning")
        return changed

    def finish(self, job_id: str, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        if status not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal status: {status}")
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?,finished_at=?,result_json=?,error=? WHERE id=?",
                (status, now(), json.dumps(result, sort_keys=True) if result else None, error, job_id),
            )
        self.event(job_id, error or f"Job {status}", "error" if status == "failed" else "info")

    def patch_result(self, job_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT result_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            result = json.loads(row["result_json"] or "{}")
            result.update(values)
            connection.execute("UPDATE jobs SET result_json=? WHERE id=?", (json.dumps(result, sort_keys=True), job_id))
        return result
