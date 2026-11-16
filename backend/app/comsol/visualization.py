"""Bounded, read-only topology snapshots. No RunJournal or scientific verdict writes."""

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime
from hashlib import file_digest, sha256
import math
from pathlib import Path
import re
import os
import struct
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Response

from app.adapter.rc42_contract import current_completion, verified_verdict
from app.comsol.bridge_preview import completed_model
from app.comsol.view_checkpoint import CHECKPOINT_EVENT, checkpoint_data
from app.execution.files import bounded_read, reject_reparse
from app.execution.scientific_transfer import decode_object

MAX_BYTES = 32_000_000
TYPE_NAMES = ('vtx', 'edg', 'tri', 'quad', 'tet', 'pyr', 'prism', 'hex')
CORNERS = (1, 2, 3, 4, 4, 5, 6, 8)
DIMENSIONS = (0, 1, 2, 2, 3, 3, 3, 3)
HASH = re.compile(r'[a-f0-9]{64}')
RUN_ID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
TAG = re.compile(r'[A-Za-z0-9_./:-]{1,160}')
INVALIDATIONS = {'evidence.invalidated', 'validation.invalidated_by_human_correction', 'artifact.integrity_failed'}


def inspect_topology(data: bytes) -> dict:
    """Validate the exact bytes to be served, including all volume connectivity."""
    if len(data) > MAX_BYTES or data[:4] not in {b'SWV1', b'SWV2'}:
        raise ValueError('invalid_topology_header')
    offset = 4

    def read(fmt):
        nonlocal offset
        try:
            value = struct.unpack_from('>' + fmt, data, offset)
        except struct.error as exc:
            raise ValueError('truncated_topology') from exc
        offset += struct.calcsize('>' + fmt)
        return value

    meta_size, = read('I')
    if not 0 < meta_size <= 262_144 or offset + meta_size > len(data):
        raise ValueError('invalid_topology_metadata_size')
    meta = decode_object(data[offset:offset + meta_size])
    offset += meta_size
    for key in ('component_tag', 'geometry_tag', 'mesh_tag', 'comsol_version', 'length_unit'):
        value = meta.get(key)
        if key == 'mesh_tag' and value == '':
            continue
        if not isinstance(value, str) or not TAG.fullmatch(value):
            raise ValueError('invalid_topology_metadata')
    dim, bounds, entities = meta.get('dimension'), meta.get('bounds'), meta.get('entity_counts')
    if (type(dim) is not int or dim not in (2, 3) or not isinstance(bounds, list) or len(bounds) != dim * 2
            or any(type(n) not in (int, float) or not -1e30 <= n <= 1e30 for n in bounds)
            or any(bounds[2*i] > bounds[2*i+1] for i in range(dim))
            or not isinstance(entities, list) or len(entities) != 4
            or any(type(n) is not int or not 0 <= n <= 500_000 for n in entities)):
        raise ValueError('invalid_geometry_metadata')
    objects = meta.get('geometry_objects')
    if not isinstance(objects, list) or any(not isinstance(name, str) or not TAG.fullmatch(name) for name in objects):
        raise ValueError('invalid_geometry_objects')

    def valid_ids(ids, maximum):
        return (isinstance(ids, list) and all(type(n) is int and 0 < n <= maximum for n in ids))

    adjacency = meta.get('boundary_domains')
    if (not isinstance(adjacency, dict) or set(adjacency) != {str(i) for i in range(1, entities[dim-1] + 1)}
            or any(not valid_ids(ids, entities[dim]) for ids in adjacency.values())):
        raise ValueError('invalid_boundary_mapping')
    for key, entity_dim in (('boundary_features', dim-1), ('domain_materials', dim)):
        entries = meta.get(key)
        if (not isinstance(entries, list) or len(entries) > 256 or any(
                not isinstance(item, dict) or set(item) != {'tag', 'label', 'ids'}
                or not isinstance(item['tag'], str) or not TAG.fullmatch(item['tag'])
                or not isinstance(item['label'], str) or len(item['label']) > 160
                or not valid_ids(item['ids'], entities[entity_dim]) for item in entries)):
            raise ValueError('invalid_selection_mapping')
    vertices, = read('I')
    if vertices > 250_000:
        raise ValueError('topology_vertex_limit')
    for _ in range(vertices):
        xyz = read('d' * dim)
        if any(not math.isfinite(x) or abs(x) > 1e30 for x in xyz):
            raise ValueError('invalid_mesh_vertex')
    groups, = read('I')
    if groups > len(CORNERS) or bool(groups) != bool(vertices) or bool(groups) != bool(meta['mesh_tag']):
        raise ValueError('invalid_mesh_group_count')
    counts, total = {}, 0
    for _ in range(groups):
        code, count = read('II')
        if code >= len(CORNERS) or DIMENSIONS[code] > dim or TYPE_NAMES[code] in counts or not 0 < count <= 500_000 - total:
            raise ValueError('invalid_mesh_element_group')
        counts[TYPE_NAMES[code]] = count
        total += count
        for _ in range(count):
            entity, *nodes = read('I' * (1 + CORNERS[code]))
            if (not 0 < entity <= entities[DIMENSIONS[code]] or any(n >= vertices for n in nodes)
                    or len(set(nodes)) != len(nodes)):
                raise ValueError('invalid_mesh_entity_or_connectivity')
    surfaces, = read('I')
    encoding = meta.get('geometry_encoding', 'object_triangles')
    shape = {'object_triangles': (3, len(objects), 64), 'boundary_curves': (2, entities[1], 1024),
             'boundary_triangles': (3, entities[2], 128)}.get(encoding) if isinstance(encoding, str) else None
    if (shape is None or groups and surfaces or not groups and (
            dim != shape[0] or surfaces != shape[1] or not 0 < surfaces <= shape[2])
            or groups and not (counts.get('tri') or counts.get('quad'))):
        raise ValueError('invalid_display_surface')
    triangles = 0
    for _ in range(surfaces):
        count, = read('I')
        if encoding == 'boundary_curves':
            if count != 65:
                raise ValueError('invalid_planar_boundary_samples')
            for _ in range(count):
                if any(not math.isfinite(x) or abs(x) > 1e30 for x in read('dd')):
                    raise ValueError('invalid_curve_vertex')
            continue
        triangles += count
        if count == 0 or triangles > 80_000:
            raise ValueError('topology_surface_limit')
        for _ in range(count):
            if any(not math.isfinite(x) or abs(x) > 1e30 for x in read('9f')):
                raise ValueError('invalid_surface_vertex')
    if data[:4] == b'SWV2':
        heat = meta.get('heat_field')
        samples, = read('I')
        if (dim != 3 or not counts.get('tet') or not isinstance(heat, dict) or set(heat) != {'dataset', 'time_s'}
                or not isinstance(heat['dataset'], str) or not TAG.fullmatch(heat['dataset'])
                or type(heat['time_s']) not in (int, float) or not 0 <= heat['time_s'] <= 1e12 or samples != vertices):
            raise ValueError('invalid_heat_field_descriptor')
        for _ in range(samples):
            value, = read('d')
            if not 0 < value < 1e7:
                raise ValueError('invalid_heat_field_sample')
    elif 'heat_field' in meta:
        raise ValueError('unexpected_heat_field')
    if offset != len(data):
        raise ValueError('unexpected_topology_bytes')
    return {**meta, 'coordinate_system': 'COMSOL Cartesian XYZ' if dim == 3 else 'COMSOL Cartesian XY',
            'geometry_status': 'read_back', 'mesh_status': 'read_back' if groups else 'unavailable',
            'representation_type': 'MESH_READBACK' if groups else 'MODEL_READBACK',
            'vertex_count': vertices, 'element_counts': counts, 'surface_triangles': triangles}


def source_fingerprint(path: Path) -> str:
    reject_reparse(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= 1024**3 or before.st_nlink != 1:
            raise ValueError('invalid_preview_source')
        digest = file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        reject_reparse(path)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink):
            raise ValueError('preview_source_changed')
    return digest


def router_for(imports, store, manager, bridge, current_project, workspace: Path) -> APIRouter:
    router = APIRouter(prefix='/api/model-visualization')
    snapshots = OrderedDict()
    capture_lock = asyncio.Lock()

    def active_checkpoint(identity, project):
        session = store().get(identity)
        if not project or session['project_id'] != project:
            raise KeyError(identity)
        run = store().latest_run(identity)
        if (not run or not all(RUN_ID.fullmatch(run.get(key) or '') for key in ('run_id', 'attempt_id'))
                or not store().attempt_is_current(run['run_id'], run['attempt_id'])):
            raise KeyError(identity)
        rid, aid = run['run_id'], run['attempt_id']
        for event in reversed(session['events']):
            payload = event['payload']
            if event['type'] == CHECKPOINT_EVENT and (payload.get('run_id'), payload.get('attempt_id')) == (rid, aid):
                index, size, digest = (payload.get(k) for k in ('index', 'bytes', 'sha256'))
                revision = sha256(f'{project}:{identity}:{rid}:{aid}:{index}:{digest}'.encode()).hexdigest()
                return rid, aid, index, size, digest, revision, event['created_at']
        raise KeyError(identity)

    @router.get('/checkpoint/{identity}')
    async def checkpoint_manifest(identity: str):
        project = current_project()
        try:
            checkpoint = await asyncio.to_thread(active_checkpoint, identity, project)
            rid, aid, index, size, digest, revision, created = checkpoint
            raw = await asyncio.to_thread(checkpoint_data, workspace / 'models/attempts' / rid / aid, index, size, digest)
            metadata = await asyncio.to_thread(inspect_topology, raw)
            if current_project() != project or await asyncio.to_thread(active_checkpoint, identity, project) != checkpoint:
                raise KeyError(identity)
        except (KeyError, OSError, ValueError) as exc:
            raise HTTPException(404, 'Current RUN model checkpoint unavailable') from exc
        return {'project_id': project, 'model_identity': identity, 'source_kind': 'checkpoint',
                'run_id': rid, 'attempt_id': aid, 'checkpoint_index': index,
                'source_model_revision': revision, 'created_at': created, 'verification_status': 'topology_verified',
                'scientific_validation_passed': False, 'artifact': {'bytes': size, 'sha256': digest}, **metadata}

    @router.get('/checkpoint/{identity}/{revision}')
    async def checkpoint_artifact(identity: str, revision: str):
        project = current_project()
        try:
            checkpoint = await asyncio.to_thread(active_checkpoint, identity, project)
            rid, aid, index, size, digest, expected, _ = checkpoint
            if revision != expected:
                raise KeyError(identity)
            raw = await asyncio.to_thread(checkpoint_data, workspace / 'models/attempts' / rid / aid, index, size, digest)
            if current_project() != project or await asyncio.to_thread(active_checkpoint, identity, project) != checkpoint:
                raise KeyError(identity)
        except (KeyError, OSError, ValueError) as exc:
            raise HTTPException(404, 'Current RUN model checkpoint unavailable') from exc
        return Response(raw, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    def resolve(kind, identity, project):
        if not project:
            raise HTTPException(404, 'Model not available in this project')
        try:
            if kind == 'imported':
                return imports().working_path(identity, project_id=project)
            if kind in ('session', 'heat-result'):
                if kind == 'heat-result':
                    completed_heat(identity, project)
                return completed_model(store(), identity, project, workspace)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(404, 'Model not available in this project') from exc
        raise HTTPException(404, 'Unknown model source')

    def completed_heat(identity, project):
        session = store().get(identity)
        if session['project_id'] != project:
            raise KeyError(identity)
        run = store().latest_run(identity)
        completion, sequence = current_completion(run, session['events'])
        if (not verified_verdict(run, completion) or completion['execution_verdict'].get('workflow_id') != 'heat_transfer_3d_cooling'
                or any(event['sequence'] > sequence and event['type'] in INVALIDATIONS for event in session['events'])):
            raise ValueError('Verified completed 3D Heat evidence required')
        record = completion['evidence_contract'].get('artifacts', {}).get('model', {})
        digest = record.get('sha256')
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError('Verified Heat MPH hash required')
        return run['run_id'], run['attempt_id'], digest

    @router.post('/{kind}/{identity}')
    async def capture(kind: str, identity: str):
        project = current_project()
        if capture_lock.locked():
            raise HTTPException(409, 'Another visualization refresh is in progress')
        async with capture_lock:
            model = await asyncio.to_thread(resolve, kind, identity, project)
            fingerprint = None
            result_identity = None
            try:
                fingerprint = await asyncio.to_thread(source_fingerprint, model)
                if kind == 'heat-result':
                    result_identity = await asyncio.to_thread(completed_heat, identity, project)
                    if fingerprint != result_identity[2]:
                        raise ValueError('Heat result source hash mismatch')
                process = await manager.start(str(model), preview_fingerprint=fingerprint)
                for _ in range(184):
                    if process.get('state') != 'starting':
                        break
                    await asyncio.sleep(.5)
                    process = manager.status()
                if process.get('state') != 'ready':
                    raise HTTPException(503, 'COMSOL model load failed or timed out. Check the Bridge log and license.')
                stamp = (process.get('pid'), process.get('started_at'), str(model), fingerprint)

                def current():
                    state = manager.status()
                    return state.get('state') == 'ready' and (
                        state.get('pid'), state.get('started_at'), state.get('model_path'), state.get('preview_fingerprint')) == stamp

                if not current():
                    raise HTTPException(409, 'COMSOL model changed while opening')
                root = model.parent / 'visualization'
                reject_reparse(root)
                root.mkdir(exist_ok=True)
                request_id = uuid4().hex
                with TemporaryDirectory(prefix='preview-', dir=root) as staging:
                    # The bridge may write only to an existing UUID directory beside its loaded MPH.
                    directory = Path(staging) / request_id
                    directory.mkdir()
                    options = {'heat_result': True} if result_identity else {}
                    descriptor = await bridge.visualization(request_id, staging_name=Path(staging).name, **options)
                    size, digest = descriptor.get('bytes'), descriptor.get('sha256')
                    if (descriptor.get('request_id') != request_id or descriptor.get('model_tag') != process.get('model_tag')
                            or type(size) is not int or not 0 < size <= MAX_BYTES
                            or not isinstance(digest, str) or not HASH.fullmatch(digest)):
                        raise ValueError('invalid_visualization_descriptor')
                    raw = await asyncio.to_thread(bounded_read, directory, 'topology.bin', size, single_link=True)
                    if len(raw) != size or sha256(raw).hexdigest() != digest:
                        raise ValueError('visualization_integrity_failed')
                    metadata = await asyncio.to_thread(inspect_topology, raw)
                    if bool(metadata.get('heat_field')) != bool(result_identity):
                        raise ValueError('Unexpected scientific field payload')
                latest_fingerprint = await asyncio.to_thread(source_fingerprint, model)
                if (not current() or latest_fingerprint != fingerprint or current_project() != project
                        or await asyncio.to_thread(resolve, kind, identity, project) != model
                        or result_identity and await asyncio.to_thread(completed_heat, identity, project) != result_identity):
                    raise HTTPException(409, 'Model or project changed during readback')
            except RuntimeError as exc:
                raise HTTPException(409, 'COMSOL Bridge is owned by another operation') from exc
            except httpx.TimeoutException as exc:
                raise HTTPException(504, 'COMSOL visualization readback timed out') from exc
            except httpx.HTTPStatusError as exc:
                raise HTTPException(422, 'COMSOL could not read the built geometry or mesh. Check the Bridge log.') from exc
            except (KeyError, ValueError, OSError, httpx.HTTPError) as exc:
                raise HTTPException(422, 'COMSOL visualization could not be verified') from exc
            finally:
                if fingerprint is not None:
                    await manager.stop_preview(model, fingerprint)
            revision = sha256(f'{fingerprint}:{digest}:{result_identity or ""}'.encode('ascii')).hexdigest()
            key = (project, kind, identity)
            manifest = {'project_id': project, 'model_identity': identity, 'source_kind': kind,
                        'source_fingerprint': fingerprint, 'source_model_revision': revision,
                        'created_at': datetime.now(UTC).isoformat(), 'verification_status': 'topology_verified',
                        'scientific_validation_passed': False, 'artifact': {'bytes': size, 'sha256': digest},
                        **metadata}
            if result_identity:
                manifest.update(run_id=result_identity[0], attempt_id=result_identity[1])
            snapshots[key] = (revision, fingerprint, raw, result_identity)
            snapshots.move_to_end(key)
            if len(snapshots) > 2:
                snapshots.popitem(last=False)
            return manifest

    async def snapshot_bytes(kind: str, identity: str, revision: str):
        project = current_project()
        model = await asyncio.to_thread(resolve, kind, identity, project)
        item = snapshots.get((project, kind, identity))
        if item is None or item[0] != revision:
            raise HTTPException(404, 'Topology snapshot expired. Refresh the model.')
        try:
            fingerprint = await asyncio.to_thread(source_fingerprint, model)
            result_identity = await asyncio.to_thread(completed_heat, identity, project) if item[3] else None
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(409, 'Source MPH unavailable or changed. Refresh the model.') from exc
        if (fingerprint != item[1] or snapshots.get((project, kind, identity)) is not item
                or current_project() != project or result_identity != item[3]):
            raise HTTPException(409, 'Source MPH changed. Refresh the model.')
        return item[2]

    @router.head('/{kind}/{identity}/{revision}')
    async def check_snapshot(kind: str, identity: str, revision: str):
        await snapshot_bytes(kind, identity, revision)
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})

    @router.get('/{kind}/{identity}/{revision}')
    async def topology(kind: str, identity: str, revision: str):
        raw = await snapshot_bytes(kind, identity, revision)
        return Response(raw, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    return router
