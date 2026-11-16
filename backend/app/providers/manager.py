"""Local-first provider discovery, capability probing, testing and streaming."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

import httpx

from app.config import SettingsStore, settings_store
from app.providers import cloud_protocols
from app.providers.deepseek import chat_payload as deepseek_payload
from app.providers.endpoints import LOCAL_PROVIDERS, provider_endpoint
from app.scientific.schemas import ScientificPlan
from app.services.model_manager import ollama_executable
from app.providers.metering import CompletionTrace, active_trace, request_started, response_received


LABELS = {
    "ollama": "Ollama",
    "lm_studio": "LM Studio",
    "openai_compatible": "Generic OpenAI Compatible",
    "deepseek": "DeepSeek API",
    "kimi": "Kimi API",
    "doubao": "Doubao API",
    "openai": "OpenAI API",
    "claude": "Claude API",
    "gemini": "Gemini API",
    "openrouter": "OpenRouter",
}

@dataclass(frozen=True, slots=True)
class ModelCapability:
    chat: bool | None = None
    streaming: bool | None = None
    tool_calling: bool | None = None
    structured_output: bool | None = None
    context_size: int | None = None
    location: str = "local"


@dataclass(frozen=True, slots=True)
class ModelDetail:
    name: str
    size_bytes: int | None = None
    size_label: str = "Unknown"
    parameter_size: str | None = None
    quantization_level: str | None = None
    family: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    label: str
    kind: str
    enabled: bool
    status: str
    detail: str
    base_url: str
    model: str
    available_models: tuple[str, ...] = ()
    model_details: tuple[ModelDetail, ...] = ()
    capabilities: ModelCapability = ModelCapability()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available_models"] = list(self.available_models)
        data["model_details"] = [asdict(item) for item in self.model_details]
        data["selected_model"] = self.model
        return data


@dataclass(frozen=True, slots=True)
class CompletionResult:
    text: str
    provider: str
    provider_label: str
    model: str


class ProviderManager:
    """Resolve, probe and invoke LLM providers without coupling them to the Agent."""

    def __init__(
        self,
        store: SettingsStore = settings_store,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store
        self.transport = transport

    def for_capability(self, role):
        from app.providers.routing import settings_for_role
        selected = settings_for_role(self.store, role)
        return ProviderManager(selected, self.transport) if selected is not None else self

    async def detect(self) -> list[dict[str, Any]]:
        configs = self.store.load()["providers"]
        tasks = [self._detect_one(name, config) for name, config in configs.items()]
        return [status.as_dict() for status in await asyncio.gather(*tasks)]

    async def preferred(self) -> ProviderStatus | None:
        statuses = [ProviderStatus(**self._status_kwargs(item)) for item in await self.detect()]
        usable = {item.name: item for item in statuses if item.status in {"Ready", "Configured"}}
        selected = str(self.store.load().get("active_provider", ""))
        # An explicit choice is authoritative: never send its prompt to a
        # different local or cloud endpoint after a failed request.
        if selected:
            return usable.get(selected)
        for name in ("ollama", "lm_studio", "openai_compatible"):
            if name in usable:
                return usable[name]
        return None

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        allow_json_retry: bool = True,
    ) -> CompletionResult | None:
        preferred = await self.preferred()
        if preferred is None:
            return None
        try:
            text = await self._complete_one(
                preferred.name, messages, temperature=temperature,
                json_mode=json_mode, detected_model=preferred.model,
                json_schema=json_schema,
                allow_json_retry=allow_json_retry,
            )
            return CompletionResult(text, preferred.name, preferred.label, preferred.model)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError, json.JSONDecodeError):
            return None

    async def complete_metered(self, messages, *, json_schema, max_output_tokens=1200, observe=None):
        """复用同一 Provider 调用；专家路径禁止隐含重试和跨端点降级。"""
        if type(max_output_tokens) is not int or not 64 <= max_output_tokens <= 8192:
            raise ValueError('invalid_expert_output_budget')
        trace = CompletionTrace(max_output_tokens)
        token = active_trace.set(trace)
        started = time.perf_counter()
        try:
            result = await self.complete(messages, temperature=0., json_mode=True,
                                         json_schema=json_schema, allow_json_retry=False)
            return result, {**trace.as_dict(), 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
        finally:
            try:
                if observe is not None:
                    observe({**trace.as_dict(), 'latency_ms': round((time.perf_counter()-started)*1000, 3)})
            finally:
                active_trace.reset(token)

    async def stream_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> AsyncIterator[dict[str, Any]]:
        status = await self.preferred()
        if status is None:
            yield {"type": "unavailable"}
            return
        yield {
            "type": "meta",
            "provider": status.name,
            "provider_label": status.label,
            "model": status.model,
        }
        emitted = False
        async for token in self._stream_one(status, messages, temperature):
            if token:
                emitted = True
                yield {"type": "delta", "text": token}
        if not emitted:
            yield {"type": "unavailable", "detail": "Provider returned no text"}
            return
        yield {"type": "done"}

    async def test_connection(self, provider: str) -> dict[str, Any]:
        configs = self.store.load()["providers"]
        if provider not in configs:
            raise KeyError(provider)
        started = time.perf_counter()
        status = await self._detect_one(provider, configs[provider])
        if status.status not in {"Ready", "Configured"} or not status.model:
            return {
                "success": False,
                "provider": provider,
                "status": status.status,
                "detail": status.detail,
                "model": status.model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        try:
            reply = await self._complete_one(
                provider,
                [{"role": "user", "content": "Reply with exactly: WORKBENCH_OK"}],
                temperature=0.0,
                json_mode=False,
                detected_model=status.model,
            )
            return {
                "success": bool(reply.strip()),
                "provider": provider,
                "status": "Ready",
                "detail": "Chat completion succeeded",
                "model": status.model,
                "reply": reply[:240],
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "capabilities": asdict(status.capabilities),
            }
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
            return {
                "success": False,
                "provider": provider,
                "status": "Error",
                "detail": f"Completion test failed: {type(exc).__name__}",
                "model": status.model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }

    async def acceptance_test(self, provider: str) -> dict[str, Any]:
        """Exercise four real inference paths against one explicitly selected provider."""

        configs = self.store.load()["providers"]
        if provider not in configs:
            raise KeyError(provider)
        suite_started = time.perf_counter()
        status = await self._detect_one(provider, configs[provider])
        if status.status not in {"Ready", "Configured"} or not status.model:
            return {
                "success": False,
                "provider": provider,
                "model": status.model,
                "status": status.status,
                "detail": status.detail,
                "tests": [],
                "latency_ms": round((time.perf_counter() - suite_started) * 1000),
            }

        tests: list[dict[str, Any]] = []

        async def record(name: str, label: str, operation: Any) -> Any:
            started = time.perf_counter()
            try:
                value, detail = await operation()
                tests.append({"name": name, "label": label, "success": True, "detail": detail, "latency_ms": round((time.perf_counter() - started) * 1000)})
                return value
            except Exception as exc:
                # Provider error bodies can contain submitted prompts or key
                # fragments. Only the exception class belongs in diagnostics.
                tests.append({"name": name, "label": label, "success": False, "detail": type(exc).__name__, "latency_ms": round((time.perf_counter() - started) * 1000)})
                return None

        async def exact_text() -> tuple[str, str]:
            reply = await self._complete_one(
                provider,
                [{"role": "user", "content": "Reply with exactly COMSOL_WORKBENCH_OK and no other text."}],
                temperature=0.0,
                json_mode=False,
                detected_model=status.model,
            )
            if reply.strip() != "COMSOL_WORKBENCH_OK":
                raise ValueError(f"expected exact marker; received {reply[:120]!r}")
            return reply, "Exact marker returned"

        async def real_stream() -> tuple[str, str]:
            chunks: list[str] = []
            events = 0
            async for chunk in self._stream_one(
                status,
                [{"role": "user", "content": "Write one short sentence containing the words COMSOL and temperature."}],
                0.0,
            ):
                events += 1
                chunks.append(chunk)
            text = "".join(chunks).strip()
            if events < 1 or not text:
                raise ValueError("stream returned no non-empty content events")
            return text, f"{events} stream event(s), {len(text)} character(s)"

        async def json_schema() -> tuple[dict[str, Any], str]:
            simple_schema = {
                "type": "object",
                "properties": {"status": {"type": "string", "const": "ok"}, "value": {"type": "integer", "const": 7}},
                "required": ["status", "value"],
                "additionalProperties": False,
            }
            reply = await self._complete_one(
                provider,
                [{"role": "user", "content": "Return only this JSON object: {\"status\":\"ok\",\"value\":7}"}],
                temperature=0.0,
                json_mode=True,
                detected_model=status.model,
                json_schema=simple_schema,
            )
            parsed = json.loads(strip_json(reply))
            if not isinstance(parsed, dict) or parsed.get("status") != "ok" or parsed.get("value") != 7:
                raise ValueError("JSON object did not satisfy status:string='ok', value:integer=7")
            return parsed, "JSON parsed and matched the requested field schema"

        async def scientific_plan_schema() -> tuple[ScientificPlan, str]:
            schema = ScientificPlan.model_json_schema()
            reply = await self._complete_one(
                provider,
                [{"role": "system", "content": "Return only a JSON object matching the provided ScientificPlan schema. Do not use markdown."},
                 {"role": "user", "content": f"Convert this simple problem into a ScientificPlan: a 2D stationary aluminum rectangle 10 mm by 5 mm, left boundary 373 K, right boundary 293 K, top and bottom thermally insulated, output the temperature field. Schema: {json.dumps(schema, ensure_ascii=False)}"}],
                temperature=0.0,
                json_mode=True,
                detected_model=status.model,
                json_schema=schema,
            )
            plan = ScientificPlan.model_validate(json.loads(strip_json(reply)))
            return plan, f"ScientificPlan {plan.schema_version} validated (plan_id={plan.plan_id[:8]})"

        await record("exact_text", "Exact text", exact_text)
        await record("streaming", "Real streaming", real_stream)
        await record("json_schema", "JSON schema", json_schema)
        await record("scientific_plan", "ScientificPlan schema", scientific_plan_schema)
        success = len(tests) == 4 and all(item["success"] for item in tests)
        return {
            "success": success,
            "provider": provider,
            "model": status.model,
            "status": "Accepted" if success else "Failed",
            "detail": "All four real inference tests passed" if success else f"{sum(item['success'] for item in tests)}/4 real inference tests passed",
            "tests": tests,
            "latency_ms": round((time.perf_counter() - suite_started) * 1000),
        }

    async def _detect_one(self, name: str, config: dict[str, Any]) -> ProviderStatus:
        enabled = bool(config.get("enabled", False))
        base_url = str(config.get("base_url", "")).rstrip("/")
        configured_model = str(config.get("model", ""))
        kind = "local" if name in LOCAL_PROVIDERS else "cloud"
        label = LABELS.get(name, name)
        empty_capability = ModelCapability(location=kind)
        if not enabled:
            return ProviderStatus(name, label, kind, False, "Disabled", "Disabled in settings", base_url, configured_model, capabilities=empty_capability)
        if not base_url:
            return ProviderStatus(name, label, kind, True, "Not configured", "Endpoint is empty", base_url, configured_model, capabilities=empty_capability)
        try:
            provider_endpoint(name, base_url)
        except ValueError as exc:
            return ProviderStatus(name, label, kind, True, "Not configured", str(exc), base_url, configured_model, capabilities=empty_capability)
        if kind == "cloud":
            if not self.store.secret(name, base_url):
                return ProviderStatus(name, label, kind, True, "Not configured", "Enter the API key for this endpoint", base_url, configured_model, capabilities=empty_capability)
            if not configured_model:
                return ProviderStatus(name, label, kind, True, "Not configured", "Select an exact model ID", base_url, configured_model, capabilities=empty_capability)
            # Avoid paid probe traffic during routine environment polling.
            # "Configured" is not evidence of connectivity; Test Connection
            # performs an actual inference against this exact model.
            return ProviderStatus(name, label, kind, True, "Configured", "Configured; live inference not tested", base_url, configured_model, capabilities=empty_capability)
        try:
            timeout = httpx.Timeout(1.2, connect=0.4)
            async with self._client(timeout) as client:
                if name == "ollama":
                    response = await client.get(f"{base_url}/api/tags")
                    response.raise_for_status()
                    model_records = response.json().get("models", [])
                    models = [str(item.get("name", "")) for item in model_records if item.get("name")]
                else:
                    response = await client.get(f"{base_url}/models", headers=self._headers(name, self._key(name, base_url)))
                    response.raise_for_status()
                    model_records = response.json().get("data", [])
                    models = [str(item.get("id", "")) for item in model_records if item.get("id")]
                model_details = tuple(model_detail(item, name) for item in model_records if item.get("name") or item.get("id"))
                resolved_model = configured_model or (models[0] if models else "")
                capability = await self._capability_probe(client, name, base_url, resolved_model, model_records, kind)
            detail = f"{len(models)} model(s) available" if models else "Service reachable; no models listed"
            state = "No Model" if name == "ollama" and not models else "Ready"
            return ProviderStatus(name, label, kind, True, state, detail, base_url, resolved_model, tuple(models), model_details, capability)
        # Client construction can raise OSError before an HTTP request exists
        # (for example, a stale SSL_CERT_FILE in the desktop environment).
        # Optional local discovery must degrade, never take down Runtime.
        except (httpx.HTTPError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            if name == "ollama":
                state = "Installed but Stopped" if ollama_executable() else "Not Installed"
                detail = "Ollama service did not respond" if ollama_executable() else "Ollama executable not detected"
            else:
                state = "Not detected" if kind == "local" else "Error"
                detail = "Service did not respond" if kind == "local" else f"Provider unavailable: {type(exc).__name__}"
            return ProviderStatus(name, label, kind, True, state, detail, base_url, configured_model, capabilities=empty_capability)

    async def _capability_probe(
        self,
        client: httpx.AsyncClient,
        name: str,
        base_url: str,
        model: str,
        records: list[dict[str, Any]],
        kind: str,
    ) -> ModelCapability:
        context_size: int | None = None
        tool_calling: bool | None = None
        structured: bool | None = None
        if name == "ollama" and model:
            try:
                response = await client.post(f"{base_url}/api/show", json={"name": model})
                response.raise_for_status()
                payload = response.json()
                explicit = payload.get("capabilities") or []
                tool_calling = True if "tools" in explicit else None
                structured = True if "completion" in explicit else None
                context_size = find_context_size(payload.get("model_info", {}))
            except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError):
                pass
        else:
            record = next((item for item in records if str(item.get("id", "")) == model), {})
            context_size = find_context_size(record)
            advertised = record.get("capabilities")
            if isinstance(advertised, dict):
                tool_calling = bool(advertised["tool_calling"]) if "tool_calling" in advertised else None
                structured = bool(advertised["structured_output"]) if "structured_output" in advertised else None
        return ModelCapability(
            chat=True,
            streaming=True,
            tool_calling=tool_calling,
            structured_output=structured,
            context_size=context_size,
            location=kind,
        )

    async def _complete_one(
        self,
        name: str,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        json_mode: bool,
        detected_model: str,
        json_schema: dict[str, Any] | None = None,
        allow_json_retry: bool = True,
    ) -> str:
        config = self.store.load()["providers"][name]
        base_url = str(config["base_url"]).rstrip("/")
        key = self._key(name, base_url)
        model = str(config.get("model", "")) or detected_model
        if not model:
            raise ValueError("No model selected")
        timeout = httpx.Timeout(180, connect=5)
        async with self._client(timeout) as client:
            if name in {"claude", "gemini"}:
                return await cloud_protocols.complete(client, name, base_url, model, key, messages, temperature, json_mode)
            if name == "ollama":
                payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": {"temperature": temperature}}
                if json_mode:
                    payload["format"] = json_schema or "json"
                if (trace := active_trace.get()) is not None:
                    payload['options']['num_predict'] = trace.output_limit
                request_started()
                response = await client.post(f"{base_url}/api/chat", json=payload)
                response.raise_for_status()
                response_received(response.json())
                content = response.json()["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Ollama returned no text")
                return content
            payload = {"model": model, "messages": messages, "temperature": temperature}
            if name == "deepseek":
                payload = deepseek_payload(model, messages, temperature, json_mode=json_mode, schema=json_schema)
            elif json_mode:
                payload["response_format"] = (
                    {"type": "json_schema", "json_schema": {"name": "workbench_output", "strict": True, "schema": json_schema}}
                    if json_schema
                    else {"type": "json_object"}
                )
            if (trace := active_trace.get()) is not None:
                payload['max_tokens'] = trace.output_limit
                if name == 'deepseek':
                    # 已有 JSON 截断补救参数前置到专家单次请求，不隐含多发一次。
                    payload['reasoning_effort'] = 'none'
            for attempt in range(2 if name == "deepseek" and json_mode and allow_json_retry else 1):
                request_started()
                response = await client.post(f"{base_url}/chat/completions", headers=self._headers(name, key), json=payload)
                response.raise_for_status()
                response_received(response.json())
                choice = response.json()["choices"][0]
                content = choice.get("message", {}).get("content")
                incomplete = choice.get("finish_reason") == "length" or (
                    choice.get("finish_reason") == "stop" and isinstance(content, str) and not content.strip()
                )
                if name == "deepseek" and json_mode and allow_json_retry and attempt == 0 and incomplete:
                    # Discard the incomplete JSON and retry once with the same
                    # model/endpoint, reserving the token budget for the answer.
                    payload = {**payload, "reasoning_effort": "none"}
                    continue
                break
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("Provider response did not finish normally")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Provider returned no text")
            return content

    async def _stream_one(
        self,
        status: ProviderStatus,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> AsyncIterator[str]:
        timeout = httpx.Timeout(180, connect=5)
        key = self._key(status.name, status.base_url)
        async with self._client(timeout) as client:
            if status.name in {"claude", "gemini"}:
                async for token in cloud_protocols.stream(client, status.name, status.base_url, status.model,
                                                          key, messages, temperature):
                    yield token
                return
            if status.name == "ollama":
                async with client.stream("POST", f"{status.base_url}/api/chat", json={"model": status.model, "messages": messages, "stream": True, "options": {"temperature": temperature}}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            payload = json.loads(line)
                            content = payload.get("message", {}).get("content")
                            if isinstance(content, str):
                                yield content
                return
            payload = (deepseek_payload(status.model, messages, temperature, stream=True) if status.name == "deepseek"
                       else {"model": status.model, "messages": messages, "temperature": temperature, "stream": True})
            async with client.stream("POST", f"{status.base_url}/chat/completions", headers=self._headers(status.name, key), json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    payload = json.loads(data)
                    reason = payload.get("choices", [{}])[0].get("finish_reason")
                    if reason not in (None, "stop"):
                        raise ValueError("Provider stream did not finish normally")
                    content = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if isinstance(content, str):
                        yield content

    def _key(self, name: str, base_url: str) -> str:
        provider_endpoint(name, base_url)
        key = self.store.secret(name, base_url)
        if name not in LOCAL_PROVIDERS and not key:
            raise ValueError("provider_credential_reentry_required")
        return key

    def _headers(self, name: str, key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if name == "openrouter":
            headers["HTTP-Referer"] = "http://127.0.0.1:5173"
            headers["X-Title"] = "COMSOL Scientific Workbench"
        return headers

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, follow_redirects=False)

    @staticmethod
    def _status_kwargs(item: dict[str, Any]) -> dict[str, Any]:
        capability = item.get("capabilities") or {}
        return {
            "name": item["name"],
            "label": item["label"],
            "kind": item["kind"],
            "enabled": item["enabled"],
            "status": item["status"],
            "detail": item["detail"],
            "base_url": item["base_url"],
            "model": item.get("selected_model") or item.get("model", ""),
            "available_models": tuple(item.get("available_models", [])),
            "model_details": tuple(ModelDetail(**detail) for detail in item.get("model_details", [])),
            "capabilities": ModelCapability(**capability),
        }


def find_context_size(payload: dict[str, Any]) -> int | None:
    for key, value in payload.items():
        normalized = str(key).lower()
        if normalized.endswith("context_length") or normalized in {"context_size", "max_model_len", "max_context_length"}:
            try:
                number = int(value)
                if number > 0:
                    return number
            except (TypeError, ValueError):
                continue
    return None


def model_detail(record: dict[str, Any], provider: str) -> ModelDetail:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    raw_size = record.get("size")
    try:
        size_bytes = int(raw_size) if raw_size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    return ModelDetail(
        name=str(record.get("name") or record.get("id") or ""),
        size_bytes=size_bytes,
        size_label=format_bytes(size_bytes),
        parameter_size=str(details.get("parameter_size")) if details.get("parameter_size") else None,
        quantization_level=str(details.get("quantization_level")) if details.get("quantization_level") else None,
        family=str(details.get("family")) if details.get("family") else None,
    )


def format_bytes(value: int | None) -> str:
    if value is None:
        return "Unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "Unknown"


def strip_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    return stripped


provider_manager = ProviderManager()
