import asyncio
import json

import httpx
import pytest

from app.comsol.java_bridge import JavaBridgeClient
from app.recovery.classifier import RUN
from app.recovery.connection_probe import probe_connection
from app.recovery.ingest import Signal, capture, digest
from app.recovery.schemas import Origin
from app.recovery.service import RecoveryService
from app.recovery.store import RecoveryConflict
from app.session.store import SessionStore


@pytest.fixture
def setup(tmp_path):
    store = SessionStore(tmp_path/'sessions.db')
    store.initialize()
    sid = store.create('project')['id']
    store.set_plan(sid,{'plan_id':'plan','revision':1})
    case = capture(store,Signal(Origin.BRIDGE,'bridge_request',method='GET',route='/health',status=503),session_id=sid)
    return store,sid,case,tmp_path/'scratch'


@pytest.mark.asyncio
@pytest.mark.parametrize('connected',[True,False,'true'])
async def test_connection_probe_uses_n02_and_independent_facts_not_agent_success(setup,connected):
    store,sid,case,scratch = setup
    await RecoveryService(store,scratch).diagnose(case,'project',sid)
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'connected':connected,'real_comsol':True,'comsol_api_loaded':True,
                                       'model_connected':True,'success':True,'scientific_validation_passed':True,
                                       'detail':'C:/private/token secret'})
    bridge = JavaBridgeClient('http://bridge',httpx.MockTransport(handler),token_provider=lambda:'a'*64)
    result = await probe_connection(store,scratch,case,'project',sid,bridge)
    verification = [e['payload_json'] for e in result['events'] if e['kind']=='connection_verification']
    assert len(verification) == 1 and verification[0]['connection_verified'] == (connected is True)
    assert verification[0]['scientific_validation_passed'] is False
    assert result['state'] == 'PROPOSED' and store.latest_run(sid) is None
    assert 'private' not in json.dumps(result)
    repeated = await probe_connection(store,scratch,case,'project',sid,bridge)
    assert calls == ['/health'] and repeated == result
    snapshots = {e['payload_json']['snapshot_id'] for e in result['events'] if e['kind']=='connection_snapshot'}
    assert {verification[0]['evidence_ref'],verification[0]['verification_ref']} <= snapshots


@pytest.mark.asyncio
async def test_probe_rejects_cancelled_case_and_version_change(setup):
    store,sid,case,scratch = setup
    service = RecoveryService(store,scratch)
    await service.diagnose(case,'project',sid)
    service.cancel(case,'project',sid)
    with pytest.raises(RecoveryConflict):
        await probe_connection(store,scratch,case,'project',sid,None)


@pytest.mark.asyncio
@pytest.mark.parametrize('code',['tls_certificate_invalid','403'])
async def test_http_hard_stop_cannot_enter_connection_execution(setup,code):
    store,sid,_,scratch=setup
    signal=(Signal(Origin.RUNTIME,'readback','tls_certificate_invalid') if code!='403'
            else Signal(Origin.API,'api_request',method='GET',route='/api/health',status=403))
    case=capture(store,signal,session_id=sid)
    result=await RecoveryService(store,scratch).diagnose(case,'project',sid)
    assert result['state']=='BLOCKED' and result['proposal'] is None
    with pytest.raises(RecoveryConflict):
        await probe_connection(store,scratch,case,'project',sid,None)


@pytest.mark.asyncio
async def test_cancel_stops_owned_probe_and_rejects_late_result(setup):
    store,sid,case,scratch=setup
    service=RecoveryService(store,scratch)
    await service.diagnose(case,'project',sid)
    entered=asyncio.Event()
    calls=[]
    async def handler(request):
        calls.append(1)
        entered.set()
        await asyncio.Future()
    bridge=JavaBridgeClient('http://bridge',httpx.MockTransport(handler),token_provider=lambda:'a'*64)
    task=asyncio.create_task(service.check_connection(case,'project',sid,bridge))
    await entered.wait()
    service.cancel(case,'project',sid)
    result=await task
    assert result['state']=='CANCELLED' and calls==[1]
    assert not any(e['kind']=='connection_verification' for e in result['events'])


@pytest.mark.asyncio
async def test_authenticated_429_412_and_validation_are_safe_and_traceable(tmp_path,monkeypatch):
    import app.main as main
    from fastapi import HTTPException
    store = SessionStore(tmp_path/'api.db')
    store.initialize()
    sid = store.create('project')['id']
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN','test-token')
    monkeypatch.setattr(main,'session_store',store)
    monkeypatch.setattr(main.project_service,'projects',lambda:[{'id':'project','active':True}])
    headers={'X-Workbench-Token':'test-token'}
    # 使用已有路由，模拟域服务异常，不在产品中增加测试端点。
    async def limited(*args,**kwargs):
        raise HTTPException(429,detail='Bearer private-secret',headers={'Retry-After':'3'})
    monkeypatch.setattr(main.agent_runtime,'complete_plan',limited)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test',headers=headers) as client:
        path=f'/api/sessions/{sid}/plan/completion'
        response=await client.post(path,json={'proposal_id':'p','expected_revision':1,'reviewed_digest':'a'*64})
        assert response.status_code == 429 and len(response.headers['X-SKY-Diagnostic-ID']) == 32
        assert 'private-secret' not in response.text
        records=(await client.get('/api/recovery/cases',params={'session_id':sid})).json()['cases']
        assert records[0]['failure']['facts']['http']['retry_after_seconds'] == 3
        assert 'private-secret' not in json.dumps(records)
        case=records[0]['case_id']
        result=await client.post(f'/api/recovery/cases/{case}/diagnose',params={'session_id':sid},json={})
        assert result.json()['report']['rule']['http']['safe_to_retry'] is False
        invalid=await client.post(path,json={'proposal_id':{'token':'super-private-input'}})
        assert invalid.status_code == 422 and 'super-private-input' not in invalid.text
        assert store.latest_run(sid) is None


@pytest.mark.asyncio
async def test_api_connection_action_is_project_scoped_and_idempotent(tmp_path,monkeypatch):
    import app.main as main
    from app.comsol import java_bridge
    store=SessionStore(tmp_path/'api.db')
    store.initialize()
    sid=store.create('project')['id']
    other=store.create('other')['id']
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN','test-token')
    monkeypatch.setattr(main,'session_store',store)
    monkeypatch.setattr(main.project_service,'projects',lambda:[{'id':'project','active':True}])
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={k:True for k in ('connected','real_comsol','comsol_api_loaded','model_connected')})
    monkeypatch.setattr(java_bridge,'java_bridge_client',JavaBridgeClient('http://bridge',httpx.MockTransport(handler),token_provider=lambda:'a'*64))
    case=capture(store,Signal(Origin.BRIDGE,'bridge_request',method='GET',route='/health',status=503),session_id=sid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test',headers={'X-Workbench-Token':'test-token'}) as client:
        path=f'/api/recovery/cases/{case}'
        assert (await client.post(path+'/diagnose',params={'session_id':sid},json={})).status_code == 200
        assert (await client.post(path+'/connection-check',params={'session_id':other})).status_code == 404
        first=await client.post(path+'/connection-check',params={'session_id':sid})
        assert first.status_code == 200,first.text
        second=await client.post(path+'/connection-check',params={'session_id':sid})
        assert second.json() == first.json() and calls == ['/health']
        exported=(await client.get(path+'/export',params={'session_id':sid})).json()['case']
        assert all(e['sha256']==digest(e['payload_json']) for e in exported['events'])


@pytest.mark.asyncio
@pytest.mark.parametrize('allow_retry',[True,False])
async def test_diagnostic_model_request_can_disable_nested_json_retry(allow_retry, tmp_path):
    from app.config import SettingsStore
    from app.providers.manager import ProviderManager
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"candidates":[]}'},
                                                   'finish_reason':'length' if len(calls)==1 else 'stop'}]})
    config=SettingsStore(tmp_path/'settings.json')
    config.update({'providers':{'deepseek':{'base_url':'https://provider.test','model':'deepseek-chat',
                                           'api_key':'fake-test-key'}}})
    manager=ProviderManager(config,httpx.MockTransport(handler))
    kwargs=dict(temperature=0,json_mode=True,detected_model='deepseek-chat',allow_json_retry=allow_retry)
    if allow_retry:
        await manager._complete_one('deepseek',[],**kwargs)
    else:
        with pytest.raises(ValueError):
            await manager._complete_one('deepseek',[],**kwargs)
    assert len(calls)==(2 if allow_retry else 1)
