"""Independent heartbeat and a fence that precedes native COMSOL startup."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

LEASE_SECONDS = 30.0
HEARTBEAT_SECONDS = 2.0


def reserve_start(directory: Path) -> bool:
    # Worker 与宿主竞争同一文件。只有赢得启动权的 worker 能进入求解器。
    try:
        with (directory / 'worker-start.claim').open('xb'):
            pass
        return True
    except FileExistsError:
        return False


@contextmanager
def heartbeat(directory: Path):
    stopped = Event()
    target = directory / 'worker-lease'
    temporary = directory / 'worker-lease.tmp'

    def renew():
        tick = 0
        while not stopped.is_set():
            tick += 1
            try:
                temporary.write_bytes(tick.to_bytes(8, 'big'))
                temporary.replace(target)
            except OSError:
                # Windows 读取端持有文件时，下一次脉冲可继续更新。
                pass
            stopped.wait(HEARTBEAT_SECONDS)

    thread = Thread(target=renew, name='comsol-worker-heartbeat', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()


def read_pulse(directory: Path) -> int:
    try:
        with (directory / 'worker-lease').open('rb') as stream:
            value = stream.read(9)
    except OSError:
        return 0
    return int.from_bytes(value, 'big') if len(value) == 8 else 0


async def watch_worker(execution, directory, owned, owns_attempt, record, *, lease_seconds=LEASE_SECONDS,
                       poll_seconds=1.0):
    clock = asyncio.get_running_loop().time
    deadline = clock() + lease_seconds
    last_pulse = 0
    reported = False
    while True:
        done, _ = await asyncio.wait({execution}, timeout=poll_seconds)
        if done:
            return execution.result()
        pulse = read_pulse(directory)
        if pulse > last_pulse:
            last_pulse = pulse
            deadline = clock() + lease_seconds
            reported = False
        if reported or clock() < deadline:
            continue
        try:
            # 先核对任务与进程所有权，再阻止 worker 进入可能仍活动的求解器。
            safe = owns_attempt() and owned.tree_is_owned() and reserve_start(directory)
        except (OSError, ValueError):
            safe = False
        if safe:
            cleanup = await owned.terminate()
            record('worker_lease_reaped' if cleanup['reaped'] else 'worker_lease_unresolved')
            return await execution
        record('worker_lease_unresolved')
        reported = True
