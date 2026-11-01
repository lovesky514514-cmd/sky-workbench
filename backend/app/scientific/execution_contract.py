"""Canonical execution inputs and server-owned revision bindings."""

import hashlib
import json
import os
from typing import Any


# Display status cannot grant execution. All scientific fields, including units,
# boundary selections, functions and material tables, remain inside the digest.
DISPLAY_FIELDS = {
    "created_at",
    "updated_at",
    "quality_status",
    "quality_report",
    "precheck_summary",
    "runnable",
    "planning_status",
    "ai_provider",
    "ai_model",
    "source",
}

BINDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_bindings (
 session_id TEXT NOT NULL, revision INTEGER NOT NULL, plan_id TEXT NOT NULL,
 project_id TEXT NOT NULL, principal TEXT NOT NULL, input_digest TEXT NOT NULL,
 PRIMARY KEY(session_id, revision), FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""


def principal_fingerprint() -> str:
    # The desktop is a single-principal service. Rotating its credential requires
    # a fresh review/commit; no bearer token or test credential is stored here.
    token = os.getenv("COMSOL_WORKBENCH_SESSION_TOKEN", "")
    return hashlib.sha256(("desktop:" + token).encode()).hexdigest()


def execution_digest(plan: dict[str, Any]) -> str:
    content = {key: value for key, value in plan.items() if key not in DISPLAY_FIELDS}
    encoded = json.dumps(
        content,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def scientific_inputs(plan: dict[str, Any]) -> dict[str, Any]:
    ignored = DISPLAY_FIELDS | {
        "plan_id",
        "revision",
        "completion",
        "warnings",
        "sources",
        "title",
    }
    return {key: value for key, value in plan.items() if key not in ignored}


def write_binding(db, session_id: str, plan: dict[str, Any]) -> None:
    row = db.execute(
        "SELECT project_id FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    db.execute(
        "INSERT INTO execution_bindings VALUES(?,?,?,?,?,?)",
        (
            session_id,
            plan["revision"],
            plan["plan_id"],
            row[0],
            principal_fingerprint(),
            execution_digest(plan),
        ),
    )


def verify_binding(db, session_id: str, plan: dict[str, Any]) -> None:
    row = db.execute(
        "SELECT project_id,plan_json FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    binding = db.execute(
        "SELECT * FROM execution_bindings WHERE session_id=? AND revision=?",
        (session_id, plan.get("revision")),
    ).fetchone()
    if not row or not binding or binding["plan_id"] != plan.get("plan_id"):
        raise ValueError(
            "Execution snapshot is unbound; review and save this plan again"
        )
    digest = execution_digest(plan)
    persisted = json.loads(row["plan_json"]) if row["plan_json"] else {}
    if (
        binding["project_id"] != row["project_id"]
        or binding["principal"] != principal_fingerprint()
        or binding["input_digest"] != digest
        or execution_digest(persisted) != digest
    ):
        raise ValueError(
            "Execution inputs or principal changed after review; save and review again"
        )
