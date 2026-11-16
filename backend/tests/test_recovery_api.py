import json

import httpx
import pytest

from app.recovery.classifier import RUN
from app.recovery.ingest import digest
from app.session.store import PlanRevisionConflict, SessionStore


@pytest.fixture
def recovery_api(tmp_path, monkeypatch):
    import app.main as main

    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('workbench')['id']
    other = store.create('external')['id']
    store.set_plan(sid, {'plan_id': 'plan', 'revision': 2})
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', 'mock-test-token')
    monkeypatch.setattr(main, 'session_store', store)
    monkeypatch.setattr(main.project_service, 'projects', lambda: [{'id': 'workbench', 'active': True}])
    return main, store, sid, other


@pytest.mark.asyncio
async def test_real_api_409_stays_409_and_exports_only_safe_evidence(recovery_api, monkeypatch):
    main, store, sid, other = recovery_api

    async def conflict(*args, **kwargs):
        raise PlanRevisionConflict('Reviewed values changed; reload the current proposal')

    monkeypatch.setattr(main.agent_runtime, 'complete_plan', conflict)
    headers = {'X-Workbench-Token': 'mock-test-token'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post(f'/api/sessions/{sid}/plan/completion?secret=private-query', headers=headers,
                                     json={'proposal_id': 'private-proposal', 'expected_revision': 1,
                                           'reviewed_digest': 'a' * 64})
        assert response.status_code == 409
        assert response.json() == {'detail': 'Reviewed values changed; reload the current proposal'}
        path = f'/api/recovery/cases?session_id={sid}'
        assert (await client.get(path)).status_code == 401
        records = (await client.get(path, headers=headers)).json()['cases']
        assert len(records) == 1
        case = records[0]['case_id']
        details = f'/api/recovery/cases/{case}'
        assert (await client.get(details, params={'session_id': other}, headers=headers)).status_code == 404
        diagnosis = await client.post(details + '/diagnose', params={'session_id': sid}, headers=headers, json={})
        assert diagnosis.status_code == 200, diagnosis.text
        assert diagnosis.json()['report']['rule']['classification'] == 'PLAN_REVISION_CONFLICT'
        proposal = diagnosis.json()['proposal']
        scope = {'session_id': sid}
        assert (await client.post(details + '/submit', params=scope, headers=headers)).status_code == 200
        assert (await client.post(details + '/simulate', params=scope, headers=headers)).status_code == 409
        approval = await client.post(details + '/review', params=scope, headers=headers,
                                     json={'decision': 'approve', 'proposal_digest': proposal['digest']})
        assert approval.status_code == 200
        result = await client.post(details + '/simulate', params=scope, headers=headers)
        assert result.json()['state'] == 'RESOLVED'
        assert store.latest_run(sid) is None
        report = await client.get(details + '/export', params={'session_id': sid}, headers=headers)
        assert report.status_code == 200
        assert report.headers['cache-control'] == 'no-store'
        for sensitive in ['mock-test-token', 'private-query', 'private-proposal', 'a' * 64]:
            assert sensitive not in report.text
        for item in report.json()['case']['events']:
            assert item['sha256'] == digest(item['payload_json'])


@pytest.mark.asyncio
async def test_bridge_failure_observability_keeps_original_no_retry(recovery_api, monkeypatch):
    from app.comsol.java_bridge import JavaBridgeClient

    main, store, sid, _ = recovery_api
    calls = 0

    def fail(request):
        nonlocal calls
        calls += 1
        assert request.url.path == RUN
        return httpx.Response(409, json={'command_success': True, 'model_state_verified': False,
                                         'detail': 'C:/private/credential text'})

    bridge = JavaBridgeClient(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(fail),
                              token_provider=lambda: 'a' * 64)

    async def route(*args):
        return await bridge.run_study()

    monkeypatch.setattr(main.agent_runtime, 'correct_live_boundary', route)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test',
                                 headers={'X-Workbench-Token': 'mock-test-token'}) as client:
        response = await client.post(f'/api/sessions/{sid}/run/boundary-correction', json={'review_id': 'test-review'})
        assert response.status_code == 409 and calls == 1
        cases = (await client.get('/api/recovery/cases', params={'session_id': sid})).json()['cases']
        assert {c['failure']['origin'] for c in cases} == {'API', 'JAVA_BRIDGE'}
        api_case, = [c['failure'] for c in cases if c['failure']['origin'] == 'API']
        bridge_case, = [c['failure'] for c in cases if c['failure']['origin'] == 'JAVA_BRIDGE']
        assert bridge_case['parent_event_id'] == api_case['event_id']
        assert bridge_case['correlation_id'] == api_case['correlation_id']
        assert 'credential' not in json.dumps(cases) and 'a' * 64 not in json.dumps(cases)
        response = await client.post(f"/api/recovery/cases/{bridge_case['event_id']}/diagnose",
                                     params={'session_id': sid}, json={})
        assert response.json()['report']['rule']['classification'] == 'SOLVE_STATE_UNVERIFIED'


@pytest.mark.asyncio
async def test_capture_failure_does_not_replace_original_http_response(recovery_api, monkeypatch):
    import sqlite3

    main, store, sid, _ = recovery_api

    async def reject(*args):
        raise PlanRevisionConflict('Plan changed; reload the current revision before saving')

    def failed(*args):
        raise sqlite3.OperationalError('unavailable private-path')

    monkeypatch.setattr(main.agent_runtime, 'update_plan', reject)
    monkeypatch.setattr(store, 'ingest_recovery', failed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test',
                                 headers={'X-Workbench-Token': 'mock-test-token'}) as client:
        response = await client.put(f'/api/sessions/{sid}/plan', json={'plan': {}})
        assert response.status_code == 409 and 'private-path' not in response.text
    assert store.recovery_drops == 1


@pytest.mark.asyncio
async def test_artifact_conflict_preserves_bytes_and_records_frozen_identity(recovery_api, monkeypatch):
    from hashlib import sha256

    main, store, sid, _ = recovery_api
    root = store.path.parent
    monkeypatch.setattr(main, 'WORKSPACE_ROOT', root)
    run_id = 'fixture-run'
    store.begin_run(sid, run_id, {})
    attempt = store.begin_attempt(run_id)
    artifact = root / 'models' / 'attempts' / run_id / attempt / 'temperature.png'
    artifact.parent.mkdir(parents=True)
    original, current = b'original fixture', b'changed fixture'
    artifact.write_bytes(current)
    store.update_run(run_id, attempt, artifacts={'temperature': str(artifact)})
    store.finish_run(run_id, attempt, 'Completed')
    store.append_event(sid, 'run.completed', {
        'run_id': run_id, 'attempt_id': attempt,
        'evidence_contract': {'passed': True, 'artifacts': {'temperature': {
            'path': str(artifact), 'size_bytes': len(original), 'sha256': sha256(original).hexdigest(),
        }}},
    })
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test',
                                 headers={'X-Workbench-Token': 'mock-test-token'}) as client:
        response = await client.get(f'/api/artifacts/{run_id}/temperature.png')
        assert response.status_code == 409
        cases = (await client.get('/api/recovery/cases', params={'session_id': sid})).json()['cases']
    failure, = [row['failure'] for row in cases]
    assert failure['scope']['scientific_run_id'] == run_id
    assert failure['scope']['scientific_attempt_id'] == attempt
    assert failure['facts']['artifact_sha256'] == sha256(current).hexdigest()
    assert failure['facts']['artifact_expected_sha256'] == sha256(original).hexdigest()
    assert str(artifact) not in json.dumps(failure)
    assert artifact.read_bytes() == current
