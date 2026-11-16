"""API 生命周期、作用域和中断恢复；模型使用合成传输。"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.agent.runtime import AgentRuntime
from app.orchestration.research_api import research_router
from app.orchestration.research_store import ResearchStore
from app.providers.manager import ProviderManager, ProviderStatus
from app.session.store import SessionStore


@pytest.mark.asyncio
async def test_api_background_cancel_idempotency_project_isolation(tmp_path, monkeypatch):
    import app.agent.runtime as env
    monkeypatch.setattr(env, 'WORKSPACE_ROOT', tmp_path)
    started = asyncio.Event()
    class Provider(ProviderManager):
        async def complete_metered(self, *a, **k):
            started.set()
            await asyncio.sleep(30)
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    sid = store.create('allowed')['id']; other = store.create('other')['id']
    runtime = AgentRuntime(sessions=store, providers=Provider())
    app = FastAPI(); app.include_router(research_router(lambda: runtime, lambda: 'allowed'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        payload = {'request_key': 'one-research', 'goal': 'Heat research'}
        response = await client.post(f'/api/sessions/{sid}/research', json=payload)
        assert response.status_code == 202 and response.json()['status'] == 'ACTIVE'
        await started.wait()
        repeated = await client.post(f'/api/sessions/{sid}/research', json=payload)
        assert repeated.status_code == 202 and repeated.json()['case_id'] == response.json()['case_id']
        modified = await client.post(f'/api/sessions/{sid}/research', json={**payload, 'goal': 'changed goal'})
        assert modified.status_code == 409
        assert (await client.get(f'/api/sessions/{other}/research')).status_code == 404
        assert (await client.get('/api/sessions/missing/research')).status_code == 404
        canceled = await client.post(f'/api/sessions/{sid}/research/cancel')
        assert canceled.json()['status'] == 'CANCELED'
        assert store.latest_run(sid) is None
        assert len(runtime.controller._research_tasks) == 1


@pytest.mark.asyncio
async def test_interrupted_plan_claim_returns_conflict_not_replay(tmp_path):
    from app.execution.expert_contracts import ExpertScope
    store=SessionStore(tmp_path/'sessions.db');store.initialize()
    sid=store.create('allowed')['id']
    runtime=AgentRuntime(sessions=store)
    studies=ResearchStore(store)
    identity='a'*32
    scope=ExpertScope(project_id='allowed',session_id=sid,case_id=identity,plan_id='planning',plan_revision=1,run_id='research',attempt_id='attempt')
    studies.create(scope,'request',{'goal':'Heat'})
    studies.finish(scope,'READY_FOR_PLAN',{'scientific_status':'NOT_RUN'})
    studies.claim_plan(identity,'b'*32)
    app=FastAPI();app.include_router(research_router(lambda:runtime,lambda:'allowed'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(f'/api/sessions/{sid}/research/plan',json={'case_id':identity})
        assert response.status_code==409 and response.json()['detail']=='research_plan_proposal_claim_interrupted'
    assert studies.read(identity)['calls']==[] and store.latest_run(sid) is None


def test_restart_marks_inflight_research_interrupted_without_resubmitting(tmp_path):
    from app.execution.expert_contracts import Budget, ExpertScope, ExpertTask
    from app.execution.revision_checkpoint import TaskRule
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    sid = store.create('workbench')['id']
    studies = ResearchStore(store)
    scope = ExpertScope(project_id='workbench', session_id=sid, case_id='case', plan_id='research-case',
                        plan_revision=1, run_id='research-case', attempt_id='attempt')
    studies.create(scope, 'request', {'goal': 'Heat'})
    task = ExpertTask(schema_version=1, scope=scope, task_id='intent', capability='research_intent',
                      objective='Heat', budget_estimate=Budget(agent_calls=1, solver_runs=0))
    store.create_checkpoint(scope, (TaskRule(task=task),), Budget(agent_calls=1, solver_runs=0))
    token = store.begin_checkpoint_dispatch(scope)
    store.claim_checkpoint_task(scope, 'intent', token=token)
    studies.reserve(scope, 'intent', 4096, SimpleNamespace(model_calls=1, token_limit=10000))
    restarted = ResearchStore(SessionStore(store.path))
    assert restarted.interrupt_active() == 1
    result = restarted.read('case')
    assert result['status'] == 'INTERRUPTED' and result['calls'][0]['status'] == 'RESERVED'
    assert store.read_checkpoint(scope).status == 'STOPPED'
    with pytest.raises(PermissionError): restarted.guard(scope)
    assert store.latest_run(sid) is None


def test_second_runtime_does_not_interrupt_a_live_research_owner(tmp_path, monkeypatch):
    from app.comsol.owned_process import OwnedProcess
    from app.execution.expert_contracts import ExpertScope
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    sid = store.create('workbench')['id']
    studies = ResearchStore(store)
    scope = ExpertScope(project_id='workbench', session_id=sid, case_id='owned', plan_id='research-owned',
                        plan_revision=1, run_id='research-owned', attempt_id='attempt')
    studies.create(scope, 'request', {'goal': 'Heat'})
    with store._connect() as db:
        db.execute('UPDATE research_studies SET launcher_json=?', (json.dumps({'pid': 987654321, 'created': 123}),))
    monkeypatch.setattr(OwnedProcess, 'identity_alive', lambda _: True)
    assert studies.interrupt_active() == 0 and studies.read('owned')['status'] == 'ACTIVE'
    monkeypatch.setattr(OwnedProcess, 'identity_alive', lambda _: False)
    assert studies.interrupt_active() == 1 and studies.read('owned')['status'] == 'INTERRUPTED'


def test_research_schema_migration_is_atomic_for_eight_store_instances(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = SessionStore(tmp_path/'sessions.db'); store.initialize()
    def initialize(_):
        peer = ResearchStore(SessionStore(store.path))
        with peer.sessions._connect() as db:
            return {row['name'] for row in db.execute('PRAGMA table_info(research_studies)')}
    with ThreadPoolExecutor(max_workers=8) as pool:
        columns = list(pool.map(initialize, range(8)))
    assert all({'launcher_json', 'proposal_case_id'} <= names for names in columns)
