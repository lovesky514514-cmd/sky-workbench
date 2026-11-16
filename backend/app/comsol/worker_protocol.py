"""只处理有界控制帧。科研文件在子进程正常退出后单独验收。"""

from pathlib import Path

from app.execution.scientific_transfer import CONTROL_BYTES, READBACK_TYPES, decode_object, load_transfer
from app.session.run_journal import StaleAttempt


EVENT = b'WORKBENCH_EVENT:'
RESULT = b'WORKBENCH_RESULT:'
LOG_BYTES = 64_000


async def _lines(reader):
    pending = bytearray()
    oversized = False
    while chunk := await reader.read(8192):
        for index, part in enumerate(chunk.split(b'\n')):
            if index:
                yield bytes(pending), oversized, True
                pending.clear()
                oversized = False
            remaining = CONTROL_BYTES - len(pending)
            pending.extend(part[:remaining])
            oversized |= len(part) > remaining
    if pending or oversized:
        yield bytes(pending), oversized, False


async def receive_control(reader, log, on_event, *, run_id, attempt_id):
    receipt = None
    remaining = LOG_BYTES
    async for line, oversized, terminated in _lines(reader):
        protocol = line.startswith((EVENT, RESULT))
        if oversized and not protocol:
            line = b'[oversized worker diagnostic omitted]'
        if remaining:
            record = (line + b'\n')[:remaining]
            log.write(record)
            remaining -= len(record)
        if not protocol:
            continue
        if oversized or not terminated:
            raise ValueError('invalid_worker_control_frame')
        if line.startswith(EVENT):
            event = decode_object(line[len(EVENT):])
            if (event.get('run_id', run_id), event.get('attempt_id', attempt_id)) != (run_id, attempt_id):
                raise StaleAttempt('worker event identity mismatch')
            await on_event({**event, 'run_id': run_id, 'attempt_id': attempt_id})
        else:
            if receipt is not None:
                raise ValueError('duplicate_worker_result')
            receipt = decode_object(line[len(RESULT):])
            if set(receipt) != {'success', 'data'} or type(receipt['success']) is not bool:
                raise ValueError('invalid_worker_result_receipt')
    return receipt


def resolve_worker_result(root: Path, receipt, *, exit_code, run_id, attempt_id, producer):
    if receipt is None:
        raise RuntimeError(f'COMSOL worker exited with code {exit_code} without a result receipt')
    if exit_code != 0 and receipt['success']:
        raise RuntimeError(f'COMSOL worker exited with code {exit_code} after publishing a result')
    kind = READBACK_TYPES.get(producer, 'worker-result.v1') if receipt['success'] else 'worker-result.v1'
    value = load_transfer(root, receipt['data'], run_id=run_id, attempt_id=attempt_id, producer=producer, data_type=kind)
    if value.get('success') is not receipt['success']:
        raise ValueError('worker_result_status_mismatch')
    return value
