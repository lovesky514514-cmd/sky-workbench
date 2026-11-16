"""生成版本固定的模拟 Heat 读回，并用独立审核重新校验。"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal

from app.agent.planner import extract_heat_transfer_plan
from app.execution.evidence_admission import FrozenArtifact
from app.execution.expert_contracts import ArtifactOrigin, EvidenceRef, ExpertScope, HEAT_RUBRIC_WEIGHTS
from app.execution.heat_review import BENCHMARK_ID, RECEIPT_ID, WORKFLOW, ExecutionReceipt, HeatReview, review_heat
from app.execution.revision_checkpoint import RevisionCheckpoint
from app.scientific.schemas import ScientificPlan
from app.validators.execution_verdict import execution_verdict
from app.validators.physics.heat_benchmark import FIELD_SOURCE, GRID, MODEL_SOURCE, HeatBenchmark
from app.validators.physics.heat_transfer_validator import HeatTransferValidator

Scenario = Literal['normal', 'wrong_boundary', 'missing_evidence', 'interrupted', 'revision', 'unknown_submission']
SOLVE_TOOL = 'heat.simulated_solve'
STAGES = ('research', 'build', 'solve', 'validate', 'export')
REFERENCE = EvidenceRef(
    schema_version=1, evidence_id='simulation-benchmark-source', kind='reference',
    source_ref='bundled.steady_heat_simulation.v1', locator='rectangular-plate',
    applicability='Fixed-temperature ends, insulated sides, uniform material and no internal source',
)


def fixture_plan() -> dict:
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan(
        '2D stationary Heat Transfer, 2000 mm x 1000 mm rectangle, left boundary 373.15 K, '
        'right boundary 293.15 K, top and bottom insulated, thermal conductivity 20 W/(m*K).'))
    report = HeatTransferValidator().precheck(plan)
    if not report.passed or not plan.runnable:
        raise ValueError('invalid_simulation_fixture')
    plan.source = 'heat-simulation'
    plan.runnable = False
    plan.title = '2D Heat · Simulation'
    plan.solver = ['Local deterministic simulation']
    plan.expected_outputs = ['Simulated evidence and independent review']
    plan.steps = []
    plan.precheck_summary = report.model_dump(mode='json')
    return plan.model_dump(mode='json')


def benchmark(checkpoint: RevisionCheckpoint) -> HeatBenchmark:
    p = checkpoint.plan['parameters']
    scale = {'m': 1.0, 'cm': 0.01, 'mm': 0.001}
    return HeatBenchmark(
        schema_version=1, scope=checkpoint.scope,
        width_m=p['width']['value'] * scale[p['width']['unit']],
        height_m=p['height']['value'] * scale[p['height']['unit']],
        left_K=p['left_temperature']['value'], right_K=p['right_temperature']['value'],
        conductivity_W_m_K=p['thermal_conductivity']['value'],
    )


def _producer(scope: ExpertScope) -> tuple[str, str]:
    return 'fixture-run-' + scope.case_id, 'fixture-attempt-' + scope.attempt_id


def fixture_readbacks(plan: HeatBenchmark, source: ExpertScope,
                      scenario: Scenario) -> tuple[FrozenArtifact, ...]:
    run_id, attempt_id = _producer(source)
    boundaries = [{'side': side, 'kind': 'fixed_temperature', 'temperature_K': temp}
                  for side, temp in [('left', plan.left_K), ('right', plan.right_K)]]
    boundaries.extend({'side': side, 'kind': 'insulated'} for side in ('top', 'bottom'))
    if scenario == 'wrong_boundary':
        boundaries[0]['side'], boundaries[2]['side'] = boundaries[2]['side'], boundaries[0]['side']
    model = dict(schema_version=1, geometry_kind='rectangle', width_m=plan.width_m, height_m=plan.height_m,
                 conductivity_W_m_K=plan.conductivity_W_m_K, heat_source_W_m3=0, boundaries=boundaries)
    field = {'schema_version': 1, 'samples': [
        dict(x_m=x * plan.width_m, y_m=y * plan.height_m,
             temperature_K=(1 - x) * plan.left_K + x * plan.right_K,
             qx_W_m2=plan.analytical_flux, qy_W_m2=0) for x in GRID for y in GRID
    ]}
    sources = [('model', MODEL_SOURCE, model)]
    if scenario != 'missing_evidence':
        sources.append(('field', FIELD_SOURCE, field))
    records = []
    for role, source_ref, value in sources:
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        ref = EvidenceRef(
            schema_version=1, kind='run_artifact', evidence_id=f'{role}-{source.attempt_id}',
            source_ref=source_ref, locator=f'simulation:{role}', applicability='Deterministic simulation only',
            artifact=ArtifactOrigin(run_id=run_id, attempt_id=attempt_id, sha256=sha256(raw).hexdigest()),
        )
        records.append(FrozenArtifact(plan.scope, ref, raw))
    return tuple(records)


def _receipt(checkpoint: RevisionCheckpoint, plan: HeatBenchmark, run_id: str, attempt_id: str,
             records: tuple[FrozenArtifact, ...]) -> ExecutionReceipt:
    scientific_plan = ScientificPlan.model_validate(checkpoint.plan)
    low, high = sorted((plan.left_K, plan.right_K))
    execution = {
        'run_id': run_id, 'attempt_id': attempt_id, 'command_success': True, 'model_state_verified': True,
        'scientific_validation_success': True,
        'model_state': {
            'geometry': ['rectangle'], 'materials': ['thermal material'], 'physics': ['heat transfer'],
            'boundary_conditions': ['left: configured'], 'mesh': ['fixture mesh'], 'study': ['stationary'],
            'solver': ['converged'], 'results': ['fixture readback'],
        },
        'validation': {'passed': True, 'left_boundary_median_K': plan.left_K, 'right_boundary_median_K': plan.right_K,
                       'temperature_min_K': low, 'temperature_max_K': high, 'analytical_rmse_K': 0.0},
    }
    manifest = {item.ref.source_ref: {'sha256': sha256(item.data).hexdigest(), 'size_bytes': len(item.data)}
                for item in records}
    evidence_ok = set(manifest) == {MODEL_SOURCE, FIELD_SOURCE} and all(
        manifest[item.ref.source_ref]['sha256'] == item.ref.artifact.sha256 for item in records)
    validation = HeatTransferValidator().validate_execution(scientific_plan, execution)
    verdict = execution_verdict(WORKFLOW, run_id, execution, validation, expected_attempt_id=attempt_id,
                                derived_evidence={'passed': evidence_ok, 'artifacts': manifest,
                                                  'issues': [] if evidence_ok else ['simulation_manifest_invalid']})
    return ExecutionReceipt(checkpoint.scope, attempt_id, verdict)


def _fixture_score(scope: ExpertScope, artifacts: tuple[FrozenArtifact, ...], first_review: bool) -> dict:
    model, field = (item.ref.evidence_id for item in artifacts)
    refs = {
        'goal_alignment': (BENCHMARK_ID, model),
        'scientific_correctness': (BENCHMARK_ID, model, field),
        'evidence': (model, field, RECEIPT_ID),
        'reproducibility': (model, RECEIPT_ID),
        'delivery': (field, RECEIPT_ID),
    }
    return dict(schema_version=1, scope=scope, rubric_version='heat-v1', dimensions=[
        dict(criterion=criterion, points=maximum - (6 if first_review and criterion == 'scientific_correctness' else 0),
             reason='模拟复验用例要求另一次取样' if first_review and criterion == 'scientific_correctness' else
             '基准模拟审核', evidence_refs=refs[criterion])
        for criterion, maximum in HEAT_RUBRIC_WEIGHTS.items()
    ])


def audit_checkpoint(checkpoint: RevisionCheckpoint, scenario: Scenario) -> HeatReview:
    plan = benchmark(checkpoint)
    observed = checkpoint.verified['solve']
    source = observed.result.scope
    if source.model_copy(update={'attempt_id': checkpoint.scope.attempt_id}) != checkpoint.scope:
        raise ValueError('simulation_provenance_mismatch')
    records = fixture_readbacks(plan, source, scenario)
    if tuple(item.ref for item in records) != observed.evidence:
        raise ValueError('simulation_readback_digest_mismatch')
    run_id, attempt_id = _producer(source)
    complete = len(records) == 2
    first_review = scenario == 'revision' and not checkpoint.revision_signatures
    return review_heat(
        scope=checkpoint.scope, scientific_run_id=run_id, scientific_attempt_id=attempt_id, benchmark=plan,
        artifacts=records, receipt=_receipt(checkpoint, plan, run_id, attempt_id, records) if complete else None,
        tasks=tuple(checkpoint.task(name) for name in STAGES), references=checkpoint.references,
        score_payload=_fixture_score(checkpoint.scope, records, first_review) if complete else None,
        task_by_criterion={'scientific_correctness': 'solve'},
    )
