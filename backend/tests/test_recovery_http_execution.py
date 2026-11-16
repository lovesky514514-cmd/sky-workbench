import asyncio
import json

import httpx
import pytest

from app.comsol.java_bridge import JavaBridgeClient
from app.recovery.http_execution import HTTP_RECOVERY, HttpRetrySession
from app.recovery.ingest import digest
from app.session.store import SessionStore


@pytest.fixture
def prepared(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('project')['id']
    store.set_plan(sid, {'plan_id':'plan','revision':1})
    return store, store.recovery_scope(session_id=sid)


async def no_wait(seconds):
    pass


@pytest.mark.asyncio
async def test_429_and_503_share_three_send_budget_and_close_responses(prepared):
    store, scope = prepared
    sends, sleeps = [], []

    def handler(request):
        sends.append(request)
        return httpx.Response([429,503,200][len(sends)-1], headers={'Retry-After':'0'}, json={'connected':True})

    async def wait(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(seconds)

    bridge = JavaBridgeClient('http://bridge', httpx.MockTransport(handler), token_provider=lambda:'a'*64)
    retry = HttpRetrySession(store, scope, 'op', sleep=wait, jitter=lambda a,b:b)
    token = HTTP_RECOVERY.set(retry)
    try:
        assert (await bridge._request('GET','/health', retries=2))['connected'] is True
        with pytest.raises(ValueError, match='not_replayable'):
            await bridge._request('GET','/health', retries=2)
    finally:
        HTTP_RECOVERY.reset(token)
    row = store.http_operation('op',scope)
    assert len(sends) == row['used'] == 3 and len(sleeps) == 2
    assert [x['observation']['status'] for x in json.loads(row['audit_json'])] == [429,503,200]


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [('POST','/model/study/run'),('POST','/model/parameter'),
    ('DELETE','/model/parameter'),('GET','/model/heat-field')])
async def test_bridge_mutations_and_run_never_use_hidden_retries(method,path):
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('private')
    bridge = JavaBridgeClient('http://bridge',httpx.MockTransport(fail),token_provider=lambda:'a'*64)
    with pytest.raises(httpx.ReadTimeout):
        await bridge._request(method,path,retries=2)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancel_during_cooldown_prevents_second_send(prepared):
    store, scope = prepared
    waiting = asyncio.Event()
    calls = []
    async def wait(seconds):
        waiting.set()
        await asyncio.Future()
    async def send():
        calls.append(1)
        return httpx.Response(429,headers={'Retry-After':'1'})
    retry = HttpRetrySession(store,scope,'cancel',sleep=wait)
    task = asyncio.create_task(retry.request('GET','/health',send))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [1] and store.http_operation('cancel',scope)['status'] == 'CANCELLED'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revision','owner','cancel','attempt'])
async def test_state_change_during_wait_refuses_resend(prepared, change):
    store, scope = prepared
    if change in {'cancel','attempt'}:
        store.begin_run(scope.session_id,'run',{})
        attempt = store.begin_attempt('run')
        scope = store.recovery_scope(session_id=scope.session_id,run_id='run',attempt_id=attempt)
    calls = []
    async def wait(seconds):
        if change == 'revision':
            store.set_plan(scope.session_id,{'plan_id':'plan','revision':2})
        elif change == 'owner':
            store.begin_run(scope.session_id,'new-owner',{})
        elif change == 'cancel':
            store.request_cancel(scope.session_id)
        else:
            store.end_attempt('run',scope.scientific_attempt_id,'RetryableFailure')
            store.begin_attempt('run')
    async def send():
        calls.append(1)
        return httpx.Response(503)
    with pytest.raises(ValueError):
        await HttpRetrySession(store,scope,'change',sleep=wait).request('GET','/health',send)
    assert calls == [1]


@pytest.mark.asyncio
async def test_large_retry_after_stops_instead_of_retrying_early(prepared):
    store, scope = prepared
    calls = []
    async def send():
        calls.append(1)
        return httpx.Response(503,headers={'Retry-After':'99999999'})
    with pytest.raises(TimeoutError):
        await HttpRetrySession(store,scope,'long-wait').request('GET','/health',send)
    assert calls == [1] and store.http_operation('long-wait',scope)['status'] == 'STOPPED'


def test_restart_cannot_reset_budget_or_replay_in_flight_send(prepared):
    store, scope = prepared
    store.open_http_operation('old',scope,digest(['GET','/health']))
    store.claim_http_send('old',scope)
    restarted = SessionStore(store.path)
    restarted.initialize()
    row = restarted.open_http_operation('old',scope,digest(['GET','/health']),maximum=3)
    assert row['used'] == 1 and row['active'] == 1
    with pytest.raises(ValueError,match='not_replayable'):
        restarted.claim_http_send('old',scope)
    other = restarted.create('other')['id']
    with pytest.raises(ValueError,match='identity'):
        restarted.open_http_operation('old',restarted.recovery_scope(session_id=other),digest(['GET','/health']))


def test_other_caller_cannot_skip_persisted_cooldown(prepared):
    from app.recovery.http_contracts import HttpObservation, OperationKind
    from app.recovery.http_policy import decide
    store,scope=prepared
    store.open_http_operation('wait',scope,digest(['GET','/health']))
    store.claim_http_send('wait',scope)
    observation=HttpObservation(status=429,operation_kind=OperationKind.READ,retry_after_seconds=10)
    store.record_http_attempt('wait',scope,observation,decide(observation),retry=True,delay=10)
    with pytest.raises(ValueError,match='cooldown_active'):
        store.claim_http_send('wait',scope)


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[1,3,8])
async def test_concurrent_scoped_requests_do_not_share_identity_or_replay(prepared,count):
    store, scope = prepared
    calls = {}
    async def one(index):
        sid = store.create('project')['id']
        current = store.recovery_scope(session_id=sid)
        async def send():
            calls[sid] = calls.get(sid,0)+1
            await asyncio.sleep(0)
            return httpx.Response(200)
        await HttpRetrySession(store,current,f'op-{index}').request('GET','/health',send)
        assert store.http_operation(f'op-{index}',current)['used'] == 1
        with pytest.raises(KeyError):
            store.http_operation(f'op-{index}',scope)
    await asyncio.gather(*(one(i) for i in range(count)))
    assert len(calls) == count and set(calls.values()) == {1}


def test_standalone_bridge_read_uses_existing_owner_and_live_identity(tmp_path,monkeypatch):
    import os
    from app.comsol.bridge_process import java_bridge_process_manager
    from app.comsol.owned_process import OwnedProcess
    import app.config as config
    store=SessionStore(tmp_path/'bridge.db')
    store.initialize()
    sid=store.create('workbench')['id']
    scope=store.recovery_scope(session_id=sid)
    model=tmp_path/'model.mph'
    model.write_bytes(b'test ownership only')
    monkeypatch.setattr(config,'WORKSPACE_ROOT',tmp_path)
    state={'owner_id':'bridge','running':True,'pid':os.getpid(),'model_path':str(model),'state':'ready'}
    monkeypatch.setattr(java_bridge_process_manager,'status',lambda:state)
    store.acquire_bridge_owner('bridge')
    identity=OwnedProcess.identify(os.getpid())
    store.record_bridge_process('bridge',identity)
    row=store.open_http_operation('bridge-read',scope,digest(['GET','/health']))
    assert row['owner_sha'] and store.claim_http_send('bridge-read',scope)==1
    # 同一个 owner 名称也不能掩盖进程身份或工作目录变化。
    state['model_path']=str(tmp_path.parent/'outside.mph')
    with pytest.raises(ValueError):
        store.open_http_operation('outside',scope,digest(['GET','/health']))
    state['model_path']=str(model)
    state['pid']=identity['pid']+12345
    with pytest.raises(ValueError):
        store.open_http_operation('forged',scope,digest(['GET','/health']))
