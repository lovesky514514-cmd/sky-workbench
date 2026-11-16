"""RUN 网格检查点 / Mesh checkpoints from the RUN's COMSOL JVM."""

from hashlib import sha256
import json
from pathlib import Path
import re

from app.execution.files import bounded_read

CHECKPOINT_EVENT = 'run.model_checkpoint'
MAX_CHECKPOINTS = 8
MAX_TOPOLOGY_BYTES = 32_000_000


def checkpoint_data(workspace: Path, index: int, size: int, digest: str) -> bytes:
    if (type(index) is not int or not 1 <= index <= MAX_CHECKPOINTS
            or type(size) is not int or not 1 <= size <= MAX_TOPOLOGY_BYTES):
        raise ValueError('invalid_model_checkpoint_descriptor')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('invalid_model_checkpoint_hash')
    raw = bounded_read(workspace / 'model-checkpoints' / str(index), 'topology.bin', size, single_link=True)
    if len(raw) != size or sha256(raw).hexdigest() != digest:
        raise ValueError('model_checkpoint_integrity')
    return raw


def publish_checkpoint(model, version: str, workspace: Path, classes, index=1):
    if not classes:
        return
    from jpype import JClass, addClassPath

    try:
        addClassPath(str(classes))
        publisher = JClass('workbench.bridge.ComsolVisualization')
        directory = workspace / 'model-checkpoints' / str(index)
        directory.mkdir(parents=True, exist_ok=False)
        java_dir = JClass('java.nio.file.Paths').get(str(directory))
        receipt = json.loads('{' + str(publisher.publish(model.java, version, java_dir)) + '}')
        event = {
            'event_type': CHECKPOINT_EVENT,
            'index': index,
            'bytes': receipt['bytes'],
            'sha256': receipt['sha256'],
        }
        print('WORKBENCH_EVENT:' + json.dumps(event), flush=True)
    except Exception as exc:
        # 画布采集独立于求解和审核 / View capture is separate from solve and review.
        print(f'COMSOL topology checkpoint unavailable: {type(exc).__name__}', flush=True)
