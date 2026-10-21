"""Runtime publication integration with a synthetic worker; never invokes COMSOL."""
import asyncio
import json
from copy import deepcopy
from itertools import product
from types import SimpleNamespace

import pytest

from app.agent.planner import extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.models import AgentState
from app.scientific.schemas import ScientificPlan
from app.session.store import SessionStore
from app.validators.base_validator import ValidationReport
from test_v110_verdict import REQUIRED_CHECKS, payload_for


@pytest.mark.parametrize('workflow', list(REQUIRED_CHECKS))
@pytest.mark.parametrize('flags', list(product([False, True], repeat=3)))
def test_runtime_event_message_timeline_card_agree(tmp_path, monkeypatch, workflow, flags):
    import app.agent.runtime as module
    import app.agent.model_card as cards
    payload, report = payload_for(workflow, tmp_path)
    for name, flag in zip(('command_success', 'model_state_verified', 'scientific_validation_success'), flags):
        payload[name] = flag
    payload.update(tool='contract-only', duration_ms=0, performance={})

    class Validator:
        def precheck(self, plan): return ValidationReport.from_checks('precheck', [])
        def validate_execution(self, plan, execution): return report

    class Tools:
        def process_ids(self, session_id): return {}
        async def execute(self, name, arguments, on_event, retries):
            for phase in ['Solve', 'Solution Validation']:
                await on_event({'phase': phase, 'status': 'running', 'command_success': True, 'model_state_verified': True, 'scientific_validation_passed': True})
            return {**deepcopy(payload), 'run_id': arguments['run_id'], 'attempt_id': arguments['attempt_id']}

    class Monitor:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass

    async def bridge(*args): pass
    monkeypatch.setattr(module.validator_registry, 'get', lambda workflow: Validator())
    monkeypatch.setattr(module, 'ResourceMonitor', Monitor)
    monkeypatch.setattr(module, 'hardware_profile', lambda *args: {})
    monkeypatch.setattr(module, 'assess_risk', lambda *args, **kwargs: {})
    monkeypatch.setattr(module, 'settings_store', SimpleNamespace(load=lambda: {'execution': {'max_repairs': 0}}))
    monkeypatch.setattr(module.java_bridge_process_manager, 'status', lambda: {'running': False})
    monkeypatch.setattr(cards, 'WORKSPACE_ROOT', tmp_path)
    store = SessionStore(tmp_path / 'sessions.db'); store.initialize()
    session = store.create('contract-only')
    store.set_state(session['id'], AgentState.PRECHECK)
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'))
    plan.workflow_id = workflow
    runtime = AgentRuntime(sessions=store, tools=Tools())
    monkeypatch.setattr(runtime, '_verify_java_bridge', bridge)
    asyncio.run(runtime._execute_run(session['id'], plan))
    view = store.get(session['id'])
    observations = [e['payload'] for e in view['events'] if e['type'] == 'comsol.progress']
    assert observations and all(e['evidence_status'] == 'provisional' and 'scientific_validation_passed' not in e for e in observations)
    assert (view['state'] == 'COMPLETED') == all(flags)
    final = view['messages'][-1]
    verdict = final['execution_verdict']
    publication = [e['payload'] for e in view['events'] if e['type'] in ('run.completed', 'run.failed')][-1]
    timeline = [e['payload'] for e in view['events'] if e['type'] == 'timeline.completed' and 'execution_verdict' in e['payload']][-1]
    assert publication['execution_verdict'] == timeline['execution_verdict'] == verdict
    for name in ('command_success', 'model_state_verified', 'scientific_validation_success'):
        assert final[name] == publication[name] == verdict[name]
    card = tmp_path / 'reports' / verdict['run_id'] / 'model_card.md'
    card_verdict = json.loads(card.read_text(encoding='utf-8').split('```json\n')[1].split('\n```')[0])
    assert card_verdict == verdict
