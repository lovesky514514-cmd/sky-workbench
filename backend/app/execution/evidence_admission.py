"""只接收总控提供的冻结证据，定位符不能触发文件或网络读取。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Literal

from app.execution.expert_contracts import EvidenceRef, ExpertScope


@dataclass(frozen=True)
class FrozenArtifact:
    scope: ExpertScope
    ref: EvidenceRef
    data: bytes


@dataclass(frozen=True)
class EvidenceRejection:
    evidence_id: str
    reason: Literal['duplicate_id', 'wrong_scope', 'wrong_producer', 'missing_bytes', 'digest_mismatch']


def admit_artifacts(scope: ExpertScope, run_id: str, attempt_id: str, artifacts: Iterable[FrozenArtifact],
                    *, reserved_ids: frozenset[str] = frozenset()
                    ) -> tuple[tuple[FrozenArtifact, ...], tuple[EvidenceRejection, ...]]:
    records = tuple(artifacts)
    counts = Counter(record.ref.evidence_id for record in records)
    admitted, rejected = [], []
    for record in records:
        evidence_id = record.ref.evidence_id
        origin = record.ref.artifact
        reason = None
        if counts[evidence_id] != 1 or evidence_id in reserved_ids:
            reason = 'duplicate_id'
        elif record.scope != scope:
            reason = 'wrong_scope'
        elif origin is None or (origin.run_id, origin.attempt_id) != (run_id, attempt_id):
            reason = 'wrong_producer'
        elif not isinstance(record.data, bytes) or not record.data:
            reason = 'missing_bytes'
        elif sha256(record.data).hexdigest() != origin.sha256:
            reason = 'digest_mismatch'
        if reason:
            rejected.append(EvidenceRejection(evidence_id, reason))
        else:
            admitted.append(record)
    return tuple(admitted), tuple(rejected)
