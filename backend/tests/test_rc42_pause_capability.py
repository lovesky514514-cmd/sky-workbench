"""Pause availability requires a current owned run, Bridge and attachment."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent.runtime import AgentRuntime
from app.execution.graph import ExecutionGraph
from app.execution.live_interrupt import LiveInterruptAdapter
from app.session.store import SessionStore


def test_pause_capability_fails_closed_without_owner_or_attachment(tmp_path: Path, monkeypatch):
    store = SessionStore(tmp_path / 'pause.sqlite')
    store.initialize()
    sid = store.create('pause-project')['id']
    store.begin_run(sid, 'run-pause', {'workflow_id': 'tds_2d_stationary_diffusion'})
    attempt = store.begin_attempt('run-pause')
    graph = ExecutionGraph.small_supported('run-pause', attempt, 'tds_2d_stationary_diffusion')
    live = LiveInterruptAdapter(store, object())
    live.register(sid, graph)
    live.start('run-pause', 'build')
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.sessions = store
    runtime.live_interrupt = live
    runtime._tasks = {}
    from app.agent import runtime as runtime_module

    async def scenario():
        waiting = asyncio.Event()
        task = asyncio.create_task(waiting.wait())
        runtime._tasks[sid] = task
        try:
            monkeypatch.setattr(runtime_module.java_bridge_process_manager, 'status',
                                lambda: {'running': True, 'owner_id': 'different-run'})
            assert await runtime.pause_capability(sid) == 'unavailable'
            monkeypatch.setattr(runtime_module.java_bridge_process_manager, 'status',
                                lambda: {'running': True, 'owner_id': 'run-pause'})
            async def unavailable():
                return {'real_comsol': False, 'model_connected': False}
            monkeypatch.setattr(runtime_module.java_bridge_client, 'health', unavailable)
            assert await runtime.pause_capability(sid) == 'unavailable'
            async def attached():
                return {'real_comsol': True, 'model_connected': True}
            monkeypatch.setattr(runtime_module.java_bridge_client, 'health', attached)
            assert await runtime.pause_capability(sid) == 'available'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
