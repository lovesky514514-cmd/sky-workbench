"""导出权限、冻结字节、旧状态、篡改与脱敏；输入均为合成账本。"""
import hashlib
import io
import json
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from app.orchestration.delivery_api import delivery_router
from app.orchestration.scientific_delivery import ScientificDelivery
from app.session.store import SessionStore


@pytest.fixture
def delivery(tmp_path):
    store=SessionStore(tmp_path/'sessions.db');store.initialize()
    sid=store.create('project')['id']
    store.begin_run(sid,'run',{})
    attempt=store.begin_attempt('run')
    model=tmp_path/'real-bytes.mph';model.write_bytes(b'synthetic-model-not-COMSOL')
    log=tmp_path/'worker.log'
    log.write_bytes('求解器日志\n'.encode('utf-8') + '原生诊断'.encode('gbk') + b'\napi_key=super-secret-credential')
    artifacts={'model':str(model),'execution_log':str(log)}
    manifest={key:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size_bytes':path.stat().st_size}
        for key,path in [('model',model),('execution_log',log)]}
    store.update_run('run',attempt,artifacts=artifacts)
    store.finish_run('run',attempt,'Completed')
    store.append_event(sid,'run.completed',{'run_id':'run','attempt_id':attempt,'execution_verdict':{'completed':True},
        'evidence_contract':{'passed':True,'run_id':'run','attempt_id':attempt,'artifacts':manifest}})
    return ScientificDelivery(store,tmp_path),store,sid,attempt,model


def test_verified_reproducible_archive_and_redaction(delivery):
    exporter,store,_,_,model=delivery
    path,sha=exporter.export('run','project')
    before=path.read_bytes()
    manifest=exporter.verify(path,sha)
    assert manifest['kind']=='scientific_run'
    assert path==exporter.export('run','project')[0] and path.read_bytes()==before
    with zipfile.ZipFile(path) as data:
        assert data.read('artifacts/model.mph')==model.read_bytes()
        exported_log = data.read('artifacts/execution_log.log').decode('utf-8')
        assert '求解器日志' in exported_log and '\ufffd' in exported_log
        assert 'super-secret-credential' not in exported_log
        original_log = model.with_name('worker.log').read_bytes()
        assert b'super-secret-credential' in original_log
        assert manifest['files']['artifacts/execution_log.log']['original_sha256'] == hashlib.sha256(original_log).hexdigest()
        assert json.loads(data.read('run.json'))['plan_snapshot_available'] is False
    assert len(store.get_run('run')['attempts'])==1


@pytest.mark.parametrize('damage',['file','old_attempt','cancel','outside','failed_verdict','archive'])
def test_rejects_corrupted_and_unverified_exports(delivery,damage,tmp_path):
    exporter,store,sid,attempt,model=delivery
    if damage=='file': model.write_bytes(b'changed')
    elif damage in {'old_attempt','cancel'}:
        with store._connect() as db:
            if damage=='old_attempt': db.execute("UPDATE run_journal SET attempt_id='old'")
            else: db.execute('UPDATE run_journal SET cancel_requested=1')
    elif damage=='outside': exporter.workspace=tmp_path/'other'
    elif damage=='failed_verdict':
        store.append_event(sid,'run.completed',{'run_id':'run','attempt_id':attempt,'execution_verdict':{'completed':False},
            'evidence_contract':{'passed':True,'run_id':'run','attempt_id':attempt,'artifacts':{}}})
    elif damage=='archive':
        path,_=exporter.export('run','project')
        with zipfile.ZipFile(path,'a') as data: data.writestr('unregistered.txt','injected')
    with pytest.raises(ValueError): exporter.export('run','project')


@pytest.mark.asyncio
async def test_http_project_scope_and_verified_zip(delivery,tmp_path):
    _,store,_,_,_=delivery
    project='other'
    app=FastAPI();app.include_router(delivery_router(lambda:store,tmp_path,lambda:project))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/scientific-runs/run/delivery')).status_code==404
        project='project'
        response=await client.get('/api/scientific-runs/run/delivery')
        assert response.status_code==200
        assert len(response.headers['X-Manifest-SHA256'])==64
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert 'manifest.json' in archive.namelist()
