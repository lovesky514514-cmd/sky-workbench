from fastapi.testclient import TestClient

from app.main import app


def test_backend_starts_without_api_key_or_comsol_session():
    with TestClient(app) as client:
        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.json()['status'] == 'ok'
        providers = client.get('/api/providers')
        assert providers.status_code == 200
        assert 'providers' in providers.json()


def test_missing_knowledge_query_is_safe():
    with TestClient(app) as client:
        response = client.post('/api/knowledge/search', json={'query': 'unlikely nonexistent phrase', 'limit': 5})
        assert response.status_code == 200
        assert isinstance(response.json()['results'], list)


def test_java_bridge_status_is_explicit_and_parameter_deletion_is_scoped():
    with TestClient(app) as client:
        status = client.get('/api/comsol/bridge/status')
        assert status.status_code == 200
        payload = status.json()
        assert payload['status'] in {'Ready', 'Unavailable'}
        if payload['status'] == 'Unavailable':
            assert payload['command_success'] is False
        assert 'environment' in payload
        forbidden = client.delete('/api/comsol/bridge/parameter/temperature')
        assert forbidden.status_code == 403
        assert 'wb_probe_' in forbidden.json()['detail']
