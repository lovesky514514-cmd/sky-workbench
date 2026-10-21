"""vNext foundation contracts. Fixtures are public-safe and never invoke COMSOL."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.intent_compiler import compile_intent
from app.agent.planner import ScientificPlanner, extract_electrochem_plan, extract_heat_transfer_plan, extract_solid_plan, extract_tds_plan
from app.agent.runtime import AgentRuntime
from app.execution.graph import ExecutionGraph, NodeState, TypedCorrection
from app.model_adapter import ModelProposal, ProposalAdapter
from app.providers.manager import CompletionResult
from app.session.state_grounding import EventLedger, ModelStateLedger, Provenance, rebuild_context_snapshot
from app.session.store import SessionStore
from app.models import AgentState
from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationReport
from app.skills.library import SkillLibrary
from app.validators.execution_verdict import execution_verdict
from test_v110_verdict import payload_for


def test_product_completion_requires_independent_evidence_contract(tmp_path: Path) -> None:
    payload, report = payload_for('heat_transfer_2d_stationary', tmp_path)
    verdict = execution_verdict('heat_transfer_2d_stationary', 'current-run', payload, report, expected_attempt_id='attempt-1')
    assert verdict.completed and verdict.evidence_contract_passed
    for mutation in (
        lambda p: p.pop('attempt_id'),
        lambda p: p['validation'].__setitem__('dof', None),
        lambda p: p['model_state'].__setitem__('mesh', []),
        lambda p: Path(p['artifacts']['validation']).unlink(),
    ):
        damaged, damaged_report = payload_for('heat_transfer_2d_stationary', tmp_path / str(id(mutation)))
        mutation(damaged)
        rejected = execution_verdict('heat_transfer_2d_stationary', 'current-run', damaged, damaged_report, expected_attempt_id='attempt-1')
        assert not rejected.completed
        assert not rejected.evidence_contract_passed


def test_missing_evidence_never_retries_a_successful_solve(tmp_path: Path, monkeypatch) -> None:
    import app.agent.runtime as runtime_module
    import app.agent.model_card as cards
    payload, report = payload_for('heat_transfer_2d_stationary', tmp_path / 'evidence-gap')
    Path(payload['artifacts']['validation']).unlink()
    calls = 0

    class Validator:
        def precheck(self, plan): return ValidationReport.from_checks('precheck', [])
        def validate_execution(self, plan, execution): return report

    class Tools:
        def process_ids(self, session_id): return {}
        async def execute(self, name, arguments, on_event, retries):
            nonlocal calls
            calls += 1
            return {**deepcopy(payload), 'run_id': arguments['run_id'], 'attempt_id': arguments['attempt_id'], 'tool': name, 'duration_ms': 0, 'performance': {}}

    class Monitor:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass

    monkeypatch.setattr(runtime_module.validator_registry, 'get', lambda workflow: Validator())
    monkeypatch.setattr(runtime_module, 'ResourceMonitor', Monitor)
    monkeypatch.setattr(runtime_module, 'hardware_profile', lambda *args: {})
    monkeypatch.setattr(runtime_module, 'assess_risk', lambda *args, **kwargs: {})
    monkeypatch.setattr(runtime_module, 'settings_store', SimpleNamespace(load=lambda: {'execution': {'max_repairs': 3}}))
    monkeypatch.setattr(runtime_module.java_bridge_process_manager, 'status', lambda: {'running': False})
    monkeypatch.setattr(cards, 'WORKSPACE_ROOT', tmp_path)
    store = SessionStore(tmp_path / 'runtime.db'); store.initialize()
    session = store.create('vnext'); store.set_state(session['id'], AgentState.PRECHECK)
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan('2D stationary heat-transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'))
    asyncio.run(AgentRuntime(sessions=store, tools=Tools())._execute_run(session['id'], plan))
    view = store.get(session['id'])
    assert calls == 1 and view['state'] == 'FAILED'
    assert view['messages'][-1]['execution_verdict']['evidence_contract_passed'] is False


@pytest.mark.parametrize('text,workflow', [
    ('2D stationary heat-transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated', 'heat_transfer_2d_stationary'),
    ('2D stationary pure diffusion model 10 mm x 5 mm D 1e-9 m^2/s left 10 mol/m^3 right 1 mol/m^3 no flux', 'tds_2d_stationary_diffusion'),
])
def test_rc2_aliases_resolve_to_supported_workflow(text: str, workflow: str) -> None:
    intent = compile_intent(text)
    assert not intent.unknowns
    extract = extract_heat_transfer_plan(text) if workflow.startswith('heat_') else extract_tds_plan(text)
    assert extract and extract['workflow_id'] == workflow and extract['runnable']


def test_d0_coupling_label_cannot_be_misparsed_as_zero_diffusion() -> None:
    text = 'Heat Transfer and Transport of Diluted Species verification baseline D(T)=D0*T/T0'
    intent = compile_intent(text)
    assert intent.parameters['diffusion_coefficient'].value != 0
    assert not any('greater than zero' in issue for issue in intent.validation_issues)


def test_boundary_ambiguity_and_missing_material_fail_closed() -> None:
    heat = extract_heat_transfer_plan('2D stationary heat-transfer 10 mm x 5 mm left 373 K right 293 K insulated')
    assert heat and not heat['runnable'] and 'Material definition' in heat['missing_information']
    electrochem = extract_electrochem_plan('2D stationary primary current distribution 10 mm x 5 mm conductivity 1 S/m potentials 0 V and 1 V')
    assert electrochem and not electrochem['runnable']
    assert {'Left Potential', 'Right Potential'} <= set(electrochem['missing_information'])


@pytest.mark.parametrize('factory,text', [
    (extract_solid_plan, "3D stationary solid mechanics 10 mm x 2 mm x 1 mm Young's modulus 70 GPa Poisson's ratio 0.3 density 2700 kg/m^3 fixed left traction 1 MPa"),
    (extract_electrochem_plan, '2D stationary primary current distribution 10 mm x 5 mm conductivity 1 S/m left 0 V right 1 V'),
])
def test_complete_deterministic_nonheat_plans_remain_runnable(factory, text: str) -> None:
    plan = factory(text)
    assert plan and plan['runnable']
    assert plan['boundary_conditions'] and plan['expected_outputs']


def test_provider_cannot_erase_deterministic_executable_facts() -> None:
    text = '2D stationary primary current distribution 10 mm x 5 mm conductivity 1 S/m left 0 V right 1 V'
    proposed = extract_electrochem_plan(text)
    assert proposed
    proposed['runnable'] = False
    proposed['boundary_conditions'] = []
    proposed['expected_outputs'] = []
    proposed['parameters']['length']['value'] = 99

    class Provider:
        async def complete(self, *args, **kwargs):
            return CompletionResult(json.dumps(proposed), 'ollama', 'Ollama', 'qwen3:8b')

    payload, _, _ = asyncio.run(ScientificPlanner(Provider()).plan(text, []))
    assert payload['runnable']
    assert payload['parameters']['length']['value'] == pytest.approx(0.01)
    assert payload['boundary_conditions'] and payload['expected_outputs']


def test_model_state_ledger_rebuilds_without_transcript_and_readback_wins(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / 'sessions.db'); store.initialize()
    session = store.create('vnext')
    facts = ModelStateLedger(store)
    events = EventLedger(store)
    facts.append(session['id'], 'parameters.width', 0.02, Provenance.PLAN)
    facts.append(session['id'], 'parameters.width', 0.0199998, Provenance.COMSOL_READBACK, run_id='run-1', attempt_id='attempt-1')
    events.append(session['id'], 'vnext.test', {'bounded': True})
    snapshot = rebuild_context_snapshot(store, session['id'], max_events=5)
    assert snapshot.facts['parameters.width']['value'] == pytest.approx(0.0199998)
    assert snapshot.facts['parameters.width']['provenance'] == 'COMSOL_READBACK'
    assert 'messages' not in snapshot.as_dict()
    replacement_runtime_store = SessionStore(store.path); replacement_runtime_store.initialize()
    rebuilt = rebuild_context_snapshot(replacement_runtime_store, session['id'], max_events=5)
    assert snapshot.canonical_hash == rebuilt.canonical_hash
    assert rebuilt.facts == snapshot.facts


def test_interruptible_graph_pause_correct_invalidate_resume() -> None:
    graph = ExecutionGraph.small_supported('run-1', 'attempt-1', 'heat_transfer_2d_stationary')
    graph.start('build')
    graph.request_pause()
    graph.safe_boundary('build')
    assert graph.nodes['build'].state is NodeState.PAUSED
    correction = TypedCorrection(path='parameters.width', value=0.02, unit='m', reason='human correction')
    invalidated = graph.apply_correction(correction, readback={'parameters.width': 0.02})
    assert invalidated == ['build', 'solve', 'validate', 'export']
    assert all(graph.nodes[name].state is NodeState.INVALIDATED for name in invalidated)
    assert graph.resume() == 'build'
    assert graph.corrections[0]['provenance'] == 'HUMAN_CORRECTION'


def test_seed_skills_validate_and_cannot_certify_success() -> None:
    library = SkillLibrary.default()
    assert 4 <= len(library.cards) <= 6
    for card in library.cards:
        assert card.provenance and card.applicability and card.mandatory_readback and card.invariants
        assert 'certif' not in json.dumps(card.model_dump(mode='json')).lower()


def test_typed_model_adapter_cannot_emit_evidence() -> None:
    class Echo(ProposalAdapter):
        async def propose(self, snapshot):
            return ModelProposal(action='clarify', arguments={'field': 'material'}, rationale='Missing material')
    proposal = asyncio.run(Echo().propose({'schema_version': 1, 'facts': {}}))
    assert proposal.action == 'clarify'
    assert not hasattr(proposal, 'scientific_validation_passed')


def test_3d_contracts_are_preparation_only() -> None:
    contract = json.loads((Path(__file__).resolve().parents[2] / 'vnext' / '3d' / 'minimal_3d_contracts.json').read_text(encoding='utf-8'))
    assert contract['support_status'] == 'PREPARATION_ONLY_UNVERIFIED'
    assert {case['geometry'] for case in contract['cases']} == {'block', 'cylinder', 'hole', 'assembly'}
    assert all(not case.get('execution_claim') for case in contract['cases'])


def test_published_vnext_schemas_are_valid_json_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in (
        'STATE_LEDGER_SCHEMA.json', 'EVENT_LEDGER_SCHEMA.json', 'CONTEXT_SNAPSHOT_SCHEMA.json',
        'SKILLCARD_SCHEMA.json', 'ASSUMPTION_LEDGER_SCHEMA.json', 'MEMORY_SCHEMA.json',
    ):
        schema = json.loads((root / name).read_text(encoding='utf-8'))
        assert schema['$schema'].endswith('2020-12/schema') and schema['type'] == 'object'
