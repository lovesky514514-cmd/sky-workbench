"""Durable, explicit record of model-affecting choices not fixed by the user."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


ASSUMPTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS assumption_ledger (
 assumption_id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL,
 run_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 plan_node_id TEXT NOT NULL,
 choice_json TEXT NOT NULL,
 reason TEXT NOT NULL,
 source TEXT NOT NULL,
 confidence REAL NOT NULL,
 user_confirmed INTEGER NOT NULL,
 affected_nodes_json TEXT NOT NULL,
 decision TEXT NOT NULL,
 executable INTEGER NOT NULL,
 domain_sensitive INTEGER NOT NULL,
 safe_default INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 superseded_by TEXT,
 FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_assumption_ledger_session
 ON assumption_ledger(session_id, created_at);
"""


class AssumptionDecision(StrEnum):
    CONTINUE = "CONTINUE"
    ASK_HUMAN = "ASK_HUMAN"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AssumptionLedger:
    def __init__(self, store: Any) -> None:
        self.store = store
        with self.store._lock, self.store._connect() as db:
            db.executescript(ASSUMPTION_SCHEMA)

    def record(
        self,
        *,
        session_id: str,
        run_id: str,
        attempt_id: str,
        plan_node_id: str,
        choice: Any,
        reason: str,
        source: str,
        confidence: float,
        user_confirmed: bool,
        affected_nodes: list[str],
        safe_default: bool = False,
        domain_sensitive: bool = False,
    ) -> dict[str, Any]:
        if not all(str(value).strip() for value in (session_id, run_id, attempt_id, plan_node_id, reason, source)):
            raise ValueError("assumption identity, node, reason and source are required")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("assumption confidence must be between 0 and 1")
        nodes = list(dict.fromkeys(str(node) for node in affected_nodes if str(node).strip()))
        if not nodes:
            raise ValueError("an assumption must identify affected PlanGraph nodes")
        executable = bool(user_confirmed or (safe_default and not domain_sensitive))
        decision = AssumptionDecision.CONTINUE if executable else AssumptionDecision.ASK_HUMAN
        record = {
            "assumption_id": str(uuid.uuid4()), "session_id": session_id,
            "run_id": run_id, "attempt_id": attempt_id, "plan_node_id": plan_node_id,
            "choice": choice, "reason": reason, "source": source,
            "confidence": float(confidence), "user_confirmed": bool(user_confirmed),
            "affected_nodes": nodes, "decision": decision, "executable": executable,
            "domain_sensitive": bool(domain_sensitive), "safe_default": bool(safe_default),
            "created_at": _now(), "superseded_by": None,
        }
        with self.store._lock, self.store._connect() as db:
            db.execute(
                """INSERT INTO assumption_ledger(
                   assumption_id,session_id,run_id,attempt_id,plan_node_id,choice_json,
                   reason,source,confidence,user_confirmed,affected_nodes_json,decision,
                   executable,domain_sensitive,safe_default,created_at,superseded_by
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["assumption_id"], session_id, run_id, attempt_id, plan_node_id,
                    json.dumps(choice, ensure_ascii=False, sort_keys=True), reason, source,
                    float(confidence), int(user_confirmed), json.dumps(nodes), decision.value,
                    int(executable), int(domain_sensitive), int(safe_default), record["created_at"], None,
                ),
            )
        self.store.append_event(session_id, "assumption.recorded", record)
        return record

    def active(self, session_id: str) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM assumption_ledger WHERE session_id=? AND superseded_by IS NULL ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def supersede(self, assumption_id: str, replacement_id: str) -> list[str]:
        if assumption_id == replacement_id:
            raise ValueError("an assumption cannot supersede itself")
        with self.store._lock, self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM assumption_ledger WHERE assumption_id=?", (assumption_id,)).fetchone()
            replacement = db.execute("SELECT * FROM assumption_ledger WHERE assumption_id=?", (replacement_id,)).fetchone()
            if old is None or replacement is None:
                raise KeyError(assumption_id if old is None else replacement_id)
            if old["session_id"] != replacement["session_id"]:
                raise ValueError("supersession must remain inside one session")
            if old["superseded_by"] is not None:
                raise ValueError("assumption is already superseded")
            db.execute(
                "UPDATE assumption_ledger SET superseded_by=?, executable=0 WHERE assumption_id=?",
                (replacement_id, assumption_id),
            )
        invalidated = list(json.loads(old["affected_nodes_json"]))
        self.store.append_event(old["session_id"], "assumption.superseded", {
            "assumption_id": assumption_id, "superseded_by": replacement_id,
            "invalidated_nodes": invalidated,
        })
        return invalidated

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        return {
            "assumption_id": row["assumption_id"], "session_id": row["session_id"],
            "run_id": row["run_id"], "attempt_id": row["attempt_id"],
            "plan_node_id": row["plan_node_id"], "choice": json.loads(row["choice_json"]),
            "reason": row["reason"], "source": row["source"],
            "confidence": float(row["confidence"]), "user_confirmed": bool(row["user_confirmed"]),
            "affected_nodes": json.loads(row["affected_nodes_json"]),
            "decision": AssumptionDecision(row["decision"]), "executable": bool(row["executable"]),
            "domain_sensitive": bool(row["domain_sensitive"]), "safe_default": bool(row["safe_default"]),
            "created_at": row["created_at"], "superseded_by": row["superseded_by"],
        }
