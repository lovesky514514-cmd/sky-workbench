import asyncio

import httpx
import pytest
import pytest_asyncio

from app.agent.runtime import AgentRuntime
from app.execution.heat_simulation import HeatSimulationService
from app.session.store import SessionStore

TOKEN = {'X-Workbench-Token': 'simulation-tests-token'}


@pytest_asyncio.fixture
async def desktop(tmp_path, monkeypatch):
    import app.main as main

    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    service = HeatSimulationService(store)
    runtime = AgentRuntime(sessions=store)

    async def forbidden(*args, **kwargs):
        pytest.fail('The simulated product path tried to call a live executor or model')

    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', TOKEN['X-Workbench-Token'])
    monkeypatch.setattr(main, 'session_store', store)
    monkeypatch.setattr(main, 'heat_simulations', service)
    monkeypatch.setattr(main, 'agent_runtime', runtime)
    monkeypatch.setattr(main, '_diagnostic_project', lambda: 'project')
    monkeypatch.setattr(main.project_service, 'projects', lambda: [{'id': 'project', 'active': True}])
    monkeypatch.setattr(runtime, '_start_run', forbidden)
    monkeypatch.setattr(main.provider_manager, 'complete', forbidden)
    monkeypatch.setattr(main.java_bridge_client, 'run_study', forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://desktop',
                                 headers=TOKEN) as client:
        yield client, store, main
    await service.shutdown()
    if main.heat_simulations is not service:
        await main.heat_simulations.shutdown()


async def new_session(client, project='project'):
    reply = await client.post('/api/sessions', json={'project_id': project, 'title': 'Heat fixture'})
    assert reply.status_code == 200
    return reply.json()['id']


def route(sid):
    return f'/api/sessions/{sid}/heat-simulation'


async def start(client, sid, scenario):
    result = await client.post(route(sid) + '/start', json={'scenario': scenario})
    assert result.status_code == 202, result.text
    assert result.json()['mode'] == 'simulation'


async def until(client, sid, state):
    async with asyncio.timeout(4):
        while True:
            result = (await client.get(route(sid))).json()
            if result['state'] == state:
                return result
            assert result['state'] in {'RUNNING', 'INTERRUPTED'}, result
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(('scenario', 'state', 'claim', 'outcome'), [
    ('normal', 'PASSED', 'temperature', 'PASS'),
    ('wrong_boundary', 'BLOCKED', 'boundary_selection', 'FAIL'),
    ('missing_evidence', 'UNVERIFIED', 'temperature', 'UNVERIFIED'),
])
@pytest.mark.asyncio
async def test_product_api_returns_independent_outcome_without_real_publication(desktop, scenario, state, claim, outcome):
    client, store, _ = desktop
    sid = await new_session(client)
    assert (await client.get(route(sid))).json()['can_start']
    await start(client, sid, scenario)
    run = await until(client, sid, state)
    report = run['report']
    check = next(item for item in report['claims'] if item['claim']['claim_id'] == claim)
    assert check['status'] == outcome
    assert run['scientific_validation_passed'] is False and store.latest_run(sid) is None
    session = (await client.get(f'/api/sessions/{sid}')).json()
    assert session['plan']['source'] == 'heat-simulation' and session['plan']['runnable'] is False
    assert session['state'] == 'PLAN_READY'
    assert not any(event['type'] == 'run.completed' for event in session['events'])
    if scenario == 'wrong_boundary':
        assert report['score_total'] == 100 and check['counterevidence_refs']
        execution = next(item for item in report['claims'] if item['claim']['claim_id'] == 'execution')
        assert execution['status'] == 'PASS'
    if scenario == 'normal':
        forged_run = await client.post(f'/api/sessions/{sid}/messages', json={'mode': 'RUN', 'content': 'Run'})
        assert forged_run.status_code == 200 and store.latest_run(sid) is None
        assert (await client.put(f'/api/sessions/{sid}/plan', json={'plan': session['plan']})).status_code == 409
        with pytest.raises(RuntimeError, match='simulation_only_session'):
            store.begin_run(sid, 'live-attempt', session['plan'])


@pytest.mark.asyncio
async def test_cancel_and_safe_restart_use_persisted_checkpoint(desktop, monkeypatch):
    client, store, main = desktop
    cancelled_sid = await new_session(client)
    await start(client, cancelled_sid, 'interrupted')
    paused = await until(client, cancelled_sid, 'PAUSED')
    response = await client.post(route(cancelled_sid) + '/cancel', json={'version': paused['version']})
    assert response.json()['state'] == 'CANCELLED'
    assert response.json()['tasks'][2] == {'id': 'solve', 'state': 'NOT_DISPATCHED'}
    assert (await client.post(route(cancelled_sid) + '/resume',
                              json={'version': response.json()['version']})).status_code == 409

    sid = await new_session(client)
    await start(client, sid, 'interrupted')
    paused = await until(client, sid, 'PAUSED')
    assert paused['tasks'][1] == {'id': 'build', 'state': 'VERIFIED'}
    assert paused['tasks'][2] == {'id': 'solve', 'state': 'PENDING'}
    await main.heat_simulations.shutdown()
    reopened = SessionStore(store.path)
    reopened.initialize()
    monkeypatch.setattr(main, 'heat_simulations', HeatSimulationService(reopened))
    assert (await client.post(route(sid) + '/resume', json={'version': paused['version']})).status_code == 202
    result = await until(client, sid, 'PASSED')
    assert result['attempt_id'] != paused['attempt_id']
    claimed = [e['payload']['task_id'] for e in reopened.get(sid)['events'] if e['type'] == 'experts.task_claimed']
    assert claimed == ['research', 'build', 'solve', 'validate', 'export']


@pytest.mark.asyncio
async def test_revision_requires_matching_approval_and_reuses_unaffected_stages(desktop):
    client, store, _ = desktop
    sid = await new_session(client)
    await start(client, sid, 'revision')
    first = await until(client, sid, 'REVISION_REQUIRED')
    assert first['report']['score_total'] == 94
    assert (await client.post(route(sid) + '/resume', json={'version': first['version']})).status_code == 409
    approval = {'version': first['version'], 'proposal_id': first['report']['revision']['revision_id']}
    assert (await client.post(route(sid) + '/approve',
                              json={**approval, 'proposal_id': 'forged'})).status_code == 409
    assert (await client.post(route(sid) + '/approve', json=approval)).status_code == 202
    final = await until(client, sid, 'PASSED')
    assert final['attempt_id'] != first['attempt_id'] and final['report']['score_total'] == 100
    assert (await client.post(route(sid) + '/approve', json=approval)).status_code == 409
    events = store.get(sid)['events']
    assert [e['payload']['task_id'] for e in events if e['type'] == 'experts.task_claimed'] == [
        'research', 'build', 'solve', 'validate', 'export', 'solve', 'validate', 'export',
    ]
    assert [e['payload']['report']['status'] for e in events if e['type'] == 'heat_simulation.audit'] == [
        'REVISION_REQUIRED', 'PASS',
    ]


@pytest.mark.asyncio
async def test_unknown_submission_stops_and_project_boundary_is_enforced(desktop):
    client, store, _ = desktop
    foreign = await new_session(client, 'other-project')
    assert (await client.post(route(foreign) + '/start', json={'scenario': 'normal'})).status_code == 404
    sid = await new_session(client)
    assert (await client.get(route(sid), headers={'X-Workbench-Token': 'invalid'})).status_code == 401
    await start(client, sid, 'unknown_submission')
    stopped = await until(client, sid, 'STOPPED')
    assert stopped['reason'] == 'submission_unknown'
    assert (await client.post(route(sid) + '/resume', json={'version': stopped['version']})).status_code == 409
    events = store.get(sid)['events']
    assert [e['payload']['task_id'] for e in events if e['type'] == 'experts.task_claimed'].count('solve') == 1


@pytest.mark.asyncio
async def test_simulation_does_not_replace_an_existing_user_plan(desktop):
    client, store, _ = desktop
    sid = await new_session(client)
    plan = {'plan_id': 'existing-plan', 'revision': 1, 'runnable': True}
    store.set_plan(sid, plan)
    assert not (await client.get(route(sid))).json()['can_start']
    assert (await client.post(route(sid) + '/start', json={'scenario': 'normal'})).status_code == 409
    assert store.get(sid)['plan'] == plan and not store.has_heat_simulation(sid)


@pytest.mark.parametrize('shutdown', [False, True])
@pytest.mark.asyncio
async def test_live_simulation_cancellation_or_runtime_loss_never_publishes_a_late_solver_result(
    desktop, monkeypatch, shutdown,
):
    from app.execution.revision_coordinator import RevisionCoordinator

    client, store, main = desktop
    sid = await new_session(client)
    entered = asyncio.Event()
    dispatch = RevisionCoordinator.dispatch

    async def interrupted_dispatch(self, scope, *, execute, **kwargs):
        async def delayed(context):
            if context.task.task_id == 'solve':
                entered.set()
                await asyncio.Future()
            return await execute(context)
        return await dispatch(self, scope, execute=delayed, **kwargs)

    monkeypatch.setattr(RevisionCoordinator, 'dispatch', interrupted_dispatch)
    await start(client, sid, 'normal')
    await asyncio.wait_for(entered.wait(), 3)
    running = (await client.get(route(sid))).json()
    if shutdown:
        await main.heat_simulations.shutdown()
        reopened = SessionStore(store.path)
        reopened.initialize()
        monkeypatch.setattr(main, 'heat_simulations', HeatSimulationService(reopened))
        interrupted = (await client.get(route(sid))).json()
        assert interrupted['state'] == 'INTERRUPTED'
        assert (await client.post(route(sid) + '/resume', json={'version': interrupted['version']})).status_code == 202
        assert (await until(client, sid, 'STOPPED'))['reason'] == 'submission_unknown'
    else:
        stopped = await client.post(route(sid) + '/cancel', json={'version': running['version']})
        assert stopped.json()['state'] == 'CANCELLED'
    events = store.get(sid)['events']
    assert not any(e['type'] == 'heat_simulation.audit' for e in events)
    assert [e['payload']['task_id'] for e in events if e['type'] == 'experts.task_claimed'].count('solve') == 1
