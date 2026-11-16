"""同一科学尝试内的冻结证据交换；不把引用当作任意文件权限。"""
import hashlib
import json
from pathlib import Path

from app.execution.evidence_admission import FrozenArtifact
from app.execution.expert_contracts import ArtifactOrigin, EvidenceRef
from app.execution.files import bounded_read, reject_reparse
from app.validators.physics.cooling_audit import HISTORY_SOURCE


def evidence_limit(source):
    # 四份最多 5000×7 的冷却历史冻结为同一证据，其余证据保持原限额。
    return 6_000_000 if source == HISTORY_SOURCE else 2_000_000


class EvidenceVault:
    def __init__(self, workspace, scope):
        self.scope = scope
        self.root = Path(workspace) / 'research_evidence' / scope.run_id / scope.attempt_id
        reject_reparse(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def freeze(self, evidence_id, source, payload):
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        limit = evidence_limit(source)
        if len(data) > limit:
            raise ValueError('frozen_evidence_size_limit')
        digest = hashlib.sha256(data).hexdigest()
        # 文件名仅由摘要生成，不接受模型提供的文件名。
        name = digest + '.json'
        target = self.root / name
        if target.exists():
            if bounded_read(self.root, name, limit) != data:
                raise ValueError('frozen_evidence_changed')
        else:
            with target.open('xb') as stream:
                stream.write(data)
        ref = EvidenceRef(schema_version=1, evidence_id=evidence_id, kind='run_artifact', source_ref=source,
            locator=name, applicability='this scientific run and attempt only',
            artifact=ArtifactOrigin(run_id=self.scope.run_id, attempt_id=self.scope.attempt_id, sha256=digest))
        return FrozenArtifact(self.scope, ref, data)

    def read(self, ref):
        if ref.kind != 'run_artifact' or ref.artifact is None or (
                ref.artifact.run_id, ref.artifact.attempt_id) != (self.scope.run_id, self.scope.attempt_id):
            raise PermissionError('foreign_evidence_producer')
        if ref.locator != ref.artifact.sha256 + '.json':
            raise PermissionError('unregistered_evidence_locator')
        data = bounded_read(self.root, ref.locator, evidence_limit(ref.source_ref))
        if hashlib.sha256(data).hexdigest() != ref.artifact.sha256:
            raise ValueError('frozen_evidence_integrity_lost')
        return json.loads(data)
