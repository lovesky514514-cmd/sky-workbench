"""有界科研文件传输 / Bounded transfer of run-scoped scientific files."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.execution.files import bounded_read, reject_reparse


CONTROL_BYTES = 16_384
RESULT_FILE = 'result.json'
LIMITS = {'worker-result.v1': 4_000_000, 'cooling-readback.v1': 1_000_000,
          'heat-readback.v1': 64_000, 'bridge-heat-field.v1': 16_000_000}
READBACK_TYPES = {'cooling_readback_worker': 'cooling-readback.v1', 'heat_readback_worker': 'heat-readback.v1'}
COOLING_COLUMNS = ('time_s', 'average_K', 'minimum_K', 'maximum_K', 'outward_heat_W', 'convection_W', 'radiation_W')
HEAT_POINT_COLUMNS = ('x_m', 'y_m', 'temperature_K', 'qx_W_m2', 'qy_W_m2')
BRIDGE_COLUMNS = ('x', 'y', 'temperature_K')
SafeIdentity = Annotated[str, Field(pattern=r'^[A-Za-z0-9-]{1,100}$')]
Extent = Annotated[int, Field(gt=0, le=200_000)]


class DataDescriptor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Literal[1]
    run_id: SafeIdentity
    attempt_id: SafeIdentity
    producer: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
    data_type: Literal['worker-result.v1', 'cooling-readback.v1', 'heat-readback.v1', 'bridge-heat-field.v1']
    relative_path: Literal['result.json']
    media_type: Literal['application/json']
    byte_count: Annotated[int, Field(gt=0, le=16_000_000)]
    sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    dimensions: Annotated[list[Extent], Field(max_length=2)]
    sample_count: Annotated[int, Field(ge=0, le=200_000)]


def _object(pairs):
    # 禁止重复字段覆盖身份 / Duplicate keys cannot overwrite identity fields.
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('duplicate_json_key')
    return value


def _nonfinite(_):
    raise ValueError('nonfinite_scientific_number')


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        _nonfinite(text)
    return value


def decode_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_nonfinite, parse_float=_float)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid_scientific_json') from error
    if not isinstance(value, dict):
        raise ValueError('scientific_json_object_required')
    return value


def _numbers(values):
    # 布尔值不是采样点 / Booleans are invalid samples.
    return all(type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max for value in values)


def _dimensions(value, kind):
    if kind == 'worker-result.v1':
        if type(value.get('success')) is not bool:
            raise ValueError('worker_result_status_required')
        return []
    if kind in READBACK_TYPES.values() and (value.get('success') is not True or value.get('saved_model_unchanged') is not True
            or type(value.get('solver_calls')) is not int or value['solver_calls'] != 0
            or not isinstance(value.get('model_readback'), dict)):
        raise ValueError('invalid_read_only_result')
    if kind == 'heat-readback.v1':
        field = value.get('field_readback')
        rows = field.get('samples') if isinstance(field, dict) else None
        if (not isinstance(rows, list) or len(rows) != 9 or any(not isinstance(row, dict)
                or set(row) != set(HEAT_POINT_COLUMNS) or not _numbers(row.values()) for row in rows)):
            raise ValueError('invalid_heat_point_shape_or_values')
        return [len(rows), len(HEAT_POINT_COLUMNS)]
    if kind == 'cooling-readback.v1':
        field, columns, minimum, maximum = value.get('field_readback'), COOLING_COLUMNS, 2, 5_000
    else:
        field, columns, minimum, maximum = value, BRIDGE_COLUMNS, 1, 200_000
    if not isinstance(field, dict) or set(field) != set(columns):
        raise ValueError('invalid_scientific_columns')
    samples = len(field[columns[0]]) if isinstance(field[columns[0]], list) else 0
    if not minimum <= samples <= maximum or any(not isinstance(field[key], list)
            or len(field[key]) != samples or not _numbers(field[key]) for key in columns):
        raise ValueError('invalid_scientific_array_shape_or_values')
    if kind == 'cooling-readback.v1' and any(a >= b for a, b in zip(field['time_s'], field['time_s'][1:])):
        raise ValueError('invalid_cooling_sample_times')
    return [samples, len(columns)]


def load_transfer(root: Path, descriptor, *, run_id: str, attempt_id: str, producer: str,
                  data_type: str) -> dict:
    item = DataDescriptor.model_validate(descriptor)
    if (item.run_id, item.attempt_id, item.producer, item.data_type) != (run_id, attempt_id, producer, data_type):
        raise PermissionError('scientific_transfer_identity_mismatch')
    limit = LIMITS[data_type]
    if item.byte_count > limit:
        raise ValueError('scientific_transfer_size_limit')
    raw = bounded_read(root, item.relative_path, item.byte_count, single_link=True)
    if len(raw) != item.byte_count or sha256(raw).hexdigest() != item.sha256:
        raise ValueError('scientific_transfer_integrity_mismatch')
    value = decode_object(raw)
    dimensions = _dimensions(value, data_type)
    if item.dimensions != dimensions or item.sample_count != (dimensions[0] if dimensions else 0):
        raise ValueError('scientific_transfer_dimensions_mismatch')
    if data_type != 'bridge-heat-field.v1' and (value.get('run_id'), value.get('attempt_id')) != (run_id, attempt_id):
        raise PermissionError('scientific_payload_identity_mismatch')
    return value


def publish_transfer(root: Path, payload: dict, *, run_id: str, attempt_id: str, producer: str,
                     data_type: str) -> DataDescriptor:
    reject_reparse(root)
    dimensions = _dimensions(payload, data_type)
    limit = LIMITS[data_type]
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix='.result-', suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            size, digest = 0, sha256()
            for piece in encoder.iterencode(payload):
                encoded = piece.encode('utf-8')
                size += len(encoded)
                if size > limit:
                    raise ValueError('scientific_transfer_size_limit')
                stream.write(encoded)
                digest.update(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        item = DataDescriptor(schema_version=1, run_id=run_id, attempt_id=attempt_id, producer=producer,
            data_type=data_type, relative_path=RESULT_FILE, media_type='application/json', byte_count=size,
            sha256=digest.hexdigest(), dimensions=dimensions, sample_count=dimensions[0] if dimensions else 0)
        if sha256(bounded_read(root, temp.name, size, single_link=True)).hexdigest() != item.sha256:
            raise ValueError('scientific_transfer_staging_changed')
        target = root / RESULT_FILE
        if os.name == 'nt':
            temp.rename(target)  # Windows rename 在目标已存在时拒绝，且使用同一目录。
        else:
            os.link(temp, target)  # POSIX rename 会覆盖目标，使用排他原子链接发布。
        return item
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


_worker_scope = ContextVar('scientific_transfer_worker_scope', default=None)


@contextmanager
def worker_scope(request: Path, producer: str) -> Iterator[None]:
    payload = decode_object(bounded_read(request.parent, request.name, 1_000_000))
    token = _worker_scope.set((request.parent / 'data', payload['run_id'], payload['attempt_id'], producer))
    try:
        yield
    finally:
        _worker_scope.reset(token)


def emit_result(payload: dict) -> None:
    scope = _worker_scope.get()
    if scope is None:
        # 旧诊断脚本直接运行 worker 的小型回执，不是产品接收协议。
        message = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(message.encode('utf-8')) + 18 > CONTROL_BYTES:
            raise ValueError('worker_file_transport_required')
        print('WORKBENCH_RESULT:' + message, flush=True)
        return
    root, run_id, attempt_id, producer = scope
    if payload.get('run_id', run_id) != run_id or payload.get('attempt_id', attempt_id) != attempt_id:
        raise PermissionError('worker_payload_identity_mismatch')
    value = {**payload, 'run_id': run_id, 'attempt_id': attempt_id}
    kind = READBACK_TYPES.get(producer, 'worker-result.v1') if value.get('success') is True else 'worker-result.v1'
    item = publish_transfer(root, value, run_id=run_id, attempt_id=attempt_id, producer=producer, data_type=kind)
    print('WORKBENCH_RESULT:' + json.dumps({'success': value['success'], 'data': item.model_dump()},
                                           separators=(',', ':'), allow_nan=False), flush=True)
