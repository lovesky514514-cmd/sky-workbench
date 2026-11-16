import asyncio
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.comsol.bridge_auth import new_bridge_token
from app.comsol.java_bridge import JavaBridgeClient
from app.comsol.worker_protocol import receive_control, resolve_worker_result
from app.execution.scientific_transfer import COOLING_COLUMNS, LIMITS, load_transfer, publish_transfer


IDENTITY = dict(run_id='case-run', attempt_id='case-attempt', producer='cooling_readback_worker')
KIND = 'cooling-readback.v1'


def profile(samples):
    return {'success': True, 'run_id': IDENTITY['run_id'], 'attempt_id': IDENTITY['attempt_id'],
            'saved_model_unchanged': True, 'solver_calls': 0, 'model_readback': {},
            'field_readback': {key: [i / 7.123456789 + n / 3.543219876 for i in range(samples)]
                               for n, key in enumerate(COOLING_COLUMNS)}}


def read(root, descriptor, **override):
    return load_transfer(root, descriptor, **{**IDENTITY, 'data_type': KIND, **override})


@pytest.mark.parametrize('samples', [2, 121, 1320, 5000])
def test_publish_load_and_duplicate(tmp_path, samples):
    data = profile(samples)
    receipt = publish_transfer(tmp_path, data, **IDENTITY, data_type=KIND)
    assert receipt.dimensions == [samples, 7] and receipt.sample_count == samples
    if samples >= 1320:
        assert receipt.byte_count > 65_536
    assert read(tmp_path, receipt.model_dump()) == data
    assert sorted(p.name for p in tmp_path.iterdir()) == ['result.json']
    with pytest.raises(FileExistsError):
        publish_transfer(tmp_path, data, **IDENTITY, data_type=KIND)
    assert read(tmp_path, receipt.model_dump()) == data


@pytest.mark.parametrize('change', ['zero', 'truncated', 'tampered', 'mislabeled', 'nan', 'duplicate_key', 'over-limit'])
def test_invalid_file_content(tmp_path, change):
    item = publish_transfer(tmp_path, profile(121), **IDENTITY, data_type=KIND).model_dump()
    path = tmp_path / 'result.json'
    original = path.read_bytes()
    if change == 'zero':
        path.write_bytes(b'')
    elif change == 'truncated':
        path.write_bytes(original[:-1])
    elif change == 'tampered':
        path.write_bytes(original.replace(b'0.0', b'1.0', 1))
    elif change == 'over-limit':
        item['byte_count'] = LIMITS[KIND] + 1
    else:
        invalid = {'mislabeled': b'\x89PNG\r\n', 'nan': original.replace(b'0.0', b'1e999', 1),
                   'duplicate_key': b'{"success":true,' + original[1:]}[change]
        path.write_bytes(invalid)
        item.update(byte_count=len(invalid), sha256=sha256(invalid).hexdigest())
    with pytest.raises(ValueError):
        read(tmp_path, item)


@pytest.mark.parametrize('change', [dict(relative_path='../result.json'), dict(relative_path='result.json:alt'),
    dict(relative_path='C:\\result.json'), dict(byte_count=0), dict(sample_count=0), dict(dimensions=[120, 7]),
    dict(media_type='application/octet-stream'), dict(producer='other'), dict(run_id='other'), dict(attempt_id='old')])
def test_descriptor_scope_and_shape(tmp_path, change):
    item = publish_transfer(tmp_path, profile(121), **IDENTITY, data_type=KIND)
    with pytest.raises((ValueError, PermissionError)):
        read(tmp_path, {**item.model_dump(), **change})


@pytest.mark.parametrize('invalid', ['empty', 'ragged', 'nonfinite', 'bad-time', 'too-many', 'solved', 'saved'])
def test_invalid_scientific_structure_is_never_published(tmp_path, invalid):
    data = profile(0 if invalid == 'empty' else 5001 if invalid == 'too-many' else 20)
    fields = data['field_readback']
    if invalid == 'ragged': fields['average_K'].pop()
    if invalid == 'nonfinite': fields['average_K'][0] = float('nan')
    if invalid == 'bad-time': fields['time_s'][1] = fields['time_s'][0]
    if invalid == 'solved': data['solver_calls'] = 1
    if invalid == 'saved': data['saved_model_unchanged'] = False
    with pytest.raises(ValueError):
        publish_transfer(tmp_path, data, **IDENTITY, data_type=KIND)
    assert not list(tmp_path.iterdir())


def test_symlink_and_hardlink_escape(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    item = publish_transfer(outside, profile(121), **IDENTITY, data_type=KIND)
    inside = tmp_path / 'scope'
    inside.mkdir()
    os.link(outside/'result.json', inside/'result.json')
    with pytest.raises(ValueError):
        read(inside, item.model_dump())
    (inside/'result.json').unlink()
    try:
        (inside/'result.json').symlink_to(outside/'result.json')
    except OSError:
        # Windows 普通令牌可能没有符号链接权限，仍已执行硬链接覆盖。
        return
    with pytest.raises(ValueError):
        read(inside, item.model_dump())


@pytest.mark.asyncio
async def test_bounded_control_and_exit(tmp_path):
    data = profile(1320)
    item = publish_transfer(tmp_path, data, **IDENTITY, data_type=KIND)
    message = {'success': True, 'data': item.model_dump()}
    frame = ('WORKBENCH_RESULT:' + json.dumps(message) + '\n').encode()
    assert len(frame) < 1024
    assert item.byte_count > 65_536
    received = []

    async def on_event(event): received.append(event)

    async def consume(raw):
        pipe = asyncio.StreamReader()
        pipe.feed_data(raw)
        pipe.feed_eof()
        return await receive_control(pipe, io.BytesIO(), on_event, run_id=IDENTITY['run_id'], attempt_id=IDENTITY['attempt_id'])

    legacy = ('WORKBENCH_RESULT:' + json.dumps(data) + '\n').encode()
    old_reader = asyncio.StreamReader()
    old_reader.feed_data(legacy[:70_000])
    with pytest.raises(ValueError, match='Separator is not found'):
        await old_reader.readline()
    with pytest.raises(ValueError, match='invalid_worker_control_frame'):
        await consume(legacy)
    with pytest.raises(ValueError, match='duplicate_worker_result'):
        await consume(frame * 2)
    with pytest.raises(ValueError, match='invalid_worker_control_frame'):
        await consume(frame.rstrip())
    receipt = await consume(b'long COMSOL diagnostic ' + b'x' * 200_000 + b'\nWORKBENCH_EVENT:{"phase":"Read"}\n' + frame)
    assert len(received) == 1 and received[0]['attempt_id'] == IDENTITY['attempt_id']
    assert resolve_worker_result(tmp_path, receipt, exit_code=0, **IDENTITY) == data
    with pytest.raises(RuntimeError, match='after publishing'):
        resolve_worker_result(tmp_path, receipt, exit_code=1, **IDENTITY)
    with pytest.raises(RuntimeError, match='without a result'):
        resolve_worker_result(tmp_path, None, exit_code=1, **IDENTITY)


@pytest.mark.asyncio
async def test_canceled_during_cleanup_cannot_publish_to_run(tmp_path, monkeypatch):
    from app.agent import tool_registry as module
    from app.agent.planner import extract_heat_transfer_plan
    from app.session.run_journal import RunCanceled
    from app.session.store import SessionStore

    monkeypatch.setattr(module, 'WORKSPACE_ROOT', tmp_path)
    store = SessionStore(tmp_path/'test.sqlite')
    store.initialize()
    session_id = store.create('workbench')['id']
    store.begin_run(session_id, 'run', {})
    attempt_id = store.begin_attempt('run')

    async def spawn(command, **_):
        request_path = Path(command[-1])
        item = publish_transfer(request_path.parent/'data', {'success': True, 'run_id': 'run',
            'attempt_id': attempt_id, 'artifacts': {'model': 'unaccepted'}}, run_id='run', attempt_id=attempt_id,
            producer='controlled', data_type='worker-result.v1')
        pipe = asyncio.StreamReader()
        pipe.feed_data(('WORKBENCH_RESULT:' + json.dumps({'success': True, 'data': item.model_dump()}) + '\n').encode())
        pipe.feed_eof()

        async def wait(): return 0

        async def terminate():
            store.request_cancel(session_id)
            return {'reaped': True}

        return SimpleNamespace(process=SimpleNamespace(pid=0, returncode=0, stdout=pipe, wait=wait), identity={}, terminate=terminate)

    monkeypatch.setattr(module.OwnedProcess, 'spawn', spawn)
    registry = module.ToolRegistry()
    registry.sessions = store

    async def execute(arguments, on_event): return await module.run_comsol_worker(arguments, on_event, 'controlled.py')
    async def event(_): pass

    registry.register(module.RegisteredTool('controlled', 'test', 'test', execute))
    plan = extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated')
    with pytest.raises(RunCanceled):
        await registry.execute('controlled', {'run_id': 'run', 'attempt_id': attempt_id, 'session_id': session_id, 'plan': plan}, event, retries=0)
    run = store.get_run('run')
    assert run['artifacts'] == {} and run['attempts'][0]['cleanup']['reaped'] and not registry._active_processes


@pytest.mark.asyncio
async def test_bridge_large_field_has_small_http_receipt(tmp_path):
    root = tmp_path/'models/attempts/run/attempt'
    samples = 20_000
    arrays = {'x': [i / 123.456 for i in range(samples)], 'y': [0.] * samples, 'temperature_K': [300.] * samples}

    def handler(request):
        request_id = request.url.params['request_id']
        item = publish_transfer(root/'bridge-readbacks'/request_id, arrays, run_id='run', attempt_id='attempt',
                                producer='java_bridge', data_type='bridge-heat-field.v1')
        assert item.byte_count > 65_536
        return httpx.Response(200, json={'request_id': request_id, 'sample_count': samples, 'data': item.model_dump()})

    checks = []
    client = JavaBridgeClient('http://127.0.0.1:1', httpx.MockTransport(handler), token_provider=new_bridge_token)
    result = await client.heat_field('run', 'attempt', root, lambda: checks.append(True))
    assert len(checks) == 3 and result['temperature_K'] == arrays['temperature_K']

    client.transport = httpx.MockTransport(lambda _: httpx.Response(200, json=arrays))
    with pytest.raises(ValueError, match='exceeded its limit'):
        await client.heat_field('run', 'attempt', root, lambda: None)
