import asyncio
import hashlib
import json
import sys
import uuid

from fastapi.testclient import TestClient
import pytest

from app import main
from app.engines.manager import EngineTaskManager
from app.engines.models import EngineTaskRequest
from app.session.store import SessionStore
from app.agent.runtime import AgentRuntime
from test_plan_completion import draft


def client(tmp_path, monkeypatch):
    manager = EngineTaskManager(tmp_path / 'runs', lambda engine: sys.executable,
                                execution_guard=lambda *args: None)
    monkeypatch.setattr(main, 'engine_tasks', manager)
    monkeypatch.setattr(main.project_service, 'projects', lambda: [
        {'id':'alpha','active':True,'read_only':False}, {'id':'beta','active':False,'read_only':False}])
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', 'fixture-v31')
    return TestClient(main.app, headers={'X-Workbench-Token':'fixture-v31'}), manager


def add_record(manager, project_id):
    run_id = str(uuid.uuid4())
    folder=manager.root/run_id;folder.mkdir(parents=True)
    data=b'controlled output\r\n'
    (folder/'log.lammps').write_bytes(data)
    manager._write({'run_id':run_id,'project_id':project_id,'state':'NEEDS_REVIEW','created_at':1,
                    'artifacts':[{'name':'log.lammps','size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]})
    return run_id, data


def test_api_binds_project_and_rejects_foreign_read_cancel_download(tmp_path, monkeypatch):
    api,manager=client(tmp_path,monkeypatch)
    own,_=add_record(manager,'alpha');foreign,_=add_record(manager,'beta')
    response=api.get('/api/engine-tasks?project_id=beta')
    assert [r['run_id'] for r in response.json()['runs']]==[own]
    assert api.get(f'/api/engine-tasks/{foreign}').status_code==404
    assert api.post(f'/api/engine-tasks/{foreign}/cancel').status_code==404
    assert api.get(f'/api/engine-tasks/{foreign}/artifacts/log.lammps').status_code==404


def test_api_download_returns_only_the_hashed_bytes(tmp_path,monkeypatch):
    api,manager=client(tmp_path,monkeypatch)
    own,data=add_record(manager,'alpha')
    response=api.get(f'/api/engine-tasks/{own}/artifacts/log.lammps')
    assert response.status_code==200 and response.content==data
    (manager.root/own/'log.lammps').write_bytes(b'changed')
    assert api.get(f'/api/engine-tasks/{own}/artifacts/log.lammps').status_code==404


def test_client_project_and_plan_approval_fields_are_not_authoritative(tmp_path,monkeypatch):
    api,manager=client(tmp_path,monkeypatch)
    async def capture(request):return {'project_id':request.project_id}
    monkeypatch.setattr(manager,'start',capture)
    response=api.post('/api/engine-tasks',json={'project_id':'beta','confirmed':True,'inputs':{'kind':'lammps_lj'}})
    assert response.status_code==200 and response.json()['project_id']=='alpha'
    store=SessionStore(tmp_path/'sessions.db');store.initialize();sid=store.create('alpha')['id']
    plan=draft();store.commit_plan_revision(sid,plan.model_dump(mode='json'),0)
    monkeypatch.setattr(main,'agent_runtime',AgentRuntime(sessions=store))
    payload=plan.model_dump(mode='json')
    payload['parameters']=payload['completion']['parameters'];payload['completion']=None
    for item in payload['parameters'].values():item.update(user_confirmed=True,use_authorized=True,source='user')
    saved=api.put(f'/api/sessions/{sid}/plan',json={'plan':payload})
    assert saved.status_code==200 and not saved.json()['plan']['runnable']
    assert api.put(f'/api/sessions/{sid}/plan',json={'plan':payload}).status_code==409
    assert api.post(f'/api/sessions/{sid}/plan/completion',json={'proposal_id':plan.completion.proposal_id,'mode':'review'}).status_code==422


def test_real_timeout_reaps_the_owned_child(tmp_path,monkeypatch):
    from app.engines import manager as module
    monkeypatch.setattr(module,'command_for',lambda *args:[sys.executable,'-c','import time; time.sleep(60)'])
    manager=EngineTaskManager(tmp_path,lambda engine:sys.executable,execution_guard=lambda *args:None)
    async def exercise():
        request=EngineTaskRequest(inputs={'kind':'lammps_lj'},confirmed=True,timeout_seconds=10)
        run=await manager.start(request);await manager.tasks[run['run_id']]
        result=manager.status(run['run_id'])
        assert result['state']=='TIMED_OUT' and result['cleanup']['reaped']
        assert not result['command_success'] and result['scientific_validation']=='NOT_EVALUATED'
    asyncio.run(exercise())


def test_project_parallelism_and_shutdown_keep_process_ownership(tmp_path,monkeypatch):
    from app.engines import manager as module
    monkeypatch.setattr(module,'command_for',lambda *args:[sys.executable,'-c','import time; time.sleep(60)'])
    manager=EngineTaskManager(tmp_path,lambda engine:sys.executable,execution_guard=lambda *args:None)
    async def exercise():
        first=await manager.start(EngineTaskRequest(project_id='alpha',inputs={'kind':'lammps_lj'},confirmed=True))
        second=await manager.start(EngineTaskRequest(project_id='beta',inputs={'kind':'lammps_lj'},confirmed=True))
        with pytest.raises(ValueError,match='already has an active'):
            await manager.start(EngineTaskRequest(project_id='alpha',inputs={'kind':'lammps_lj'},confirmed=True))
        for _ in range(100):
            if len(manager.processes)==2:break
            await asyncio.sleep(0.01)
        assert len(manager.processes)==2
        await manager.shutdown()
        for run in [first,second]:
            saved=manager.status(run['run_id'])
            assert saved['state']=='CANCELED' and saved['cleanup']['reaped']
        assert not manager.tasks and not manager.active_runs and not manager.processes
    asyncio.run(exercise())
