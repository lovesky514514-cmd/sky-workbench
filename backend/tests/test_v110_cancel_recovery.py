"""Public-safe fault injection ONLY: none of these tests invokes COMSOL."""
import asyncio
import json
import sys
import subprocess
import os
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.session.store import SessionStore
from app.models import AgentState
from app.agent.runtime import AgentRuntime
from app.agent.planner import extract_heat_transfer_plan
from app.scientific.schemas import ScientificPlan
from app.validators.base_validator import ValidationReport
from test_v110_verdict import payload_for


def setup_store(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('controlled-injection')['id']
    return store, sid


@pytest.mark.parametrize('phase', ['Build', 'Solve', 'Export'])
@pytest.mark.parametrize('injection', range(20))
def test_cancel_beats_failure_retry_and_late_success(tmp_path, monkeypatch, phase, injection):
    import app.agent.runtime as module
    import app.agent.model_card as cards
    store, sid = setup_store(tmp_path)
    payload, report = payload_for('heat_transfer_2d_stationary', tmp_path / 'evidence')
    payload.update(model_state={}, artifacts={'model': 'controlled-only.mph'}, tool='controlled', duration_ms=0, performance={})
    calls = []

    class Validator:
        def precheck(self, plan): return ValidationReport.from_checks('precheck', [])
        def validate_execution(self, plan, result): return report

    class Tools:
        def process_ids(self, session_id): return {}
        async def cancel(self, session_id, force=False):
            return {'status': 'force_terminated' if force else 'safe_cancel_unavailable', 'cancelled': force}
        async def execute(self, name, arguments, on_event, retries):
            calls.append(arguments)
            await on_event({'phase': phase, 'status': 'running'})
            result = await runtime.cancel_run(sid, force=bool(injection % 2))
            assert result['cancel_requested'] is True
            if not injection % 2:
                assert result['status'] == 'safe_cancel_unavailable'
                assert not result['cancelled']
            if injection % 3 == 0:
                raise RuntimeError('controlled worker failure after cancellation')
            await on_event({'phase': 'Export', 'status': 'success'})
            return {**deepcopy(payload), 'run_id': arguments['run_id'], 'attempt_id': arguments['attempt_id']}

    class Monitor:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass

    monkeypatch.setattr(module.validator_registry, 'get', lambda _: Validator())
    monkeypatch.setattr(module, 'ResourceMonitor', Monitor)
    monkeypatch.setattr(module, 'hardware_profile', lambda *a: {})
    monkeypatch.setattr(module, 'assess_risk', lambda *a, **k: {})
    monkeypatch.setattr(module, 'settings_store', SimpleNamespace(load=lambda: {'execution': {'max_repairs': 3}}))
    monkeypatch.setattr(module.java_bridge_process_manager, 'status', lambda: {'running': False})
    monkeypatch.setattr(cards, 'WORKSPACE_ROOT', tmp_path)
    store.set_state(sid, AgentState.PRECHECK)
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'))
    runtime = AgentRuntime(sessions=store, tools=Tools())
    asyncio.run(runtime._execute_run(sid, plan))
    assert len(calls) == 1
    view = store.get(sid)
    assert view['state'] == 'CANCELED'
    assert not any(e['type'] == 'run.completed' for e in view['events'])
    run = store.get_run(calls[0]['run_id'])
    assert run['cancel_requested'] and run['outcome'] == 'Canceled'
    assert not store.attempt_is_current(run['run_id'], calls[0]['attempt_id'])
    assert SessionStore(store.path).get_run(run['run_id'])['cancel_requested']
    assert view['messages'][-1]['run_lifecycle']['outcome'] == 'Canceled'
    assert view['model']['run_lifecycle']['outcome'] == 'Canceled'
    event = [e for e in view['events'] if e['type'] == 'run.canceled'][-1]
    assert event['payload']['run_lifecycle']['outcome'] == 'Canceled'
    timeline = [e for e in view['events'] if e['type'] == 'timeline.completed' and e['payload'].get('run_lifecycle')][-1]
    assert timeline['payload']['run_lifecycle']['outcome'] == 'Canceled'
    card = tmp_path / 'reports' / run['run_id'] / 'model_card.md'
    assert 'Canceled' in card.read_text(encoding='utf-8')


@pytest.mark.parametrize('phase', ['BUILDING', 'BUILD_VALIDATION', 'SOLVING', 'SOLUTION_VALIDATION', 'EXPORTING'])
@pytest.mark.parametrize('fault', ['runtime_crash', 'worker_crash', 'bridge_crash', 'desktop_exit'])
def test_recovery_matrix_never_certifies_files(tmp_path, phase, fault):
    store, sid = setup_store(tmp_path)
    artifact = tmp_path / 'original.mph'
    artifact.write_bytes(b'not a scientific result')
    # A real controlled runtime child writes the journal and dies without cleanup.
    # Worker/bridge variants kill a harmless owned child before crashing the harness.
    code = r'''
import asyncio, os, sys
from pathlib import Path
from app.session.store import SessionStore
from app.models import AgentState
from app.comsol.owned_process import OwnedProcess
store = SessionStore(Path(sys.argv[1])); store.initialize()
sid, phase, fault, artifact = sys.argv[2:]
store.begin_run(sid, 'run-1', {'workflow_id': 'controlled'})
attempt = store.begin_attempt('run-1')
async def inject():
    child = await OwnedProcess.spawn([sys.executable, '-c', 'import time; time.sleep(60)'])
    store.update_run('run-1', attempt, phase=phase, worker=child.identity, artifacts={'model': artifact})
    store.set_state(sid, AgentState(phase))
    if fault in ('worker_crash', 'bridge_crash'):
        await child.terminate()
    # runtime_crash and desktop_exit simulate abrupt sidecar loss, NOT real desktop UI.
    os._exit(27)
asyncio.run(inject())
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(store.path), sid, phase, fault, str(artifact)], timeout=10, capture_output=True)
    assert result.returncode == 27, result.stderr.decode(errors='replace')
    attempt = store.get_run('run-1')['attempt_id']
    start = time.perf_counter()
    # Simulated app restart boundary, not a real desktop/COMSOL crash.
    restored = SessionStore(store.path)
    restored.reconcile_active_runs()
    assert time.perf_counter() - start < 5
    assert restored.get(sid)['state'] == 'INTERRUPTED'
    assert restored.get_run('run-1')['phase'] == 'INTERRUPTED'
    assert not restored.attempt_is_current('run-1', attempt)
    assert artifact.read_bytes() == b'not a scientific result'
    assert not any(e['type'] == 'run.completed' for e in restored.get(sid)['events'])


def test_owner_and_cancellation_are_transactional(tmp_path):
    store, sid = setup_store(tmp_path)
    other = store.create('other')['id']
    store.begin_run(sid, 'one', {})
    attempt = store.begin_attempt('one')
    with pytest.raises(RuntimeError): store.begin_run(other, 'two', {})
    store.request_cancel(sid, force=True)
    with pytest.raises(RuntimeError): store.begin_attempt('one')
    with pytest.raises(RuntimeError): store.finish_run('one', attempt, 'Completed')
    assert not store.attempt_is_current('one', attempt)


def test_retryable_fatal_and_stale_are_distinct(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'one', {})
    first = store.begin_attempt('one')
    store.end_attempt('one', first, 'RetryableFailure')
    second = store.begin_attempt('one')
    assert second != first
    assert not store.attempt_is_current('one', first)
    with pytest.raises(RuntimeError): store.finish_run('one', first, 'Completed')
    store.finish_run('one', second, 'FatalFailure')
    assert store.get_run('one')['outcome'] == 'FatalFailure'
    assert store.get_run('one')['attempts'][0]['outcome'] == 'RetryableFailure'


def test_owned_child_tree_and_unrelated_process(tmp_path):
    from app.comsol.owned_process import OwnedProcess
    async def exercise():
        other = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)')
        owned = None
        try:
            owned = await OwnedProcess.spawn([sys.executable, '-c',
                'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print(p.pid,flush=True); time.sleep(30)'])
            child = int(await asyncio.wait_for(owned.process.stdout.readline(), 5))
            identity = owned.identity
            assert identity['pid'] == owned.process.pid and identity['created']
            result = await owned.terminate()
            assert result['reaped'] is True
            assert other.returncode is None
            assert not OwnedProcess.identity_alive(OwnedProcess.identify(child))
        finally:
            if owned: await owned.terminate()
            other.terminate()
            await other.wait()
    asyncio.run(exercise())


def test_soft_cancel_does_not_terminate_owned_worker(tmp_path):
    from app.agent.tool_registry import ToolRegistry
    from app.comsol.owned_process import OwnedProcess
    async def exercise():
        owned = await OwnedProcess.spawn([sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(30)'])
        registry = ToolRegistry()
        registry._active_processes['sid'] = owned.process
        registry._owned['sid'] = owned
        try:
            await asyncio.wait_for(owned.process.stdout.readline(), 5)
            result = await registry.cancel('sid')
            assert result['status'] == 'safe_cancel_unavailable' and not result['cancelled']
            assert OwnedProcess.identity_alive(owned.identity) is True
            result = await registry.cancel('sid', force=True)
            assert result['cancelled'] and result['cleanup']['reaped']
        finally: await owned.terminate()
    asyncio.run(exercise())


def test_unknown_ownership_never_kills(tmp_path):
    from app.agent.tool_registry import ToolRegistry
    async def exercise():
        other = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)')
        try:
            registry = ToolRegistry()
            registry._active_processes['sid'] = other  # No ownership handle: PID is insufficient.
            result = await registry.cancel('sid', force=True)
            assert result['status'] == 'ownership_unverified'
            assert other.returncode is None
        finally:
            other.terminate(); await other.wait()
    asyncio.run(exercise())


def test_live_orphan_is_not_killed_or_recertified(tmp_path):
    from app.comsol.owned_process import OwnedProcess
    store, sid = setup_store(tmp_path)
    async def exercise():
        other = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)')
        try:
            store.begin_run(sid, 'orphan', {})
            attempt = store.begin_attempt('orphan')
            store.update_run('orphan', attempt, worker=OwnedProcess.identify(other.pid))
            result = store.reconcile_active_runs()
            assert result[0]['orphaned']
            assert other.returncode is None
            with pytest.raises(RuntimeError): store.begin_run(sid, 'blocked', {})
            assert not store.attempt_is_current('orphan', attempt)
        finally:
            other.terminate(); await other.wait()
    asyncio.run(exercise())


def test_shutdown_reclaims_owned_worker_and_marks_interrupted(tmp_path):
    from app.comsol.owned_process import OwnedProcess
    from app.agent.tool_registry import ToolRegistry
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'shutdown', {})
    attempt = store.begin_attempt('shutdown')
    async def exercise():
        owned = await OwnedProcess.spawn([sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(30)'])
        await asyncio.wait_for(owned.process.stdout.readline(), 5)
        registry = ToolRegistry()
        registry._active_processes[sid] = owned.process
        registry._owned[sid] = owned
        runtime = AgentRuntime(sessions=store, tools=registry)
        store.update_run('shutdown', attempt, worker=owned.identity, phase='SOLVING')
        async def controlled_run():
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                store.update_run('shutdown', attempt, cleanup=await owned.terminate())
                store.finish_run('shutdown', attempt, 'Interrupted')
        runtime._tasks[sid] = asyncio.create_task(controlled_run())
        await asyncio.sleep(0)
        await runtime.shutdown()
        assert OwnedProcess.identity_alive(owned.identity) is False
        assert store.get(sid)['state'] == 'INTERRUPTED'
        assert any(e['type'] == 'run.shutdown_cleanup' for e in store.get(sid)['events'])
    asyncio.run(exercise())


def test_recovery_card_preserves_original_artifacts(tmp_path, monkeypatch):
    import app.agent.model_card as cards
    monkeypatch.setattr(cards, 'WORKSPACE_ROOT', tmp_path)
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'interrupted', {})
    store.begin_attempt('interrupted')
    old = tmp_path / 'reports' / 'interrupted' / 'model_card.md'
    old.parent.mkdir(parents=True); old.write_text('prior artifact', encoding='utf-8')
    result = asyncio.run(AgentRuntime(sessions=store).reconcile())
    assert result[0]['state'] == 'INTERRUPTED'
    assert old.read_text(encoding='utf-8') == 'prior artifact'
    card = old.with_name('recovery_card.md').read_text(encoding='utf-8')
    assert 'Interrupted' in card and 'execution_interrupted' in card
    assert asyncio.run(AgentRuntime(sessions=store).reconcile()) == []


def test_stale_event_and_ended_attempt_cannot_certify(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'stale', {})
    first = store.begin_attempt('stale')
    store.end_attempt('stale', first, 'RetryableFailure')
    with pytest.raises(RuntimeError): store.finish_run('stale', first, 'Completed')
    second = store.begin_attempt('stale')
    runtime = AgentRuntime(sessions=store)
    before = len(store.get(sid)['events'])
    with pytest.raises(RuntimeError):
        asyncio.run(runtime._handle_attempt_event(sid, 'stale', second, {'phase': 'Export', 'attempt_id': first}))
    assert len(store.get(sid)['events']) == before


def test_attempt_workspaces_never_overwrite(tmp_path, monkeypatch):
    import app.agent.tool_registry as module
    from pathlib import Path
    monkeypatch.setattr(module, 'WORKSPACE_ROOT', tmp_path)
    requests = []

    async def spawn(command, **kwargs):
        request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        requests.append(request)
        model = Path(request['workspace']) / 'models' / 'same-name.mph'
        model.parent.mkdir(parents=True); model.write_bytes(request['attempt_id'].encode())
        reader = asyncio.StreamReader()
        reader.feed_data(('WORKBENCH_RESULT:' + json.dumps({'success': True, 'run_id': request['run_id'], 'artifacts': {'model': str(model)}}) + '\n').encode())
        reader.feed_eof()
        async def wait(): return 0
        async def terminate(): return {'reaped': True}
        return SimpleNamespace(process=SimpleNamespace(pid=0, returncode=0, stdout=reader, wait=wait), identity={}, terminate=terminate)

    monkeypatch.setattr(module.OwnedProcess, 'spawn', spawn)
    registry = module.ToolRegistry()
    async def executor(arguments, on_event):
        return await module.run_comsol_worker(arguments, on_event, 'controlled.py')
    registry.register(module.RegisteredTool('controlled', 'fixture only', 'test', executor))
    async def event(_): pass
    async def exercise():
        results = []
        for attempt in ('first', 'second'):
            results.append(await registry.execute('controlled', {'run_id': 'same-run', 'attempt_id': attempt}, event, retries=0))
        assert requests[0]['workspace'] != requests[1]['workspace']
        assert Path(results[0]['artifacts']['model']).read_bytes() == b'first'
        assert Path(results[1]['artifacts']['model']).read_bytes() == b'second'
        with pytest.raises(RuntimeError):
            await registry.execute('controlled', {'run_id': 'same-run', 'attempt_id': 'first'}, event, retries=0)
        assert len(requests) == 2
    asyncio.run(exercise())


def test_unproven_cleanup_cannot_complete(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'cleanup', {})
    attempt = store.begin_attempt('cleanup')
    store.update_run('cleanup', attempt, worker={'pid': 123, 'created': 456}, cleanup={'reaped': False})
    with pytest.raises(RuntimeError): store.finish_run('cleanup', attempt, 'Completed')
    assert store.get_run('cleanup')['outcome'] is None
    assert store.finish_run('cleanup', attempt, 'FatalFailure')['orphaned']


def test_bridge_mutation_owner_conflict(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'run', {})
    with pytest.raises(RuntimeError): store.acquire_bridge_owner('bridge')
    store.finish_run('run', None, 'FatalFailure')
    store.acquire_bridge_owner('bridge')
    with pytest.raises(RuntimeError): store.begin_run(sid, 'second', {})
    store.release_bridge_owner('bridge')
    store.begin_run(sid, 'second', {})


def test_bridge_stop_during_controlled_start_cannot_resurrect(tmp_path, monkeypatch):
    import threading
    import app.comsol.bridge_process as bridge
    store, sid = setup_store(tmp_path)
    monkeypatch.setattr(bridge, 'session_store', store)
    manager = bridge.JavaBridgeProcessManager()
    monkeypatch.setattr(manager, '_validate_model_path', lambda value: tmp_path / 'fixture-only.mph')
    entered = threading.Event()
    def controlled_start(path):
        entered.set()
        assert manager._stop_requested.wait(3)
        raise RuntimeError('controlled startup canceled before process creation')
    monkeypatch.setattr(manager, '_start_sync', controlled_start)
    async def exercise():
        await manager.start('fixture-only')
        assert await asyncio.to_thread(entered.wait, 2)
        result = await asyncio.wait_for(manager.stop(), 3)
        assert result['state'] == 'stopped' and not result['running']
        assert manager._task.done()
        store.begin_run(sid, 'after-stop', {})
    asyncio.run(exercise())


def test_shutdown_before_task_enters_runtime_is_reconciled(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'pending', {})
    runtime = AgentRuntime(sessions=store)
    async def exercise():
        runtime._tasks[sid] = asyncio.create_task(asyncio.sleep(30))
        await runtime.shutdown()
        assert store.get(sid)['state'] == 'INTERRUPTED'
        assert store.get_run('pending')['attempts'] == []
    asyncio.run(exercise())


def test_retry_callback_keeps_original_attempt_identity(tmp_path, monkeypatch):
    import app.agent.runtime as module
    import app.agent.model_card as cards
    store, sid = setup_store(tmp_path)
    payload, report = payload_for('heat_transfer_2d_stationary')
    calls, callbacks = [], []
    class Validator:
        def precheck(self, plan): return ValidationReport.from_checks('precheck', [])
        def validate_execution(self, plan, result): return report
    class Tools:
        def process_ids(self, sid): return {}
        async def execute(self, name, args, callback, retries):
            calls.append(args); callbacks.append(callback)
            if len(calls) == 1: raise RuntimeError('controlled retryable failure')
            # A retained callback without self-reported IDs is still bound to attempt 1.
            with pytest.raises(RuntimeError):
                await callbacks[0]({'phase': 'Solve', 'status': 'success'})
            await callback({'phase': 'Solve'})
            await callback({'phase': 'Solution Validation'})
            return {**deepcopy(payload), 'run_id': args['run_id'], 'attempt_id': args['attempt_id'],
                    'tool': 'controlled', 'duration_ms': 0, 'performance': {}}
    class Monitor:
        def __init__(self, *a, **k): pass
        def start(self): pass
        async def stop(self): pass
    monkeypatch.setattr(module.validator_registry, 'get', lambda _: Validator())
    monkeypatch.setattr(module, 'ResourceMonitor', Monitor)
    monkeypatch.setattr(module, 'hardware_profile', lambda *a: {})
    monkeypatch.setattr(module, 'assess_risk', lambda *a, **k: {})
    monkeypatch.setattr(module, 'settings_store', SimpleNamespace(load=lambda: {'execution': {'max_repairs': 1}}))
    monkeypatch.setattr(module.java_bridge_process_manager, 'status', lambda: {'running': False})
    monkeypatch.setattr(cards, 'WORKSPACE_ROOT', tmp_path)
    store.set_state(sid, AgentState.PRECHECK)
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'))
    asyncio.run(AgentRuntime(sessions=store, tools=Tools())._execute_run(sid, plan))
    assert len(calls) == 2 and calls[0]['attempt_id'] != calls[1]['attempt_id']
    assert store.get(sid)['state'] == 'COMPLETED'
    assert [a['outcome'] for a in store.get_run(calls[0]['run_id'])['attempts']] == ['RetryableFailure', 'Completed']


def test_pending_cancel_survives_restart_before_worker_returns(tmp_path):
    store, sid = setup_store(tmp_path)
    store.begin_run(sid, 'pending-cancel', {})
    attempt = store.begin_attempt('pending-cancel')
    store.request_cancel(sid, force=True)
    restarted = SessionStore(store.path)
    assert restarted.get_run('pending-cancel')['cancel_requested']
    result = restarted.reconcile_active_runs()
    assert result[0]['outcome'] == 'Canceled'
    assert restarted.get(sid)['state'] == 'CANCELED'
    with pytest.raises(RuntimeError): restarted.begin_attempt('pending-cancel')
    with pytest.raises(RuntimeError): restarted.finish_run('pending-cancel', attempt, 'Completed')


def test_pid_reuse_is_not_the_original_owned_process(monkeypatch):
    from app.comsol.owned_process import OwnedProcess
    monkeypatch.setattr(OwnedProcess, 'identify', staticmethod(lambda pid: {'pid': pid, 'created': 200, 'alive': True}))
    assert OwnedProcess.identity_alive({'pid': 123, 'created': 100}) is False
    assert OwnedProcess.identity_alive({'pid': 123, 'created': None}) is None


def test_two_store_instances_have_one_atomic_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store, sid = setup_store(tmp_path)
    other_sid = store.create('other-session')['id']
    barrier = Barrier(2)
    def acquire(session_id):
        peer = SessionStore(store.path)
        barrier.wait(timeout=3)
        try:
            peer.begin_run(session_id, session_id, {})
            return 'owned'
        except RuntimeError:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(acquire, (sid, other_sid))) == ['blocked', 'owned']


def test_precheck_rejection_does_not_publish_execution_failure(tmp_path, monkeypatch):
    import app.agent.runtime as module
    store, sid = setup_store(tmp_path)
    store.set_state(sid, AgentState.PRECHECK)
    class Validator:
        def precheck(self, plan): return ValidationReport(scope='precheck', passed=False, command_success=False,
                                                        model_state_verified=False, scientific_validation_success=False, checks=[])
    monkeypatch.setattr(module.validator_registry, 'get', lambda _: Validator())
    plan = ScientificPlan.model_validate(extract_heat_transfer_plan('2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'))
    asyncio.run(AgentRuntime(sessions=store)._execute_run(sid, plan))
    view = store.get(sid)
    assert view['state'] == 'VALIDATION_FAILED'
    assert not any(e['type'] == 'run.failed' or (e['type'] == 'state.changed' and e['payload']['state'] == 'FAILED') for e in view['events'])
    assert store.latest_run(sid)['attempts'] == []
    assert 'execution_verdict' not in view['messages'][-1]
