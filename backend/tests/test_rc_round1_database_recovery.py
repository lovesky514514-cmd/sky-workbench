"""Isolated database failures must preserve originals and revoke old claims."""

import hashlib
import sqlite3
from pathlib import Path

from app.session.store import SessionStore


def test_new_database_starts_without_recovery(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    assert store.recovery_status["status"] == "not_required"
    assert store.list() == []


def test_truncated_database_is_preserved_and_quarantined(tmp_path):
    original = tmp_path / "sessions.db"
    original.write_bytes(b"not a sqlite database")
    companion = tmp_path / "sessions.db-wal"
    companion.write_bytes(b"forensic wal bytes")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()

    store = SessionStore(original)
    store.initialize()
    assert original.read_bytes() == b"not a sqlite database"
    assert companion.read_bytes() == b"forensic wal bytes"
    assert store.path != original
    assert store.recovery_status["status"] == "history_quarantined"
    assert store.recovery_status["historical_scientific_results"] == "UNVERIFIED"
    assert hashlib.sha256(Path(store.recovery_status["backup"]).read_bytes()).hexdigest() == digest
    assert store.list() == []

    # A restart reuses the isolated active DB, never the damaged original.
    new_session = store.create("workbench")
    restarted = SessionStore(original)
    restarted.initialize()
    assert restarted.path == store.path
    assert restarted.get(new_session["id"])["state"] == "IDLE"


def test_old_schema_completed_claim_is_not_imported(tmp_path):
    original = tmp_path / "sessions.db"
    with sqlite3.connect(original) as db:
        db.execute("CREATE TABLE sessions(id TEXT, title TEXT, state TEXT)")
        db.execute("INSERT INTO sessions VALUES ('old', 'Old claimed success', 'COMPLETED')")
    old_bytes = original.read_bytes()
    store = SessionStore(original)
    store.initialize()
    assert original.read_bytes() == old_bytes
    assert store.recovery_status["reason"] == "legacy_session_schema"
    assert store.list() == []


def test_archive_hides_task_but_preserves_scientific_record(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session = store.create("workbench", "Example task")
    store.add_message(session["id"], "user", "2D example", "PLAN")
    result = store.archive(session["id"])
    assert result["records_deleted"] == 0
    assert store.list() == []
    preserved = store.get(session["id"])
    assert preserved["messages"][0]["content"] == "2D example"
    assert any(event["type"] == "session.archived" for event in preserved["events"])
