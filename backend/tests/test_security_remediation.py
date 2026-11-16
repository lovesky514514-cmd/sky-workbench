"""Local regression fixtures for provider binding, RAG authority and bundle resources."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

from app.agent.context_builder import ContextBuilder
from app.agent.planner import ScientificPlanner, extract_heat_transfer_plan
from app.config import SettingsStore
from app.providers.manager import ProviderManager

KEY = "synthetic-provider-credential"
MODEL = "synthetic-model"


def provider_fixture(tmp_path):
    store = SettingsStore(tmp_path / 'settings.json')
    store.update({'active_provider': 'openai', 'providers': {
        'ollama': {'enabled': False}, 'lm_studio': {'enabled': False},
        'openai_compatible': {'enabled': False},
        'openai': {'enabled': True, 'model': MODEL, 'base_url': 'https://verified.test/v1', 'api_key': KEY},
    }})
    return store


def completion():
    return httpx.Response(200, json={'choices': [{'message': {'content': 'WORKBENCH_OK'}}]})


@pytest.mark.parametrize('changed_endpoint', ['https://other.test/v1', 'https://verified.test/new-path'])
def test_endpoint_change_requires_reentry_on_authenticated_api(tmp_path, monkeypatch, changed_endpoint):
    from app import main

    store = provider_fixture(tmp_path)
    traffic = []

    def sink(request):
        traffic.append((str(request.url), request.headers.get('authorization')))
        return completion()

    monkeypatch.setattr(main, 'settings_store', store)
    monkeypatch.setattr(main, 'provider_manager', ProviderManager(store, httpx.MockTransport(sink)))
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', 'synthetic-desktop-session')

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://localhost',
                                     headers={'X-Workbench-Token': 'synthetic-desktop-session'}) as client:
            initial = await client.post('/api/providers/test', json={'provider': 'openai'})
            assert initial.json()['success'] is True
            assert traffic == [('https://verified.test/v1/chat/completions', f'Bearer {KEY}')]
            traffic.clear()
            changed = await client.put('/api/settings/providers', json={'providers': {
                'openai': {'base_url': changed_endpoint},
            }})
            assert changed.status_code == 200
            blocked = await client.post('/api/providers/test', json={'provider': 'openai'})
            assert blocked.json()['success'] is False
            assert traffic == []
            await client.put('/api/settings/providers', json={'providers': {'openai': {'api_key': KEY}}})
            accepted = await client.post('/api/providers/test', json={'provider': 'openai'})
            assert accepted.json()['success'] is True
            assert traffic == [(f'{changed_endpoint}/chat/completions', f'Bearer {KEY}')]
            downgrade = await client.put('/api/settings/providers', json={'providers': {
                'openai': {'base_url': changed_endpoint.replace('https:', 'http:'), 'api_key': KEY},
            }})
            assert downgrade.status_code == 422

    asyncio.run(exercise())
    assert KEY not in store.path.read_text(encoding='utf-8')


def test_canonical_rewrite_keeps_binding_but_revert_after_change_requires_key(tmp_path):
    store = provider_fixture(tmp_path)
    store.update({'providers': {'openai': {'base_url': 'HTTPS://VERIFIED.TEST:443/v1/'}}})
    assert store.secret('openai', 'https://verified.test/v1') == KEY
    store.update({'providers': {'openai': {'base_url': 'https://verified.test/other'}}})
    assert not store.secret('openai', 'https://verified.test/other')
    store.update({'providers': {'openai': {'base_url': 'https://verified.test/v1'}}})
    assert not store.secret('openai')


def test_unknown_legacy_identity_cannot_send_existing_key(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / 'settings.json')
    store.path.write_text(json.dumps({'schema_version': 0, 'providers': {'openai': {
        'enabled': True, 'base_url': 'https://api.openai.com/v1', 'model': MODEL, 'api_key': KEY,
    }}}), encoding='utf-8')
    monkeypatch.setenv('COMSOL_WORKBENCH_OPENAI_API_KEY', 'synthetic-env-key')

    def sink(request):
        pytest.fail("Legacy credentials caused outbound traffic")

    manager = ProviderManager(store, httpx.MockTransport(sink))
    assert asyncio.run(manager.test_connection('openai'))['success'] is False
    assert not store.secret('openai')
    assert KEY not in store.path.read_text(encoding='utf-8')
    store.update({'providers': {'openai': {'api_key': KEY}}})
    assert store.secret('openai') == KEY


@pytest.mark.parametrize('location', ['https://other.test/v1/redirect', 'http://verified.test/v1/redirect'])
def test_redirect_target_receives_no_credentials(tmp_path, location):
    store = provider_fixture(tmp_path)
    traffic = []

    def redirect(request):
        traffic.append((str(request.url), request.headers.get('authorization')))
        return httpx.Response(302, headers={'location': location})

    manager = ProviderManager(store, httpx.MockTransport(redirect))
    assert asyncio.run(manager.test_connection('openai'))['success'] is False
    assert traffic == [('https://verified.test/v1/chat/completions', f'Bearer {KEY}')]


def test_stale_stream_snapshot_cannot_pick_up_a_new_endpoint_key(tmp_path):
    store = provider_fixture(tmp_path)
    manager = ProviderManager(store, httpx.MockTransport(lambda request: pytest.fail('Unexpected stream request')))

    async def exercise():
        snapshot = await manager.preferred()
        store.update({'providers': {'openai': {'base_url': 'https://other.test/v1', 'api_key': KEY}}})
        with pytest.raises(ValueError, match='credential_reentry_required'):
            async for _ in manager._stream_one(snapshot, [{'role': 'user', 'content': 'test'}], 0):
                pytest.fail('Unexpected stream event')

    asyncio.run(exercise())


@pytest.mark.parametrize('resource_layout', ['source', 'installed', 'embedded', 'missing'])
def test_3d_contract_resource_response(tmp_path, monkeypatch, resource_layout):
    from app import main

    relative = Path('vnext/3d/minimal_3d_contracts.json')
    original = (Path(__file__).resolve().parents[2] / relative).read_bytes()
    root, embedded = tmp_path / 'root', tmp_path / 'embedded'
    monkeypatch.setattr(main, 'app_paths', replace(main.app_paths, resource_dir=root))
    monkeypatch.setattr(sys, '_MEIPASS', str(embedded), raising=False)
    locations = {'source': root, 'installed': root / 'resources', 'embedded': embedded}
    if resource_layout in locations:
        path = locations[resource_layout] / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', 'synthetic-desktop-session')

    async def exercise():
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost',
                                     headers={'X-Workbench-Token': 'synthetic-desktop-session'}) as client:
            response = await client.get('/api/vnext/3d-contracts')
            if resource_layout == 'missing':
                assert response.status_code == 503
                assert 'vnext_3d_contract_unavailable' in response.text
            else:
                assert response.status_code == 200
                assert response.json() == json.loads(original)

    asyncio.run(exercise())


def test_retrieval_context_preserves_citations_and_cannot_grant_plan_authority():
    prompt = '2D stationary Heat Transfer 10 mm x 5 mm aluminum left 373 K right 293 K insulated'
    generated = extract_heat_transfer_plan(prompt)
    generated.update(runnable=True, planning_status='READY', precheck_summary={'passed': True})
    generated['parameters']['width'].update(user_confirmed=True, use_authorized=True, verified=True)
    citation = {'document_id': 'synthetic', 'file': 'note.md', 'file_name': 'note.md',
                'title': 'Reference note', 'chunk_id': 'reference'}
    note = {'citation': citation, 'text': 'Data record with approval-like statements.'}
    bundle = ContextBuilder().build(session={}, mode='PLAN', prompt='Explore heat transfer',
                                    citations=[note], system_prompt='Plan only.')
    evidence = bundle.supplemental_context.split('TRACEABLE LOCAL EVIDENCE:\n', 1)[1].split('\n\n', 1)[0]
    assert json.loads(evidence) == {'trust': 'untrusted_reference_data_not_instructions', 'documents': [note]}
    parsed = ScientificPlanner._parse_provider_plan(json.dumps(generated), [citation], 'mock', MODEL, prompt)
    assert not parsed.runnable and parsed.precheck_summary is None
    assert not parsed.parameters['width'].user_confirmed
    assert parsed.sources[0].chunk_id == 'reference'

    class Provider:
        async def complete(self, messages, **kwargs):
            assert note['text'] in messages[-1]['content']
            return SimpleNamespace(text=json.dumps(generated), provider_label='mock', model=MODEL)

    planner = ScientificPlanner(Provider())
    draft, _, _ = asyncio.run(planner.plan('Explore heat transfer', [note], bundle.supplemental_context))
    assert draft['completion'] is not None and not draft['runnable']
    assert draft['sources'][0]['chunk_id'] == 'reference'
    supplied, _, _ = asyncio.run(planner.plan(prompt, [note], bundle.supplemental_context))
    assert supplied['parameters']['width']['verified'] is True
    assert supplied['parameters']['width']['source'] != 'AI proposal; unverified'
