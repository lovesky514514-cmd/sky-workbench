"""Append-only SQLite event log and session projections."""

from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import DATABASE_PATH
from app.models import AgentMode, AgentState
from app.session.run_journal import RUN_SCHEMA, RunJournalMixin
from app.session.reviews import REVIEW_SCHEMA, ReviewStoreMixin
from app.session.state_grounding import STATE_GROUNDING_SCHEMA
from app.session.assumptions import ASSUMPTION_SCHEMA
from app.memory.store import MEMORY_SCHEMA


MAX_TIMELINE_ITEMS = 250

def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore(ReviewStoreMixin, RunJournalMixin):
    """Store durable facts and rebuild client views from the event stream."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = path
        self._lock = RLock()
        self.recovery_status: dict[str, Any] = {"status": "not_required"}

    def _select_safe_database(self) -> None:
        """Quarantine an unreadable/legacy file without editing its original bytes.

        A fresh, separate database is preferable to pretending that a damaged
        event chain certifies old runs. The original DB and WAL/SHM companions
        stay in place and receive byte-for-byte backups for manual recovery.
        """
        if not self.path.exists():
            return
        original = self.path
        try:
            if original.stat().st_size == 0:
                raise sqlite3.DatabaseError("empty_existing_database")
            with sqlite3.connect(f"file:{original.as_posix()}?mode=ro", uri=True) as source:
                if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("integrity_check_failed")
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "sessions" in tables:
                    columns = {row[1] for row in source.execute("PRAGMA table_info(sessions)")}
                    expected = {"id", "title", "project_id", "state", "mode", "plan_json",
                                "model_json", "error", "created_at", "updated_at"}
                    if not expected.issubset(columns) or not {"events", "run_journal", "run_attempts"}.issubset(tables):
                        raise sqlite3.DatabaseError("legacy_session_schema")
        except (OSError, sqlite3.DatabaseError) as exc:
            digest = hashlib.sha256()
            with original.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            identity = digest.hexdigest()[:16]
            backup_dir = original.parent / "recovery-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{original.stem}.{identity}.db"
            if not backup.exists():
                shutil.copy2(original, backup)
            # The original WAL/SHM may be needed for forensic reconstruction;
            # never delete them or mistake the isolated new DB for recovered data.
            for suffix in ("-wal", "-shm"):
                companion = original.with_name(original.name + suffix)
                target = backup_dir / (backup.name + suffix)
                if companion.is_file() and not target.exists():
                    shutil.copy2(companion, target)
            self.path = original.with_name(f"{original.stem}.degraded-{identity}.db")
            self.recovery_status = {
                "status": "history_quarantined", "reason": str(exc),
                "backup": str(backup), "active_database": str(self.path),
                "historical_scientific_results": "UNVERIFIED",
            }

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._select_safe_database()
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    mode TEXT,
                    plan_json TEXT,
                    model_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_session_sequence
                    ON events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS resource_samples (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_resource_samples_session_run
                    ON resource_samples(session_id, run_id, sequence);
                """
            )

            db.executescript(RUN_SCHEMA)
            db.executescript(REVIEW_SCHEMA)
            db.executescript(STATE_GROUNDING_SCHEMA)
            db.executescript(ASSUMPTION_SCHEMA)
            db.executescript(MEMORY_SCHEMA)

    def create(self, project_id: str, title: str | None = None) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO sessions
                   (id, title, project_id, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, title or "New scientific task", project_id, AgentState.IDLE, now, now),
            )
        self.append_event(session_id, "session.created", {"project_id": project_id})
        return self.get(session_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM sessions s WHERE NOT EXISTS
                   (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.event_type='session.archived')
                   ORDER BY updated_at DESC LIMIT ?""", (limit,)
            ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def archive(self, session_id: str) -> dict[str, Any]:
        """Hide a settled task from Recent without deleting its scientific record.

        The managed workspace is a single root, not a disposable project.
        Archival is deliberately refused while a run still owns execution.
        """
        if not self.exists(session_id):
            raise KeyError(session_id)
        latest = self.latest_run(session_id)
        if latest and latest["outcome"] is None:
            raise RuntimeError("active_run_cannot_be_archived")
        with self._connect() as db:
            archived = db.execute(
                "SELECT 1 FROM events WHERE session_id=? AND event_type='session.archived' LIMIT 1",
                (session_id,),
            ).fetchone()
        if not archived:
            self.append_event(session_id, "session.archived", {"reason": "user_removed_from_recent"})
        return {"status": "archived", "session_id": session_id, "records_deleted": 0}

    def exists(self, session_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    def get(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            event_rows = db.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY sequence", (session_id,)
            ).fetchall()
        view = self._row_to_summary(row)
        events = [self._event_from_row(item) for item in event_rows]
        view["events"] = events
        view["messages"] = [
            event["payload"]
            for event in events
            if event["type"] == "message"
        ]
        timeline_by_id: dict[str, dict[str, Any]] = {}
        timeline_order: list[str] = []
        for event in events:
            if not event["type"].startswith("timeline."):
                continue
            payload = event["payload"]
            if event["type"] == "timeline.worker" and payload.get("status") == "running":
                payload = {**payload, "status": "info"}
            item_id = payload["id"]
            if item_id not in timeline_by_id:
                timeline_order.append(item_id)
                timeline_by_id[item_id] = {}
            timeline_by_id[item_id].update(payload)
        view["timeline"] = [timeline_by_id[item_id] for item_id in timeline_order[-MAX_TIMELINE_ITEMS:]]
        return view

    def events_after(self, session_id: str, sequence: int, limit: int = 500) -> list[dict[str, Any]]:
        """Read a bounded event tail for resumable SSE without changing durability."""

        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM events
                   WHERE session_id = ? AND sequence > ?
                   ORDER BY sequence LIMIT ?""",
                (session_id, max(0, sequence), max(1, min(limit, 500))),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def append_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str:
        event_id = str(uuid.uuid4())
        now = utc_now()
        encoded = json.dumps(payload, ensure_ascii=False)
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO events
                   (session_id, event_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, event_id, event_type, encoded, now),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return event_id

    def completed_evidence(self, run_id: str) -> dict[str, Any] | None:
        """Return only evidence published by the current completed attempt."""

        with self._connect() as db:
            rows = db.execute(
                """SELECT e.payload_json, r.attempt_id
                   FROM events e JOIN run_journal r ON r.session_id = e.session_id
                   WHERE r.run_id = ? AND e.event_type = 'run.completed'
                   ORDER BY e.sequence DESC""",
                (run_id,),
            ).fetchall()
        for row in rows:
            payload = json.loads(row['payload_json'])
            if payload.get('run_id') != run_id:
                continue
            if payload.get('attempt_id') != row['attempt_id']:
                return None
            evidence = payload.get('evidence_contract')
            return evidence if isinstance(evidence, dict) and evidence.get('passed') is True else None
        return None

    def append_resource_sample(self, session_id: str, run_id: str, payload: dict[str, Any]) -> None:
        """Persist lightweight telemetry with a bounded per-run history."""

        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO resource_samples (session_id, run_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, run_id, json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            db.execute(
                """DELETE FROM resource_samples WHERE sequence IN (
                       SELECT sequence FROM resource_samples
                       WHERE session_id = ? AND run_id = ?
                       ORDER BY sequence DESC LIMIT -1 OFFSET 600
                   )""",
                (session_id, run_id),
            )

    def resource_samples(self, session_id: str, run_id: str, limit: int = 600) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT payload_json, created_at FROM resource_samples
                   WHERE session_id = ? AND run_id = ? ORDER BY sequence DESC LIMIT ?""",
                (session_id, run_id, max(1, min(limit, 600))),
            ).fetchall()
        return [{"created_at": row["created_at"], **json.loads(row["payload_json"])} for row in reversed(rows)]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        mode: AgentMode,
        **extra: Any,
    ) -> str:
        payload = {"role": role, "content": content, "mode": mode, "created_at": utc_now()}
        payload.update(extra)
        return self.append_event(session_id, "message", payload)

    def set_state(self, session_id: str, state: AgentState, error: str | None = None) -> None:
        now = utc_now()
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE sessions SET state = ?, error = ?, updated_at = ? WHERE id = ?",
                (state, error, now, session_id),
            )
        self.append_event(session_id, "state.changed", {"state": state, "error": error})

    def set_mode(self, session_id: str, mode: AgentMode) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?",
                (mode, utc_now(), session_id),
            )

    def set_plan(self, session_id: str, plan: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE sessions SET plan_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(plan, ensure_ascii=False), utc_now(), session_id),
            )
        self.append_event(session_id, "plan.updated", plan)

    def set_model(self, session_id: str, model: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE sessions SET model_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(model, ensure_ascii=False), utc_now(), session_id),
            )
        self.append_event(session_id, "model.updated", model)

    def set_title_from_prompt(self, session_id: str, prompt: str) -> None:
        title = " ".join(prompt.strip().split())[:64] or "Scientific task"
        with self._lock, self._connect() as db:
            row = db.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row and row["title"] == "New scientific task":
                db.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title, utc_now(), session_id),
                )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "project_id": row["project_id"],
            "state": row["state"],
            "mode": row["mode"],
            "plan": json.loads(row["plan_json"]) if row["plan_json"] else None,
            "model": json.loads(row["model_json"]) if row["model_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": row["sequence"],
            "id": row["event_id"],
            "type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }


session_store = SessionStore()
