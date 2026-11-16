"""只读连接必须选择当前任务的最终模型，不能靠目录时间猜测。"""
import hashlib

import httpx
import pytest

from app.comsol.bridge_preview import completed_model
from app.session.store import SessionStore


@pytest.fixture
def model_case(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('workbench')['id']
    store.begin_run(sid, 'preview-run', {})
    attempt = store.begin_attempt('preview-run')
    model = tmp_path / 'models' / 'attempts' / 'preview-run' / attempt / 'models' / 'test.mph'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'frozen completed model')
    record = {'path': str(model), 'size_bytes': model.stat().st_size,
              'sha256': hashlib.sha256(model.read_bytes()).hexdigest()}
    store.update_run('preview-run', attempt, artifacts={'model': str(model)})
    store.finish_run('preview-run', attempt, 'Completed')
    evidence = {'passed': True, 'run_id': 'preview-run', 'attempt_id': attempt,
                'artifacts': {'model': record}}
    store.append_event(sid, 'run.completed', {'run_id': 'preview-run', 'attempt_id': attempt,
                                            'evidence_contract': evidence})
    return store, sid, model, tmp_path, evidence


def test_selects_nested_final_model_without_top_level_glob(model_case):
    store, sid, model, root, _ = model_case
    (root / 'models' / 'unrelated.mph').write_bytes(b'newer but unrelated')
    assert completed_model(store, sid, 'workbench', root) == model
    assert len(store.latest_run(sid)['attempts']) == 1


@pytest.mark.parametrize('failure', ['foreign_project', 'no_active_project', 'missing', 'modified',
                                     'stale_attempt', 'wrong_evidence', 'escape', 'new_run'])
def test_rejects_wrong_scope_or_unproven_model(model_case, failure, monkeypatch):
    store, sid, model, root, evidence = model_case
    project = 'workbench'
    if failure == 'foreign_project': project = 'external'
    elif failure == 'no_active_project': project = None
    elif failure == 'missing': model.unlink()
    elif failure == 'modified': model.write_bytes(b'changed')
    elif failure == 'new_run': store.begin_run(sid, 'newer-unfinished-run', {})
    elif failure == 'escape':
        root = root / 'other-workspace'
    else:
        evidence['attempt_id' if failure == 'stale_attempt' else 'run_id'] = 'wrong'
        monkeypatch.setattr(store, 'completed_evidence', lambda _: evidence)
    with pytest.raises((KeyError, ValueError)):
        completed_model(store, sid, project, root)


@pytest.mark.asyncio
async def test_api_resolves_task_then_reuses_existing_bridge_manager(model_case, monkeypatch):
    import app.main as main

    store, sid, model, root, _ = model_case
    monkeypatch.setattr(main, 'session_store', store)
    monkeypatch.setattr(main, 'WORKSPACE_ROOT', root)
    monkeypatch.setattr(main.project_service, 'projects', lambda: [{'id': 'workbench', 'active': True}])
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', 'preview-test-token')
    calls = []

    async def start(path):
        calls.append(path)
        return {'state': 'starting'}

    monkeypatch.setattr(main.java_bridge_process_manager, 'start', start)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test',
                                 headers={'X-Workbench-Token': 'preview-test-token'}) as client:
        response = await client.post('/api/comsol/bridge/start', json={'session_id': sid})
        assert response.status_code == 202
        assert calls == [str(model)]
        response = await client.post('/api/comsol/bridge/start', json={'session_id': sid, 'model_path': str(model)})
        assert response.status_code == 400
        monkeypatch.setattr(main.java_bridge_process_manager, 'status', lambda: {
            'running': True, 'state': 'ready', 'model_path': str(root / 'models' / 'other.mph')})
        assert (await client.post('/api/comsol/bridge/start', json={'session_id': sid})).status_code == 409
        monkeypatch.setattr(main.project_service, 'projects', lambda: [{'id': 'other', 'active': True}])
        assert (await client.post('/api/comsol/bridge/start', json={'session_id': sid})).status_code == 404
    assert calls == [str(model)]
