import io
from pathlib import Path
import subprocess
import sys
import tracemalloc

import pytest

from app.knowledge.semantic_runtime import REQUEST_LIMIT, RESPONSE_LIMIT, SemanticRuntimeClient, _read_frame, _request_frame


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / 'scripts/semantic_runtime_worker.py'
SHIPPED = ROOT / 'frontend/src-tauri/resources/semantic_runtime/semantic_runtime_worker.py'


@pytest.mark.parametrize(('body', 'expected'), [
    ("sys.stdout.buffer.write(b'x' * 1000001); sys.stdout.buffer.flush()", 'semantic_response_limit'),
    ("print('{\"ok\":true', end='', flush=True)", 'semantic_response_truncated'),
    ("sys.exit(9)", 'semantic_worker_exited_without_response'),
    ("print('{\"ok\":true,\"result\":{},\"request_id\":\"foreign\"}', flush=True)", 'semantic_response_identity_mismatch'),
    ("print('partial', end='', flush=True); time.sleep(10)", 'semantic_worker_response_timeout'),
])
def test_broken_exchange_reaps_process(monkeypatch, body, expected):
    client = SemanticRuntimeClient()
    client._timeout = .25 if expected.endswith('_timeout') else 4
    child = subprocess.Popen([sys.executable, '-u', '-c', 'import sys,time; sys.stdin.buffer.readline(); ' + body],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    client._process = child
    monkeypatch.setattr(client, '_start', lambda: child)
    try:
        with pytest.raises(RuntimeError, match=expected):
            client._call({'operation': 'ping'})
        assert client._process is None and child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(5)


def test_host_memory_stays_bounded_for_oversize_line():
    pipe = io.BytesIO(b'x' * (32 * RESPONSE_LIMIT) + b'\n')
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match='semantic_response_limit'):
            _read_frame(pipe)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert pipe.tell() == RESPONSE_LIMIT + 1 and peak < 2 * RESPONSE_LIMIT
    with pytest.raises(ValueError, match='semantic_request_limit'):
        _request_frame({'texts':['中' * 60_000] * 3})


def test_shipped_worker_ping_and_rejects_incomplete_input(tmp_path):
    assert WORKER.read_bytes() == SHIPPED.read_bytes()
    cmd = [sys.executable, '-u', str(SHIPPED), '--embedding', str(tmp_path), '--reranker', str(tmp_path)]
    frame = _request_frame({'operation':'ping', 'request_id':'a'*32})
    valid = subprocess.run(cmd, input=frame, capture_output=True, timeout=10, check=True)
    assert _read_frame(io.BytesIO(valid.stdout))['request_id'] == 'a'*32
    for invalid in (frame[:-1], b'x' * (REQUEST_LIMIT + 1)):
        stopped = subprocess.run(cmd, input=invalid, capture_output=True, timeout=10)
        assert stopped.returncode == 2 and stopped.stdout == b''
