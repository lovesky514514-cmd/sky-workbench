"""Synthetic worker processes only; never import or start COMSOL."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.agent.tool_registry import ToolRegistry
from app.comsol import worker_lease
from app.comsol.owned_process import OwnedProcess
from app.session.store import SessionStore

BACKEND = Path(__file__).resolve().parents[1]


def worker_environment():
    return {**os.environ, 'PYTHONPATH': str(BACKEND), 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}


async def drain(owned):
    payload, _ = await owned.process.communicate()
    return owned.process.returncode, payload


async def marker(path):
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


def test_exception_exits_even_when_a_worker_thread_would_block_teardown(tmp_path):
    code = """
import sys, threading
import desktop_runtime
threading.Thread(target=threading.Event().wait).start()
def broken(*args, **kwargs):
    raise RuntimeError('synthetic worker failure')
desktop_runtime._run_worker = broken
sys.argv = ['desktop_runtime', '--worker', 'heat_worker', '--request', 'request.json']
sys.exit(desktop_runtime.main())
"""
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=worker_environment(),
                            capture_output=True, timeout=5)
    assert result.returncode == 1
    assert b'worker_unhandled_error' in result.stderr
    assert b'synthetic worker failure' not in result.stderr


def test_expired_worker_with_owned_tree_and_attempt_is_reaped(tmp_path):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('synthetic-lease')['id']
    run = 'synthetic-run'
    store.begin_run(sid, run, {'workflow_id': 'controlled'})
    attempt = store.begin_attempt(run)
    messages = []
    code = """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path('child-pid').write_text(str(child.pid))
time.sleep(30)
"""

    async def exercise():
        owned = await OwnedProcess.spawn([sys.executable, '-c', code], cwd=tmp_path, env=worker_environment())
        execution = asyncio.create_task(drain(owned))
        try:
            await marker(tmp_path / 'child-pid')
            child = OwnedProcess.identify(int((tmp_path / 'child-pid').read_text()))
            assert child.get('created')
            store.update_run(run, attempt, worker=owned.identity)
            assert not store.owns_worker_attempt(sid, run, attempt, {**owned.identity, 'created': 1})
            result, _ = await asyncio.wait_for(worker_lease.watch_worker(
                execution, tmp_path, owned,
                lambda: store.owns_worker_attempt(sid, run, attempt, owned.identity), messages.append,
                lease_seconds=0.05, poll_seconds=0.015,
            ), timeout=5)
            assert result != 0 and messages == ['worker_lease_reaped']
            cleanup = await owned.terminate()
            assert cleanup['reaped'] and not OwnedProcess.identity_alive(child)
            store.update_run(run, attempt, cleanup=cleanup)
            store.finish_run(run, attempt, 'FatalFailure')
            with store._connect() as db:
                assert db.execute('SELECT count(*) FROM comsol_owner').fetchone()[0] == 0
                assert db.execute('SELECT count(*) FROM comsol_owner_identity').fetchone()[0] == 0
        finally:
            await owned.terminate()

    asyncio.run(exercise())


@pytest.mark.parametrize('case', ['healthy_silent', 'solver_claimed', 'wrong_attempt'])
def test_silent_worker_survives_if_live_or_ownership_and_activity_are_uncertain(tmp_path, case):
    code = """
from pathlib import Path
from contextlib import nullcontext
import sys, time
from app.comsol import worker_lease
case = sys.argv[1]
worker_lease.HEARTBEAT_SECONDS = 0.015
if case != 'wrong_attempt':
    assert worker_lease.reserve_start(Path.cwd())
with worker_lease.heartbeat(Path.cwd()) if case == 'healthy_silent' else nullcontext():
    Path('started').touch()
    while not Path('finish').exists():
        time.sleep(0.02)
print('normal worker output')
"""
    statuses = []

    async def exercise():
        owned = await OwnedProcess.spawn([sys.executable, '-c', code, case], cwd=tmp_path, env=worker_environment())
        registry = ToolRegistry()
        registry._active_processes['session'] = owned.process
        registry._owned['session'] = owned
        execution = asyncio.create_task(drain(owned))
        try:
            await marker(tmp_path / 'started')
            if case == 'healthy_silent':
                await marker(tmp_path / 'worker-lease')
            expected = (
                b'{"cancelled":false,"status":"safe_cancel_unavailable",'
                b'"detail":"This isolated COMSOL API workflow exposes no documented in-process solver cancel operation; the process was not killed"}'
            )
            import json
            assert json.dumps(await registry.cancel('session', force=False), separators=(',', ':')).encode() == expected
            watched = asyncio.create_task(worker_lease.watch_worker(
                execution, tmp_path, owned, lambda: case != 'wrong_attempt', statuses.append,
                lease_seconds=0.12, poll_seconds=0.015,
            ))
            await asyncio.sleep(0.42)
            assert not watched.done() and OwnedProcess.identity_alive(owned.identity) is True
            assert statuses == ([] if case == 'healthy_silent' else ['worker_lease_unresolved'])
            assert (tmp_path / 'worker-start.claim').exists() is (case != 'wrong_attempt')
            (tmp_path / 'finish').touch()
            exit_code, output = await asyncio.wait_for(watched, 5)
            assert exit_code == 0 and output.strip() == b'normal worker output'
        finally:
            await owned.terminate()

    asyncio.run(exercise())
