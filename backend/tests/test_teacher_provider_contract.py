"""Provider routing must reflect the user's explicit choice and real API evidence."""

import asyncio
import json

import httpx

from app.config import SettingsStore
from app.providers.manager import ProviderManager


def test_explicit_cloud_choice_never_falls_back_to_ollama(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "kimi", "providers": {
        "kimi": {"enabled": True, "model": "kimi-test", "api_key": "synthetic-test-key"},
        "ollama": {"enabled": True, "base_url": "http://127.0.0.1", "model": "qwen3:8b"},
    }})
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(404)

    manager = ProviderManager(store, httpx.MockTransport(handler))
    result = asyncio.run(manager.complete([{"role": "user", "content": "test"}]))
    assert result is None
    assert "/api/chat" not in calls


def test_doubao_configured_without_models_endpoint_can_be_live_tested(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "doubao", "providers": {
        "doubao": {"enabled": True, "model": "doubao-test", "api_key": "synthetic-test-key"},
    }})

    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            assert request.headers["authorization"] == "Bearer synthetic-test-key"
            assert json.loads(request.content)["model"] == "doubao-test"
            return httpx.Response(200, json={"choices": [{"message": {"content": "WORKBENCH_OK"}}]})
        return httpx.Response(404)

    manager = ProviderManager(store, httpx.MockTransport(handler))
    status = next(item for item in asyncio.run(manager.detect()) if item["name"] == "doubao")
    assert status["status"] == "Configured"
    tested = asyncio.run(manager.test_connection("doubao"))
    assert tested["success"] is True
    assert tested["model"] == "doubao-test"


def test_packaged_default_does_not_override_saved_cloud_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("COMSOL_WORKBENCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("COMSOL_WORKBENCH_PRODUCT_MODEL", "qwen3:8b")
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "deepseek"})
    settings = store.load()
    assert settings["active_provider"] == "deepseek"
    assert settings["providers"]["deepseek"]["enabled"] is False


def test_missing_ollama_is_not_a_cloud_http_400(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "ollama", "providers": {
        "ollama": {"enabled": True, "base_url": "http://127.0.0.1", "model": "qwen3:8b"},
    }})
    manager = ProviderManager(store, httpx.MockTransport(lambda request: httpx.Response(404)))
    assert asyncio.run(manager.complete([{"role": "user", "content": "test"}])) is None


def test_kimi_uses_exact_selected_model_and_never_lists_paid_models(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "kimi", "providers": {
        "kimi": {"enabled": True, "model": "kimi-test", "api_key": "synthetic-test-key"},
    }})
    calls = []

    async def handler(request):
        calls.append((request.url.host, request.url.path))
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        assert json.loads(request.content)["model"] == "kimi-test"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    manager = ProviderManager(store, httpx.MockTransport(handler))
    result = asyncio.run(manager.complete([{"role": "user", "content": "test"}]))
    assert result is not None and result.provider == "kimi" and result.text == "ok"
    assert [path for host, path in calls if host == "api.moonshot.cn"] == ["/v1/chat/completions"]


def test_claude_and_gemini_use_native_protocols_without_key_persistence(tmp_path):
    for provider in ("claude", "gemini"):
        store = SettingsStore(tmp_path / f"{provider}.json")
        store.update({"active_provider": provider, "providers": {
            provider: {"enabled": True, "model": "synthetic-model", "api_key": "synthetic-test-key"},
        }})

        async def handler(request):
            payload = json.loads(request.content)
            if provider == "claude":
                assert request.url.path == "/v1/messages"
                assert request.headers["x-api-key"] == "synthetic-test-key"
                assert payload["messages"][0]["content"] == "test"
                return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
            assert request.url.path == "/v1beta/models/synthetic-model:generateContent"
            assert request.headers["x-goog-api-key"] == "synthetic-test-key"
            assert payload["contents"][0]["parts"][0]["text"] == "test"
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

        manager = ProviderManager(store, httpx.MockTransport(handler))
        result = asyncio.run(manager.complete([{"role": "user", "content": "test"}]))
        assert result is not None and result.provider == provider and result.text == "ok"
        assert "synthetic-test-key" not in store.path.read_text(encoding="utf-8")


def test_cloud_rejects_insecure_or_embedded_credential_urls(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "doubao", "providers": {
        "doubao": {"enabled": True, "base_url": "http://user:pass@example.test/v1", "model": "test", "api_key": "synthetic-test-key"},
    }})
    manager = ProviderManager(store, httpx.MockTransport(lambda request: httpx.Response(500)))
    assert asyncio.run(manager.preferred()) is None


def test_automatic_local_mode_rejects_remote_generic_endpoint(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "", "providers": {
        "ollama": {"enabled": False}, "lm_studio": {"enabled": False},
        "openai_compatible": {"enabled": True, "base_url": "https://remote.example/v1", "model": "remote"},
    }})
    manager = ProviderManager(store, httpx.MockTransport(lambda request: httpx.Response(200)))
    assert asyncio.run(manager.preferred()) is None


def test_http_200_without_text_never_counts_as_connection_success(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "doubao", "providers": {
        "doubao": {"enabled": True, "model": "doubao-test", "api_key": "synthetic-test-key"},
    }})
    manager = ProviderManager(store, httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": None}}]})))
    tested = asyncio.run(manager.test_connection("doubao"))
    assert tested["success"] is False
    assert asyncio.run(manager.complete([{"role": "user", "content": "test"}])) is None


def test_empty_stream_never_emits_done(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"active_provider": "doubao", "providers": {
        "doubao": {"enabled": True, "model": "doubao-test", "api_key": "synthetic-test-key"},
    }})
    manager = ProviderManager(store, httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"data: [DONE]\n\n")))

    async def events():
        return [event async for event in manager.stream_completion([{"role": "user", "content": "test"}])]

    result = asyncio.run(events())
    assert [item["type"] for item in result] == ["meta", "unavailable"]
