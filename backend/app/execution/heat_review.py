"""N03 的宿主审核入口，组合现有执行判定、独立物理检查和 N01 评分。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal

from pydantic import ValidationError

from app.execution.evidence_admission import EvidenceRejection, FrozenArtifact, admit_artifacts
from app.execution.expert_contracts import (
    ArtifactOrigin, Contract, Criterion, EvidenceRef, ExpertClaim, ExpertIntake, ExpertScope, ExpertTask,
    HEAT_RUBRIC_WEIGHTS, ReviewScore, RevisionProposal, ScopedRecord,
)
from app.validators.execution_verdict import ExecutionVerdict
from app.validators.physics.heat_benchmark import (
    CHECKS, FIELD_SOURCE, MODEL_SOURCE, CheckObservation, CheckStatus, HeatBenchmark, HeatFieldReadback,
    HeatModelReadback, check_field, check_model,
)

WORKFLOW = 'heat_transfer_2d_stationary'
BENCHMARK_ID = 'heat-benchmark'
RECEIPT_ID = 'heat-execution-verdict'
PASS_SCORE = 95
DIMENSION_CHECKS = {
    'goal_alignment': ('model_geometry', 'boundary_selection'),
    'scientific_correctness': ('material_and_source', 'boundary_selection', 'temperature', 'heat_flux'),
    'evidence': ('model_geometry', 'temperature', 'execution'),
    'reproducibility': ('material_and_source', 'execution'),
    'delivery': ('temperature', 'heat_flux', 'execution'),
}


@dataclass(frozen=True)
class ExecutionReceipt:
    scope: ExpertScope
    attempt_id: str
    verdict: ExecutionVerdict


class HeatClaimReview(Contract):
    claim: ExpertClaim
    status: CheckStatus
    observations: tuple[CheckObservation, ...]
    supporting_evidence_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...]
    has_conflict: bool


class HeatReview(ScopedRecord):
    scientific_run_id: str
    scientific_attempt_id: str
    benchmark_sha256: str
    evidence: tuple[EvidenceRef, ...]
    rejected: tuple[EvidenceRejection, ...]
    claims: tuple[HeatClaimReview, ...]
    score: ReviewScore | None
    score_total: int | None
    status: Literal['PASS', 'BLOCKED', 'UNVERIFIED', 'REVISION_REQUIRED']
    reasons: tuple[str, ...]
    revision: RevisionProposal | None = None


def _host_evidence(benchmark: HeatBenchmark, run_id: str, attempt_id: str,
                   receipt: ExecutionReceipt | None) -> tuple[tuple[EvidenceRef, ...], tuple[CheckObservation, ...]]:
    reference = EvidenceRef(schema_version=1, evidence_id=BENCHMARK_ID, kind='reference',
                            source_ref='heat.benchmark.v1', locator='frozen_plan',
                            applicability='恒定导热率、无体热源的二维稳态矩形')
    if (receipt is None or receipt.scope != benchmark.scope or receipt.attempt_id != attempt_id
            or receipt.verdict.run_id != run_id or receipt.verdict.workflow_id != WORKFLOW):
        return (reference,), ()
    payload = {'verdict': receipt.verdict.as_dict(), 'attempt_id': attempt_id, 'scope': receipt.scope.model_dump()}
    fingerprint = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    run_evidence = EvidenceRef(
        schema_version=1, evidence_id=RECEIPT_ID, kind='run_artifact',
        source_ref='runtime.execution_verdict', locator='execution_verdict', applicability='当前科学运行与尝试',
        artifact=ArtifactOrigin(run_id=run_id, attempt_id=attempt_id, sha256=fingerprint),
    )
    status = 'PASS' if receipt.verdict.completed else 'FAIL'
    check = CheckObservation(evidence_id=RECEIPT_ID, status=status, reason='既有执行判定通过所有运行与产物检查')
    return (reference, run_evidence), (check,)


def _claims(benchmark: HeatBenchmark, readbacks: Iterable[FrozenArtifact],
            execution: tuple[CheckObservation, ...]) -> dict[str, HeatClaimReview]:
    observations: dict[str, list[CheckObservation]] = {key: [] for key in CHECKS}
    for artifact in readbacks:
        ref = artifact.ref.evidence_id
        source = artifact.ref.source_ref
        try:
            if source == MODEL_SOURCE:
                checks = check_model(benchmark, HeatModelReadback.model_validate_json(artifact.data), ref)
            else:
                checks = check_field(benchmark, HeatFieldReadback.model_validate_json(artifact.data), ref)
        except ValidationError:
            checks = {key: CheckObservation(evidence_id=ref, status='UNVERIFIED', reason='读回缺项或格式无效')
                      for key, (_, required_source) in CHECKS.items() if source == required_source}
        for key, check in checks.items():
            observations[key].append(check)
    claims = {key: _claim(key, description, observations[key], (BENCHMARK_ID,))
              for key, (description, _) in CHECKS.items()}
    claims['execution'] = _claim('execution', '当前尝试通过既有执行判定', execution)
    return claims


def _claim(key: str, description: str, observations: Iterable[CheckObservation],
           references: tuple[str, ...] = ()) -> HeatClaimReview:
    records = tuple(observations)
    supporting = tuple(item.evidence_id for item in records if item.status == 'PASS')
    counter = tuple(item.evidence_id for item in records if item.status == 'FAIL')
    status: CheckStatus = 'UNVERIFIED'
    if counter:
        status = 'FAIL'
    elif records and all(item.status == 'PASS' for item in records):
        status = 'PASS'
    claim = ExpertClaim(claim_id=key, statement=description,
                        evidence_refs=(*references, *(item.evidence_id for item in records)))
    return HeatClaimReview(claim=claim, status=status, observations=records, supporting_evidence_refs=supporting,
                           counterevidence_refs=counter, has_conflict=bool(supporting and counter))


def _score(intake: ExpertIntake, payload: dict | None, claims: Mapping[str, HeatClaimReview]) -> ReviewScore | None:
    if payload is None:
        return None
    score = intake.score(payload)
    for dimension in score.dimensions:
        linked = [claims[key] for key in DIMENSION_CHECKS[dimension.criterion]]
        refs = set(dimension.evidence_refs)
        allowed = {ref for item in linked for ref in item.claim.evidence_refs}
        observed = [{item.evidence_id for item in claim.observations if item.status != 'UNVERIFIED'} for claim in linked]
        if not refs <= allowed or any(not refs & group for group in observed):
            raise ValueError('Dimension must cite admissible observations for its assigned claims')
    return score


def _revision(intake: ExpertIntake, score: ReviewScore, claims: Mapping[str, HeatClaimReview],
              task_by_criterion: Mapping[Criterion, str]) -> RevisionProposal:
    issues, actions = [], []
    for dimension in score.dimensions:
        criterion = dimension.criterion
        if dimension.points == HEAT_RUBRIC_WEIGHTS[criterion]:
            continue
        task = task_by_criterion[criterion]
        issues.append(dict(schema_version=1, issue_id=f'review-{criterion}', claim_id=DIMENSION_CHECKS[criterion][0],
                           category='delivery' if criterion == 'delivery' else 'intent_mismatch',
                           reason=dimension.reason, evidence_refs=dimension.evidence_refs, affected_task_ids=(task,)))
        actions.append(dict(task_id=task, action='revalidate', target=criterion,
                            expected_evidence='提交该评分维度的修订依据及对应复核记录'))
    payload = dict(schema_version=1, scope=score.scope, revision_id=f'heat-review-{score.scope.attempt_id}',
                   issues=issues, actions=actions, budget_estimate={'agent_calls': 0, 'solver_runs': 0})
    return intake.proposal(payload, known_claims=set(claims))


def review_heat(*, scope: ExpertScope, scientific_run_id: str, scientific_attempt_id: str, benchmark: HeatBenchmark,
                artifacts: Iterable[FrozenArtifact], receipt: ExecutionReceipt | None, tasks: Iterable[ExpertTask],
                score_payload: dict | None = None, task_by_criterion: Mapping[Criterion, str] | None = None,
                references: tuple[EvidenceRef, ...] = (),
                ) -> HeatReview:
    """总控提供当前身份、冻结输入和任务。评分只是一份待核验的审核记录。"""
    if benchmark.scope != scope or not scientific_run_id.strip() or not scientific_attempt_id.strip():
        raise ValueError('A current benchmark and scientific run identity are required')
    reference_ids = {ref.evidence_id for ref in references}
    if (len(reference_ids) != len(references) or reference_ids.intersection((BENCHMARK_ID, RECEIPT_ID))
            or any(ref.kind != 'reference' for ref in references)):
        raise ValueError('Additional references must be unique, host-admitted reference evidence')
    refs, execution = _host_evidence(benchmark, scientific_run_id, scientific_attempt_id, receipt)
    accepted, rejected = admit_artifacts(scope, scientific_run_id, scientific_attempt_id, artifacts,
                                        reserved_ids=frozenset(reference_ids | {BENCHMARK_ID, RECEIPT_ID}))
    readbacks = tuple(item for item in accepted if item.ref.source_ref in {MODEL_SOURCE, FIELD_SOURCE})
    evidence = (*refs, *references, *(item.ref for item in readbacks))
    claims = _claims(benchmark, readbacks, execution)
    reasons = [f'{item.status}:{key}' for key, item in claims.items() if item.status != 'PASS']
    hard_failed = any(item.status == 'FAIL' for item in claims.values())
    score, revision = None, None
    try:
        intake = ExpertIntake(scope=scope, tasks=(task.model_dump() for task in tasks), evidence=evidence)
        score = _score(intake, score_payload, claims)
    except ValueError:
        reasons.append('invalid_review_scope_or_evidence')
    total = score.total if score is not None else None
    if total is None:
        reasons.append('score_unverified')

    status = 'BLOCKED' if hard_failed else 'UNVERIFIED'
    if not reasons and score is not None and total is not None:
        if total >= PASS_SCORE:
            status = 'PASS'
        else:
            try:
                revision = _revision(intake, score, claims, task_by_criterion or {})
                status = 'REVISION_REQUIRED'
                reasons.append('score_below_threshold')
            except (KeyError, ValueError):
                reasons.append('revision_target_unverified')
    return HeatReview(
        schema_version=1, scope=scope, scientific_run_id=scientific_run_id, scientific_attempt_id=scientific_attempt_id,
        benchmark_sha256=sha256(benchmark.model_dump_json().encode()).hexdigest(), evidence=evidence, rejected=rejected,
        claims=tuple(claims.values()), score=score, score_total=total, status=status, reasons=tuple(reasons), revision=revision,
    )
