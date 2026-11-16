"""三维 Heat 独立审核：复用证据准入与原 95 分门槛，物理失败优先。"""
import json
from typing import Literal

from app.execution.evidence_admission import admit_artifacts
from app.execution.expert_contracts import HEAT_RUBRIC_WEIGHTS, ReviewScore, ScopedRecord
from app.validators.physics.cooling_audit import MODEL_SOURCE, FIELD_SOURCE, HISTORY_SOURCE, audit_cooling


class CoolingReview(ScopedRecord):
    scientific_run_id: str
    scientific_attempt_id: str
    status: Literal['PASS','BLOCKED','UNVERIFIED']
    reasons: tuple[str,...]
    checks: dict
    metrics: dict
    score: ReviewScore | None = None
    score_total: int | None = None
    revision: dict | None = None


def review_cooling(scope, plan, artifacts, verdict):
    accepted,rejected=admit_artifacts(scope,scope.run_id,scope.attempt_id,artifacts)
    by_source={source:[a for a in accepted if a.ref.source_ref==source] for source in (MODEL_SOURCE,FIELD_SOURCE,HISTORY_SOURCE)}
    reasons=[f'{r.evidence_id}:{r.reason}' for r in rejected]
    checks={}; metrics={}; score=None
    if all(len(items)==1 for items in by_source.values()):
        raw={key:json.loads(items[0].data) for key,items in by_source.items()}
        findings=audit_cooling(plan,raw[MODEL_SOURCE],raw[FIELD_SOURCE],raw[HISTORY_SOURCE])
        checks,metrics=findings['checks'],findings['metrics']
        checks['execution']={'status':'PASS' if verdict.completed and verdict.run_id==scope.run_id and verdict.workflow_id==plan.workflow_id else 'FAIL'}
        dimensions={'goal_alignment':('model_geometry','parameter_readback','boundary_selection'),
            'scientific_correctness':tuple(checks),'evidence':('independent_saved_profile','finite_fields'),
            'reproducibility':('independent_resolves','mesh_agreement','time_agreement'),
            'delivery':('independent_saved_profile','requested_time')}
        refs=tuple(a.ref.evidence_id for a in accepted)
        score=ReviewScore(schema_version=1,scope=scope,rubric_version='heat-v1',dimensions=tuple(
            dict(criterion=key,points=maximum if all(checks.get(name,{}).get('status')=='PASS' for name in dimensions[key]) else 0,
                reason='独立原始数据与模型观测决定分数；文字修改不改变物理检查',evidence_refs=refs)
            for key,maximum in HEAT_RUBRIC_WEIGHTS.items()))
        reasons.extend(key for key,value in checks.items() if value['status']!='PASS')
    else:
        reasons.append('independent_evidence_missing_or_ambiguous')
    total=score.total if score else None
    status='BLOCKED' if checks and reasons else 'UNVERIFIED' if reasons or total is None or total<95 else 'PASS'
    revision=None if status=='PASS' else {'affected_tasks':['acquire-independent-readback','challenge-numerics','independent-review'],
        'reason':reasons,'solver_runs':0,'requires_new_run_authorization':True}
    return CoolingReview(schema_version=1,scope=scope,scientific_run_id=scope.run_id,scientific_attempt_id=scope.attempt_id,
        status=status,reasons=tuple(reasons),checks=checks,metrics=metrics,score=score,score_total=total,revision=revision)
