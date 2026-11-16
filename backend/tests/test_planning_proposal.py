"""计划先提案、N02验收后发布；真实提供商预算不被旧JSON修复循环绕过。"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.planner import extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.execution.expert_contracts import ExpertScope
from app.orchestration.plan_proposal import propose_reviewed_plan
from app.orchestration.research_store import ResearchStore
from app.providers.manager import CompletionResult
from app.session.store import SessionStore
from app.validators.base_validator import ValidationReport


def test_metered_plan_proposal_is_admitted_before_plan_publication(tmp_path, monkeypatch):
    import app.agent.runtime as env
    monkeypatch.setattr(env, 'WORKSPACE_ROOT', tmp_path)
    prompt = '2D stationary heat transfer 10 mm x 5 mm left 373 K right 293 K insulated k=20 W/(m*K)'
    payload = extract_heat_transfer_plan(prompt)
    calls = []
    class Provider:
        async def complete_metered(self, messages, **options):
            calls.append(messages)
            metrics = {'requests': 1, 'usage_known': True, 'input_tokens': 100, 'output_tokens': 200, 'latency_ms': 1}
            options['observe'](metrics)
            return CompletionResult(json.dumps(payload), 'fixture', 'fixture', 'fixture-model'), metrics
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    sid = store.create('workbench')['id']
    runtime = AgentRuntime(sessions=store, providers=Provider())
    monkeypatch.setattr(runtime, '_retrieve', lambda *_: [])
    monkeypatch.setattr(runtime, '_validate_plan', lambda _: ValidationReport.from_checks('precheck', []))
    scope = ExpertScope(project_id='workbench', session_id=sid, case_id='parent', plan_id='research-parent',
                        plan_revision=1, run_id='research-parent', attempt_id='parent-attempt')
    studies = ResearchStore(store)
    studies.create(scope, 'request', {'goal': prompt})
    studies.finish(scope, 'READY_FOR_PLAN', {'scientific_status': 'NOT_RUN'})
    parent = studies.read('parent')
    result = asyncio.run(propose_reviewed_plan(runtime.controller, sid, prompt, parent,
        {'model_calls': 8, 'token_limit': 250000}, tmp_path))
    assert result is not None and store.get(sid)['plan']['plan_id'] == result.plan_id
    assert len(calls) >= 1 and len(calls) <= 2
    events = store.get(sid)['events']
    verified = next(index for index, event in enumerate(events) if event['type']=='experts.task_verified')
    published = next(index for index, event in enumerate(events) if event['type']=='plan.updated')
    assert verified < published and store.latest_run(sid) is None
    with store._connect() as db:
        child = db.execute("SELECT case_id FROM research_studies WHERE case_id!='parent'").fetchone()['case_id']
    receipt = studies.read(child)['calls'][0]['receipt']
    assert receipt['requests'] == 1 and receipt['input_tokens'] == 100
    before = len(calls)
    reused = asyncio.run(propose_reviewed_plan(runtime.controller, sid, prompt, parent,
        {'model_calls': 8, 'token_limit': 250000}, tmp_path))
    assert reused is None and len(calls) == before


@pytest.mark.asyncio
async def test_cancel_during_provider_proposal_never_publishes_or_replays(tmp_path, monkeypatch):
    import app.agent.runtime as env
    monkeypatch.setattr(env, 'WORKSPACE_ROOT', tmp_path)
    started = asyncio.Event()
    class Provider:
        async def complete_metered(self, *args, **options):
            started.set()
            await asyncio.sleep(30)
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    sid = store.create('workbench')['id']
    runtime = AgentRuntime(sessions=store, providers=Provider())
    monkeypatch.setattr(runtime, '_retrieve', lambda *_: [])
    studies = ResearchStore(store)
    scope = ExpertScope(project_id='workbench', session_id=sid, case_id='parent', plan_id='research-parent',
                        plan_revision=1, run_id='research-parent', attempt_id='parent-attempt')
    studies.create(scope, 'request', {'goal': 'Heat research'})
    parent = studies.finish(scope, 'READY_FOR_PLAN', {'scientific_status': 'NOT_RUN'})
    task = asyncio.create_task(runtime.controller.prepare_plan(sid, 'Heat research', parent,
        {'model_calls': 8, 'token_limit': 250000}))
    await asyncio.wait_for(started.wait(), 5)
    await runtime.controller.cancel_research(sid)
    with pytest.raises(asyncio.CancelledError): await task
    assert store.get(sid)['plan'] is None and store.latest_run(sid) is None
    child = studies.read(studies.read('parent')['proposal_case_id'])
    assert child['status'] == 'CANCELED' and child['calls'][0]['status'] == 'INTERRUPTED'
    result = await runtime.controller.prepare_plan(sid, 'Heat research', parent,
        {'model_calls': 8, 'token_limit': 250000})
    assert result is None and len(studies.read(child['case_id'])['calls']) == 1


def test_supported_draft_binding_keeps_unresolved_questions_and_forbids_runnable(tmp_path):
    from app.orchestration.plan_binding import bind_supported_draft
    from app.scientific.schemas import ScientificPlan
    plan = ScientificPlan(title='Unapproved draft', research_goal='三维铁块冷却5 mm x 5 mm x 25 mm，初温600 °C，环境20 °C，无接触，无相变。',
        original_request='三维铁块冷却5 mm x 5 mm x 25 mm，初温600 °C，环境20 °C，无接触，无相变。',
        missing_information=['independent numerical checks unresolved'])
    bound = bind_supported_draft(plan)
    assert bound.workflow_id == 'heat_transfer_3d_cooling' and not bound.runnable
    assert 'independent numerical checks unresolved' in bound.missing_information
    assert bound.parameters['initial_temperature'].value == 873.15
