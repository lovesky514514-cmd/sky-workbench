import asyncio
import json

import httpx

from app.config import SettingsStore
from app.providers.manager import ProviderManager


def provider_store(tmp_path, *, active: str = "ollama") -> SettingsStore:
    store = SettingsStore(tmp_path / "settings.json")
    store.update(
        {
            "active_provider": active,
            "providers": {
                "ollama": {"enabled": active == "ollama", "base_url": "http://local.test", "model": ""},
                "lm_studio": {"enabled": active == "lm_studio", "base_url": "http://local.test/v1", "model": ""},
                "openai_compatible": {"enabled": False},
                "deepseek": {"enabled": False},
                "openai": {"enabled": False},
                "openrouter": {"enabled": False},
            },
        }
    )
    return store


def test_ollama_discovery_capabilities_completion_and_streaming(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 32768}})
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            if payload.get("stream"):
                return httpx.Response(200, content=b'{"message":{"content":"WORK"}}\n{"message":{"content":"BENCH"}}\n')
            return httpx.Response(200, json={"message": {"content": "WORKBENCH_OK"}})
        return httpx.Response(404)

    manager = ProviderManager(provider_store(tmp_path), httpx.MockTransport(handler))

    async def exercise() -> None:
        status = next(item for item in await manager.detect() if item["name"] == "ollama")
        assert status["status"] == "Ready"
        assert status["available_models"] == ["qwen3:8b"]
        assert status["selected_model"] == "qwen3:8b"
        assert status["capabilities"] == {
            "chat": True,
            "streaming": True,
            "tool_calling": True,
            "structured_output": True,
            "context_size": 32768,
            "location": "local",
        }
        tested = await manager.test_connection("ollama")
        assert tested["success"] is True
        assert tested["reply"] == "WORKBENCH_OK"
        events = [event async for event in manager.stream_completion([{"role": "user", "content": "test"}])]
        assert events[0]["type"] == "meta"
        assert "".join(event.get("text", "") for event in events) == "WORKBENCH"
        assert events[-1]["type"] == "done"

    asyncio.run(exercise())


def test_lm_studio_discovery_uses_only_explicit_capability_claims(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "local-model", "context_length": 16384, "capabilities": {"tool_calling": False}}]},
        )

    manager = ProviderManager(provider_store(tmp_path, active="lm_studio"), httpx.MockTransport(handler))
    status = next(item for item in asyncio.run(manager.detect()) if item["name"] == "lm_studio")
    assert status["status"] == "Ready"
    assert status["selected_model"] == "local-model"
    assert status["capabilities"]["tool_calling"] is False
    assert status["capabilities"]["structured_output"] is None
    assert status["capabilities"]["context_size"] == 16384


def test_api_keys_are_memory_only_and_never_persisted(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = store.update({"providers": {"openai": {"enabled": True, "api_key": "top-secret"}}})
    persisted = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "top-secret" not in persisted
    assert "api_key" not in persisted
    assert store.secret("openai") == "top-secret"
    assert "api_key" not in settings["providers"]["openai"]

