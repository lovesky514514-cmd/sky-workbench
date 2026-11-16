from hashlib import sha256
import json
from pathlib import Path
import struct
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.comsol.visualization import inspect_topology, router_for
from app.services.imported_mph import ImportedMphStore
from app.session.store import SessionStore


def topology(*, dimension=3, entity=1, large=False, stl=False):
    meta = {'component_tag': 'comp1', 'geometry_tag': 'geom1', 'mesh_tag': '' if stl else 'mesh1',
            'comsol_version': '6.3', 'length_unit': 'mm', 'dimension': dimension,
            'bounds': [0, 2] * dimension, 'entity_counts': [4, 3, 1, int(dimension == 3)],
            'geometry_objects': ['solid'], 'boundary_features': [], 'domain_materials': [],
            'boundary_domains': {str(i): [1] for i in range(1, 2 if dimension == 3 else 4)}}
    header = json.dumps(meta).encode()
    data = b'SWV1' + struct.pack('>I', len(header)) + header
    if stl:
        return data + struct.pack('>IIII9f', 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0)
    data += struct.pack('>I' + 'd' * (4 * dimension), 4, *range(4 * dimension))
    count = 5_000 if large else 1
    data += struct.pack('>III', 1, 2, count)
    data += struct.pack('>IIII', entity, 0, 1, 2) * count
    return data + struct.pack('>I', 0)


def test_contract_keeps_geometry_only_and_mesh_distinct():
    assert inspect_topology(topology(dimension=2))['coordinate_system'] == 'COMSOL Cartesian XY'
    assert inspect_topology(topology(dimension=3))['representation_type'] == 'MESH_READBACK'
    assert inspect_topology(topology(stl=True))['representation_type'] == 'MODEL_READBACK'
    assert inspect_topology(topology(stl=True))['mesh_status'] == 'unavailable'


@pytest.mark.parametrize('data', [topology(entity=0), topology(entity=2), topology()[:-1], topology() + b'junk'])
def test_corrupt_or_invalid_mapping_not_admitted(data):
    with pytest.raises(ValueError):
        inspect_topology(data)


@pytest.mark.asyncio
async def test_refresh_binds_project_fingerprint_and_bytes_without_a_run(tmp_path):
    items = ImportedMphStore(tmp_path / 'models')

    async def upload():
        yield b'MPH source A'

    item = await items.save('fixture.mph', upload())
    state = {'project': 'workbench', 'tamper': False, 'unavailable': False}
    model = items.working_path(item['id'])
    events = []
    process = {}

    class Manager:
        async def start(self, path, *, preview_fingerprint):
            process.update(state='failed' if state['unavailable'] else 'ready', pid=42, started_at='fixed',
                           model_path=path, preview_fingerprint=preview_fingerprint, model_tag='m')
            events.append('start')
            return process

        def status(self):
            return process

        async def stop_preview(self, path, fingerprint):
            assert path == model and fingerprint == process['preview_fingerprint']
            events.append('stop')

    class Bridge:
        async def visualization(self, identity, *, staging_name):
            data = topology(large=True)
            path = Path(model).parent / 'visualization' / staging_name / identity / 'topology.bin'
            path.write_bytes(data)
            return {'request_id': identity, 'model_tag': 'm', 'bytes': len(data),
                    'sha256': 'f'*64 if state['tamper'] else sha256(data).hexdigest()}

    def forbidden():
        pytest.fail('Imported-model viewer accessed the scientific ledger')

    app = FastAPI()
    app.include_router(router_for(lambda: items, forbidden, Manager(), Bridge(), lambda: state['project'], tmp_path))
    route = f"/api/model-visualization/imported/{item['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(route)
        assert response.status_code == 200, response.text
        manifest = response.json()
        assert manifest['scientific_validation_passed'] is False
        old = route + '/' + manifest['source_model_revision']
        artifact = await client.get(old)
        assert len(artifact.content) > 65_536
        assert sha256(artifact.content).hexdigest() == manifest['artifact']['sha256']
        assert (await client.head(old)).status_code == 204
        state['project'] = 'legacy'
        assert (await client.post(route)).status_code == 404
        assert (await client.get(old)).status_code == 404
        state['project'] = 'workbench'
        model.write_bytes(b'MPH source B')
        assert (await client.get(old)).status_code == 409
        assert (await client.head(old)).status_code == 409
        refreshed = (await client.post(route)).json()
        assert refreshed['source_model_revision'] != manifest['source_model_revision']
        assert (await client.get(old)).status_code == 404
        state['tamper'] = True
        assert (await client.post(route)).status_code == 422
        state['unavailable'] = True
        assert (await client.post(route)).status_code == 503
    assert events == ['start', 'stop'] * 4


@pytest.mark.asyncio
async def test_checkpoint_requires_current_attempt_and_intact_bytes(tmp_path):
    store = SessionStore(tmp_path / 'journal.db')
    store.initialize()
    sid = store.create('test')['id']
    rid = str(uuid4())
    store.begin_run(sid, rid, {})
    aid = store.begin_attempt(rid)
    base = tmp_path / 'models/attempts' / rid / aid / 'model-checkpoints'
    raw = topology(large=True)

    def publish(index, data=raw):
        directory = base / str(index)
        directory.mkdir(parents=True)
        file = directory / 'topology.bin'
        file.write_bytes(data)
        store.append_event(sid, 'run.model_checkpoint', {'run_id': rid, 'attempt_id': aid, 'index': index,
                                                        'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
        return file

    app = FastAPI()
    app.include_router(router_for(None, lambda: store, None, None, lambda: 'test', tmp_path))
    path = f'/api/model-visualization/checkpoint/{sid}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get(path)).status_code == 404
        publish(1)
        first = (await client.get(path)).json()
        assert first['attempt_id'] == aid and first['scientific_validation_passed'] is False
        old = path + '/' + first['source_model_revision']
        assert (await client.get(old)).content == raw
        file = publish(2, raw[:-1])
        assert (await client.get(path)).status_code == 404
        assert (await client.get(old)).status_code == 404
        file.write_bytes(raw)
        current = (await client.get(path)).json()
        assert current['checkpoint_index'] == 2
        store.request_cancel(sid)
        assert (await client.get(path)).status_code == 404
        assert (await client.get(path + '/' + current['source_model_revision'])).status_code == 404
