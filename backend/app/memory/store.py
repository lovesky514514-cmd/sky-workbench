"""Deterministic memory admission, supersession and version quarantine."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS typed_memories (
 memory_id TEXT PRIMARY KEY,
 memory_type TEXT NOT NULL,
 content_json TEXT NOT NULL,
 provenance_json TEXT NOT NULL,
 validation_status TEXT NOT NULL,
 validation_reason TEXT NOT NULL,
 version_scope TEXT NOT NULL,
 applicability_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_typed_memories_type_status
 ON typed_memories(memory_type, validation_status, created_at);
"""


class MemoryType(StrEnum):
    EPISODIC_RUN = "EPISODIC_RUN"
    SEMANTIC_FAILURE_PATTERN = "SEMANTIC_FAILURE_PATTERN"
    PROCEDURAL_REPAIR = "PROCEDURAL_REPAIR"
    NEGATIVE_REPAIR = "NEGATIVE_REPAIR"
    RESOURCE_API = "RESOURCE_API"
    BELIEF_HYPOTHESIS = "BELIEF_HYPOTHESIS"


class ValidationStatus(StrEnum):
    TRUSTED = "TRUSTED"
    TRUSTED_NEGATIVE = "TRUSTED_NEGATIVE"
    HYPOTHESIS = "HYPOTHESIS"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: MemoryType
    content: dict[str, Any]
    provenance: dict[str, Any]
    version_scope: str
    applicability: dict[str, Any]
    source_outcome: str
    evidence_contract_passed: bool
    scientific_validation_passed: bool
    false_pass: bool = False


class MemoryAdmissionPolicy:
    """Hard rules only; no learned confidence or LLM judgement is authoritative."""

    def evaluate(self, candidate: MemoryCandidate) -> tuple[ValidationStatus, str]:
        if not candidate.provenance.get("run_id") or not candidate.provenance.get("attempt_id"):
            return ValidationStatus.REJECTED, "missing_run_attempt_provenance"
        if candidate.false_pass:
            return ValidationStatus.REJECTED, "false_pass_source"
        if candidate.memory_type is MemoryType.BELIEF_HYPOTHESIS:
            return ValidationStatus.HYPOTHESIS, "belief_is_not_factual_evidence"
        if candidate.memory_type is MemoryType.NEGATIVE_REPAIR:
            return ValidationStatus.TRUSTED_NEGATIVE, "retained_as_negative_repair_only"
        if candidate.memory_type is MemoryType.PROCEDURAL_REPAIR:
            if candidate.source_outcome != "Completed":
                return ValidationStatus.REJECTED, "procedural_source_not_completed"
            if not candidate.evidence_contract_passed or not candidate.scientific_validation_passed:
                return ValidationStatus.REJECTED, "procedural_source_not_scientifically_certified"
            return ValidationStatus.TRUSTED, "certified_procedural_source"
        if candidate.memory_type is MemoryType.EPISODIC_RUN:
            if candidate.source_outcome != "Completed":
                return ValidationStatus.REJECTED, "episodic_source_not_completed"
            if not candidate.evidence_contract_passed or not candidate.scientific_validation_passed:
                return ValidationStatus.REJECTED, "episodic_source_not_scientifically_certified"
            return ValidationStatus.TRUSTED, "certified_episodic_source"
        if candidate.source_outcome in {"Failed", "Unverifiable", "UNVERIFIABLE"}:
            return ValidationStatus.REJECTED, "unverified_source"
        if not candidate.evidence_contract_passed:
            return ValidationStatus.REJECTED, "evidence_contract_missing"
        return ValidationStatus.TRUSTED, "validated_source"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    def __init__(self, store: Any) -> None:
        self.store = store
        with self.store._lock, self.store._connect() as db:
            db.executescript(MEMORY_SCHEMA)

    def admit(self, candidate: MemoryCandidate, policy: MemoryAdmissionPolicy) -> dict[str, Any]:
        status, reason = policy.evaluate(candidate)
        record = {
            "memory_id": str(uuid.uuid4()), "type": MemoryType(candidate.memory_type),
            "content": candidate.content, "provenance": candidate.provenance,
            "validation_status": status, "validation_reason": reason,
            "version_scope": candidate.version_scope, "applicability": candidate.applicability,
            "created_at": _now(), "superseded_by": None,
        }
        with self.store._lock, self.store._connect() as db:
            db.execute(
                """INSERT INTO typed_memories(memory_id,memory_type,content_json,provenance_json,
                   validation_status,validation_reason,version_scope,applicability_json,created_at,superseded_by)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["memory_id"], record["type"].value,
                    json.dumps(record["content"], ensure_ascii=False, sort_keys=True),
                    json.dumps(record["provenance"], ensure_ascii=False, sort_keys=True),
                    status.value, reason, candidate.version_scope,
                    json.dumps(candidate.applicability, ensure_ascii=False, sort_keys=True),
                    record["created_at"], None,
                ),
            )
        return record

    def admit_failure(
        self,
        *,
        session_id: str,
        run_id: str,
        attempt_id: str,
        workflow_id: str,
        failure_class: str,
        failed_phase: str,
        attempted_action: str,
        outcome: str,
        version_scope: str,
    ) -> dict[str, Any]:
        """Persist a non-certifying negative repair memory.

        Failure memories are useful for avoiding a repeated unsafe action, but
        are never evidence that a model or scientific result is correct.
        """

        if not all(value.strip() for value in (session_id, run_id, attempt_id, workflow_id)):
            raise ValueError("failure memory requires session/run/attempt/workflow identity")
        return self.admit(
            MemoryCandidate(
                memory_type=MemoryType.NEGATIVE_REPAIR,
                content={
                    "workflow_id": workflow_id,
                    "failure_class": failure_class or "unclassified_failure",
                    "failed_phase": failed_phase or "UNKNOWN",
                    "attempted_action": attempted_action or "NONE",
                    "outcome": outcome,
                    "may_certify": False,
                },
                provenance={
                    "session_id": session_id,
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "authority": "RUN_JOURNAL_TERMINAL_OUTCOME",
                },
                version_scope=version_scope,
                applicability={"workflow_id": workflow_id},
                source_outcome=outcome,
                evidence_contract_passed=False,
                scientific_validation_passed=False,
                false_pass=False,
            ),
            MemoryAdmissionPolicy(),
        )

    def get(self, memory_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM typed_memories WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return self._decode(row)

    def relevant(self, workflow_id: str, *, version_scope: str) -> list[dict[str, Any]]:
        with self.store._lock, self.store._connect() as db:
            rows = db.execute(
                """SELECT * FROM typed_memories
                   WHERE validation_status IN (?,?) AND superseded_by IS NULL ORDER BY rowid""",
                (ValidationStatus.TRUSTED.value, ValidationStatus.TRUSTED_NEGATIVE.value),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = self._decode(row)
                if not self._version_compatible(item["version_scope"], version_scope):
                    db.execute(
                        "UPDATE typed_memories SET validation_status=?,validation_reason=? WHERE memory_id=?",
                        (ValidationStatus.QUARANTINED.value, "version_scope_incompatible", item["memory_id"]),
                    )
                    continue
                applicable = item["applicability"].get("workflow_id")
                if applicable not in (None, "*", workflow_id):
                    continue
                result.append(item)
        return result

    def supersede(self, memory_id: str, successor_id: str) -> None:
        if memory_id == successor_id:
            raise ValueError("memory cannot supersede itself")
        with self.store._lock, self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT 1 FROM typed_memories WHERE memory_id=?", (memory_id,)).fetchone()
            new = db.execute("SELECT 1 FROM typed_memories WHERE memory_id=?", (successor_id,)).fetchone()
            if not old or not new:
                raise KeyError(memory_id if not old else successor_id)
            db.execute(
                "UPDATE typed_memories SET validation_status=?,validation_reason=?,superseded_by=? WHERE memory_id=?",
                (ValidationStatus.SUPERSEDED.value, "superseded", successor_id, memory_id),
            )

    @staticmethod
    def _version_compatible(record_scope: str, active_scope: str) -> bool:
        def major_minor(value: str) -> tuple[str, ...]:
            return tuple(value.strip().lstrip("v").split(".")[:2])
        return bool(record_scope and active_scope and major_minor(record_scope) == major_minor(active_scope))

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"], "type": MemoryType(row["memory_type"]),
            "content": json.loads(row["content_json"]), "provenance": json.loads(row["provenance_json"]),
            "validation_status": ValidationStatus(row["validation_status"]),
            "validation_reason": row["validation_reason"], "version_scope": row["version_scope"],
            "applicability": json.loads(row["applicability_json"]), "created_at": row["created_at"],
            "superseded_by": row["superseded_by"],
        }
