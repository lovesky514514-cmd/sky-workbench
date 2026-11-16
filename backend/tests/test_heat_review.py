from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from app.execution.evidence_admission import FrozenArtifact
from app.execution.expert_contracts import ArtifactOrigin, EvidenceRef, ExpertScope, ExpertTask, HEAT_RUBRIC_WEIGHTS
from app.execution.heat_review import BENCHMARK_ID, RECEIPT_ID, WORKFLOW, ExecutionReceipt, HeatReview, review_heat
from app.validators.execution_verdict import ExecutionVerdict
from app.validators.physics.heat_benchmark import FIELD_SOURCE, MODEL_SOURCE, HeatBenchmark

RUN = 'solver-run'
ATTEMPT = 'solver-attempt'
MODEL = 'model-readback'
FIELD = 'field-readback'
SCOPE = ExpertScope(project_id='heat-project', session_id='heat-session', case_id='steady-heat', plan_id='heat-plan',
                    plan_revision=2, run_id='expert-run', attempt_id='expert-attempt')
SCORE_REFS = {
    'goal_alignment': (BENCHMARK_ID, MODEL),
    'scientific_correctness': (BENCHMARK_ID, MODEL, FIELD),
    'evidence': (MODEL, FIELD, RECEIPT_ID),
    'reproducibility': (MODEL, RECEIPT_ID),
    'delivery': (FIELD, RECEIPT_ID),
}


@pytest.fixture
def inputs():
    return json.loads((Path(__file__).parent / 'fixtures' / 'heat_review_v1.json').read_text(encoding='utf-8'))


def freeze(data, *, source=MODEL_SOURCE, evidence_id=MODEL, scope=SCOPE, run=RUN, attempt=ATTEMPT):
    raw = json.dumps(data, sort_keys=True).encode()
    origin = ArtifactOrigin(run_id=run, attempt_id=attempt, sha256=sha256(raw).hexdigest())
    return FrozenArtifact(scope, EvidenceRef(schema_version=1, evidence_id=evidence_id, kind='run_artifact',
                                             source_ref=source, locator='frozen_readback', applicability='Heat fixture',
                                             artifact=origin), raw)


def score(total, *, scope=SCOPE):
    return dict(schema_version=1, scope=scope.model_dump(), rubric_version='heat-v1', dimensions=[
        dict(criterion=criterion, points=maximum - (100 - total if criterion == 'delivery' else 0),
             reason=('交付缺少逐项复现说明' if criterion == 'delivery' and total < 100 else '按证据逐项审核'),
             evidence_refs=SCORE_REFS[criterion]) for criterion, maximum in HEAT_RUBRIC_WEIGHTS.items()
    ])


def evaluate(inputs, **overrides):
    task = ExpertTask(schema_version=1, scope=SCOPE, task_id='heat-delivery-review', capability='scientific_review',
                      objective='Review Heat delivery', budget_estimate={'agent_calls': 0, 'solver_runs': 0})
    verdict = ExecutionVerdict(RUN, WORKFLOW, True, True, True, (), True)
    params = dict(scope=SCOPE, scientific_run_id=RUN, scientific_attempt_id=ATTEMPT, tasks=(task,),
                  benchmark=HeatBenchmark(scope=SCOPE, **inputs['benchmark']),
                  artifacts=(freeze(inputs['model']), freeze(inputs['field'], source=FIELD_SOURCE, evidence_id=FIELD)),
                  receipt=ExecutionReceipt(SCOPE, ATTEMPT, verdict), score_payload=score(100),
                  task_by_criterion={'delivery': task.task_id})
    params.update(overrides)
    return review_heat(**params)


def find(report, key):
    return next(item for item in report.claims if item.claim.claim_id == key)


@pytest.mark.parametrize('total', [95, 100])
def test_review_passes_at_threshold_and_keeps_provenance(inputs, total):
    report = evaluate(inputs, score_payload=score(total))
    assert report.status == 'PASS' and report.score_total == total and not report.revision
    assert all(claim.status == 'PASS' for claim in report.claims)
    assert {item.criterion: item.evidence_refs for item in report.score.dimensions} == SCORE_REFS
    assert find(report, 'heat_flux').supporting_evidence_refs == (FIELD,)
    assert len(report.benchmark_sha256) == 64
    assert HeatReview.model_validate_json(report.model_dump_json()) == report


def test_score_94_generates_only_evidence_bound_revision(inputs):
    report = evaluate(inputs, score_payload=score(94))
    assert report.status == 'REVISION_REQUIRED' and report.score_total == 94
    issue, = report.revision.issues
    action, = report.revision.actions
    assert issue.evidence_refs == SCORE_REFS['delivery'] and issue.reason == '交付缺少逐项复现说明'
    assert action.task_id == 'heat-delivery-review' and action.target == 'delivery'
    assert report.revision.budget_estimate.solver_runs == 0
    rejected = evaluate(inputs, score_payload=score(94), task_by_criterion={'delivery': 'forged-task'})
    assert rejected.status == 'UNVERIFIED' and rejected.revision is None


@pytest.mark.parametrize(('defect', 'claim'), [
    ('boundary', 'boundary_selection'), ('conductivity', 'material_and_source'),
    ('center', 'temperature'), ('units', 'temperature'), ('flux_sign', 'heat_flux'),
    ('flux_magnitude', 'heat_flux'), ('transverse', 'heat_flux'), ('geometry', 'model_geometry'),
])
def test_perfect_score_cannot_override_a_failed_physical_check(inputs, defect, claim):
    center = inputs['field']['samples'][4]
    if defect == 'boundary':
        left, _, top, _ = inputs['model']['boundaries']
        left['side'], top['side'] = top['side'], left['side']
    elif defect == 'conductivity':
        inputs['model']['conductivity_W_m_K'] *= 1000
    elif defect == 'center':
        center['temperature_K'] += 2
    elif defect == 'units':
        center['temperature_K'] -= 273.15
    elif defect == 'flux_sign':
        center['qx_W_m2'] *= -1
    elif defect == 'flux_magnitude':
        center['qx_W_m2'] *= 1.1
    elif defect == 'transverse':
        center['qy_W_m2'] = 20
    elif defect == 'geometry':
        inputs['model']['geometry_kind'] = 'ellipse'
    report = evaluate(inputs)
    assert report.score_total == 100 and report.status == 'BLOCKED' and not report.revision
    assert find(report, claim).status == 'FAIL'


def test_conflict_preserves_support_and_counterevidence_and_blocks_delivery(inputs):
    original = freeze(inputs['field'], source=FIELD_SOURCE, evidence_id=FIELD)
    inputs['field']['samples'][4]['qx_W_m2'] *= -1
    counter = freeze(inputs['field'], source=FIELD_SOURCE, evidence_id='second-field')
    report = evaluate(inputs, artifacts=(freeze(inputs['model']), original, counter))
    check = find(report, 'heat_flux')
    assert report.status == 'BLOCKED' and check.has_conflict
    assert check.supporting_evidence_refs == (FIELD,) and check.counterevidence_refs == ('second-field',)
    assert [item.status for item in check.observations] == ['PASS', 'FAIL']


@pytest.mark.parametrize('defect', ['missing', 'incomplete_grid', 'wrong_schema', 'unreadable'])
def test_missing_or_unreadable_field_cannot_certify(inputs, defect):
    if defect == 'incomplete_grid':
        inputs['field']['samples'].pop()
    if defect == 'wrong_schema':
        inputs['field']['samples'][0]['qx_W_m2'] = '800'
    field = freeze(inputs['field'] if defect != 'unreadable' else 'not a field', source=FIELD_SOURCE, evidence_id=FIELD)
    report = evaluate(inputs, artifacts=(freeze(inputs['model']),) if defect == 'missing'
                      else (freeze(inputs['model']), field))
    assert report.status == 'UNVERIFIED' and report.score_total is None
    assert find(report, 'temperature').status == 'UNVERIFIED'


@pytest.mark.parametrize(('fault', 'reason'), [
    ('project_id', 'wrong_scope'), ('session_id', 'wrong_scope'), ('plan_revision', 'wrong_scope'),
    ('expert_attempt', 'wrong_scope'), ('run', 'wrong_producer'), ('attempt', 'wrong_producer'),
    ('tampered_bytes', 'digest_mismatch'), ('duplicate', 'duplicate_id'), ('reserved_id', 'duplicate_id'),
])
def test_rejects_stale_foreign_and_tampered_artifacts(inputs, fault, reason):
    kwargs = {'source': FIELD_SOURCE, 'evidence_id': FIELD}
    if fault in {'project_id', 'session_id', 'plan_revision', 'expert_attempt'}:
        key = 'attempt_id' if fault == 'expert_attempt' else fault
        kwargs['scope'] = SCOPE.model_copy(update={key: 1 if key == 'plan_revision' else 'foreign'})
    elif fault in {'run', 'attempt'}:
        kwargs[fault] = 'old'
    elif fault == 'reserved_id':
        kwargs['evidence_id'] = RECEIPT_ID
    field = freeze(inputs['field'], **kwargs)
    if fault == 'tampered_bytes':
        field = replace(field, data=field.data + b' ')
    fields = (field, field) if fault == 'duplicate' else (field,)
    report = evaluate(inputs, artifacts=(freeze(inputs['model']), *fields))
    assert report.status == 'UNVERIFIED' and FIELD not in {item.evidence_id for item in report.evidence}
    assert {item.reason for item in report.rejected} == {reason}


@pytest.mark.parametrize('fault', ['forged_ref', 'irrelevant_ref', 'old_score', 'forged_approval'])
def test_score_must_match_current_scope_and_observed_claims(inputs, fault):
    candidate = score(100)
    if fault == 'forged_ref':
        candidate['dimensions'][0]['evidence_refs'] = ['invented']
    elif fault == 'irrelevant_ref':
        candidate['dimensions'][0]['evidence_refs'] = [FIELD]
    elif fault == 'old_score':
        candidate['scope']['plan_revision'] -= 1
    elif fault == 'forged_approval':
        candidate['approved'] = True
    result = evaluate(inputs, score_payload=candidate)
    assert result.status == 'UNVERIFIED' and result.score is None
    assert all(claim.status == 'PASS' for claim in result.claims)


@pytest.mark.parametrize(('case', 'expected'), [('missing', 'UNVERIFIED'), ('old', 'UNVERIFIED'), ('failed', 'BLOCKED')])
def test_existing_execution_verdict_remains_a_required_gate(inputs, case, expected):
    verdict = ExecutionVerdict(RUN, WORKFLOW, True, True, True, (), True)
    receipt = ExecutionReceipt(SCOPE, ATTEMPT, verdict)
    if case == 'missing':
        receipt = None
    elif case == 'old':
        receipt = replace(receipt, attempt_id='old-attempt')
    else:
        receipt = replace(receipt, verdict=verdict.reject('artifact_digest_changed'))
    result = evaluate(inputs, receipt=receipt)
    assert result.status == expected
    assert find(result, 'execution').status == ('FAIL' if case == 'failed' else 'UNVERIFIED')


def test_heat_review_combines_existing_validator_verdict_and_new_field_gate(inputs, tmp_path):
    from app.agent.planner import extract_heat_transfer_plan
    from app.scientific.schemas import ScientificPlan
    from app.validators.execution_verdict import execution_verdict
    from app.validators.physics.heat_transfer_validator import HeatTransferValidator
    from test_v110_verdict import payload_for

    plan = ScientificPlan.model_validate(extract_heat_transfer_plan(
        '2D stationary Heat Transfer 2000 mm x 1000 mm aluminum left 373 K right 293 K insulated'))
    execution, _ = payload_for(WORKFLOW, tmp_path)
    execution.update(run_id=RUN, attempt_id=ATTEMPT, model_state={
        'geometry': ['rectangle'], 'materials': ['aluminum'], 'physics': ['heat transfer'],
        'boundary_conditions': ['left: 373 K'], 'mesh': ['mesh'], 'study': ['stationary'],
        'solver': ['converged'], 'results': ['temperature field'],
    })
    execution['validation'].update(left_boundary_median_K=373.15, right_boundary_median_K=293.15,
                                   temperature_min_K=293.15, temperature_max_K=373.15, analytical_rmse_K=0.15)
    validation = HeatTransferValidator().validate_execution(plan, execution)
    legacy_verdict = execution_verdict(WORKFLOW, RUN, execution, validation, expected_attempt_id=ATTEMPT)
    receipt = ExecutionReceipt(SCOPE, ATTEMPT, legacy_verdict)
    assert legacy_verdict.completed and evaluate(inputs, receipt=receipt).status == 'PASS'
    inputs['field']['samples'][4]['qx_W_m2'] = -800
    report = evaluate(inputs, receipt=receipt)
    assert find(report, 'execution').status == 'PASS'
    assert find(report, 'heat_flux').status == 'FAIL'
    assert report.status == 'BLOCKED' and report.score_total == 100
