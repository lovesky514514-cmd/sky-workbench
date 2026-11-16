"""Async localhost client for the minimal COMSOL Java Bridge contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import asyncio
from pathlib import Path
from typing import Any, Callable, Literal
import os
import re
from uuid import uuid4

import httpx

from app.recovery.ingest import BridgeAuthenticationUnavailable, bridge_signal, digest
from app.recovery.http_execution import HTTP_RECOVERY

from app.config import settings_store
from app.comsol.bridge_auth import BRIDGE_TOKEN_HEADER, valid_bridge_token
from app.execution.files import reject_reparse
from app.execution.scientific_transfer import CONTROL_BYTES, decode_object, load_transfer


BridgeEventType = Literal["command", "model_change", "progress", "critical_failure"]


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    sequence: int
    event_type: BridgeEventType
    phase: str
    status: str
    detail: str
    command_success: bool
    model_state_verified: bool
    scientific_validation_passed: bool
    created_at: str | None = None
    target: str | None = None
    action: str | None = None
    verified: bool | None = None
    old_value: str | None = None
    new_value: str | None = None
    detection_method: str | None = None
    progress: float | None = None
    percent: int | None = None
    message: str | None = None
    progress_source: str | None = None
    duration_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class JavaBridgeClient:
    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 token_provider: Callable[[], str | None] | None = None) -> None:
        self.base_url = base_url
        self.transport = transport
        self.token_provider = token_provider or _owned_bridge_token

    def configured_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        desktop_url = os.getenv("COMSOL_WORKBENCH_JAVA_BRIDGE_URL", "").strip()
        if desktop_url:
            return desktop_url.rstrip("/")
        config = settings_store.load().get("comsol", {}).get("java_bridge", {})
        return str(config.get("base_url", "http://127.0.0.1:8765")).rstrip("/")

    async def health(self) -> dict[str, Any]:
        try:
            payload = await self._request("GET", "/health")
            return {
                "status": "Ready" if payload.get("connected") else "Unavailable",
                "bridge": str(payload.get("bridge", "")),
                "bridge_running": bool(payload.get("bridge_running")),
                "comsol_api_loaded": bool(payload.get("comsol_api_loaded")),
                "model_connected": bool(payload.get("model_connected", payload.get("connected"))),
                "connected": bool(payload.get("connected")),
                "real_comsol": bool(payload.get("real_comsol")),
                "comsol_version": str(payload.get("comsol_version", "")),
                "model_tag": str(payload.get("model_tag", "")),
                "bridge_mode": str(payload.get("bridge_mode", "")),
                "connection_mode": str(payload.get("connection_mode", "")),
                "detection_method": str(payload.get("detection_method", "unavailable")),
                "poll_interval_ms": int(payload.get("poll_interval_ms", 0)),
                "detail": str(payload.get("detail", "No bridge detail returned")),
                "base_url": self.configured_url(),
                "command_success": bool(payload.get("command_success")),
                "model_state_verified": bool(payload.get("model_state_verified")),
                "scientific_validation_passed": bool(payload.get("scientific_validation_passed")),
                "model_tags": payload.get("model_tags", []),
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return {
                "status": "Unavailable",
                "bridge_running": False,
                "comsol_api_loaded": False,
                "model_connected": False,
                "connected": False,
                "real_comsol": False,
                "detail": "Java Bridge unavailable; inspect the scoped diagnostic record.",
                "base_url": self.configured_url(),
                "command_success": False,
                "model_state_verified": False,
                "scientific_validation_passed": False,
                "model_tags": [],
                "comsol_version": "",
                "model_tag": "",
                "bridge_mode": "",
                "connection_mode": "",
                "detection_method": "unavailable",
                "poll_interval_ms": 0,
            }

    async def model_tree(self, model_tag: str | None = None) -> dict[str, Any]:
        params = {"model_tag": model_tag} if model_tag else None
        return await self._request("GET", "/model/tree", params=params)

    async def visualization(self, request_id: str, *, staging_name: str, heat_result=False) -> dict[str, Any]:
        params = {"request_id": request_id, "staging": staging_name}
        if heat_result:
            params["field"] = "saved_heat"
        return await self._request("GET", "/model/visualization", params=params,
                                   request_timeout=httpx.Timeout(45.0, connect=0.75))

    async def test_feature(self, payload: dict[str, str]) -> dict[str, Any]:
        return await self._request("POST", "/commands/test-feature", json=payload, retries=0)

    async def parameters(self) -> dict[str, Any]:
        return await self._request("GET", "/model/parameters")

    async def set_parameter(self, name: str, value: str) -> dict[str, Any]:
        return await self._request("POST", "/model/parameter", json={"name": name, "value": value}, retries=0)

    async def boundary_feature(self, *, component_tag: str, physics_tag: str,
                               feature_tag: str, property_key: str) -> dict[str, Any]:
        return await self._request('GET', '/model/boundary-feature', params={
            'component_tag': component_tag, 'physics_tag': physics_tag,
            'feature_tag': feature_tag, 'property_key': property_key,
        }, retries=0)

    async def heat_core(self) -> dict[str, Any]:
        return await self._request('GET', '/model/heat-core', retries=0)

    async def set_boundary_feature(self, *, component_tag: str, physics_tag: str,
                                   feature_tag: str, property_key: str,
                                   expected_selection: int, expected_expression: str,
                                   proposed_expression: str) -> dict[str, Any]:
        # No automatic retry: a COMSOL mutation is not an idempotent transport read.
        return await self._request('POST', '/model/boundary-feature', json={
            'component_tag': component_tag, 'physics_tag': physics_tag,
            'feature_tag': feature_tag, 'property_key': property_key,
            'expected_selection': expected_selection,
            'expected_expression': expected_expression,
            'proposed_expression': proposed_expression,
        }, retries=0)

    async def remove_parameter(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", "/model/parameter", params={"name": name}, retries=0)

    async def run_study(self, study_tag: str = "std1") -> dict[str, Any]:
        return await self._request(
            "POST",
            "/model/study/run",
            json={"study_tag": study_tag},
            retries=0,
            request_timeout=httpx.Timeout(600.0, connect=2.0),
        )

    async def heat_field(self, run_id: str, attempt_id: str, attempt_root: Path,
                         check_current: Callable[[], None]) -> dict[str, Any]:
        if (not all(re.fullmatch(r'[A-Za-z0-9-]{1,100}', value) for value in (run_id, attempt_id))
                or attempt_root.parts[-4:] != ('models', 'attempts', run_id, attempt_id)):
            raise PermissionError('bridge_field_scope_invalid')
        check_current()
        request_id = uuid4().hex
        root = attempt_root / 'bridge-readbacks' / request_id
        reject_reparse(root)
        root.mkdir(parents=True, exist_ok=False)
        payload = await self._request(
            'GET', '/model/heat-field', retries=0,
            params={'run_id': run_id, 'attempt_id': attempt_id, 'request_id': request_id},
            request_timeout=httpx.Timeout(120.0, connect=2.0),
        )
        check_current()
        if payload.get('request_id') != request_id or 'data' not in payload:
            raise ValueError('bridge_field_receipt_mismatch')
        value = await asyncio.to_thread(load_transfer, root, payload['data'], run_id=run_id,
                                        attempt_id=attempt_id, producer='java_bridge', data_type='bridge-heat-field.v1')
        check_current()
        if payload.get('sample_count') != len(value['x']) or any(name in payload for name in value):
            raise ValueError('bridge_field_receipt_shape_mismatch')
        return {**payload, **value}

    async def export_corrected_heat(self, run_id: str, attempt_id: str) -> dict[str, Any]:
        try:
            return await self._request(
                'POST', '/model/heat-export', json={'run_id': run_id, 'attempt_id': attempt_id},
                retries=0, request_timeout=httpx.Timeout(180.0, connect=2.0),
            )
        except httpx.HTTPStatusError as exc:
            try:
                detail = str(exc.response.json().get('detail', 'COMSOL export unavailable'))
            except (ValueError, AttributeError):
                detail = 'COMSOL export unavailable'
            raise RuntimeError(f'Corrected COMSOL Heat export failed: {detail}') from exc

    async def events(self, after: int = 0) -> dict[str, Any]:
        payload = await self._request("GET", "/events", params={"after": max(0, after)})
        normalized: list[dict[str, Any]] = []
        for raw in payload.get("events", []):
            normalized.append(
                BridgeEvent(
                    sequence=int(raw.get("sequence", 0)),
                    event_type=_event_type(raw.get("event_type")),
                    phase=str(raw.get("phase", "unknown")),
                    status=str(raw.get("status", "unknown")),
                    detail=str(raw.get("detail", "")),
                    command_success=bool(raw.get("command_success")),
                    model_state_verified=bool(raw.get("model_state_verified")),
                    scientific_validation_passed=bool(raw.get("scientific_validation_passed")),
                    created_at=str(raw.get("created_at")) if raw.get("created_at") else None,
                    target=str(raw.get("target")) if raw.get("target") is not None else None,
                    action=str(raw.get("action")) if raw.get("action") is not None else None,
                    verified=bool(raw.get("verified")) if raw.get("verified") is not None else None,
                    old_value=str(raw.get("old_value")) if raw.get("old_value") is not None else None,
                    new_value=str(raw.get("new_value")) if raw.get("new_value") is not None else None,
                    detection_method=str(raw.get("detection_method")) if raw.get("detection_method") is not None else None,
                    progress=float(raw.get("progress")) if raw.get("progress") is not None else None,
                    percent=int(raw.get("percent")) if raw.get("percent") is not None else None,
                    message=str(raw.get("message")) if raw.get("message") is not None else None,
                    progress_source=str(raw.get("progress_source")) if raw.get("progress_source") is not None else None,
                    duration_ms=int(raw.get("duration_ms")) if raw.get("duration_ms") is not None else None,
                ).as_dict()
            )
        return {"latest_sequence": int(payload.get("latest_sequence", after)), "events": normalized}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retries: int = 0,
        request_timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # No token means no network request: loopback and a random port are not authentication.
        token = self.token_provider()
        if not valid_bridge_token(token):
            error = BridgeAuthenticationUnavailable("Java Bridge authentication unavailable")
            error._sky_failure_signal = bridge_signal(method, path, error)
            raise error
        headers = httpx.Headers(kwargs.pop("headers", None))
        headers[BRIDGE_TOKEN_HEADER] = token
        headers['Accept-Encoding'] = 'identity'
        timeout = request_timeout or httpx.Timeout(3.0, connect=0.75)
        base_url = self.configured_url()
        # retries 参数保留调用兼容；真正重试只由持久化的只读恢复上下文控制。
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport, trust_env=False,
                                         follow_redirects=False) as client:
                async def send():
                    request = client.build_request(method, f"{base_url}{path}", headers=headers, **kwargs)
                    limit = (CONTROL_BYTES if path in {'/model/heat-field', '/model/visualization'} else
                             65_536 if HTTP_RECOVERY.get() is not None else 4_000_000)
                    response = await client.send(request, stream=True)
                    data = bytearray()
                    try:
                        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                            raise ValueError('Bridge compressed response is not supported')
                        declared = response.headers.get('Content-Length')
                        if declared is not None and (not declared.isdecimal() or int(declared) > limit):
                            raise ValueError('Bridge response exceeded its limit')
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if len(data) + len(chunk) > limit:
                                raise ValueError('Bridge response exceeded its limit')
                            data.extend(chunk)
                        return httpx.Response(response.status_code, headers=response.headers, content=bytes(data), request=request)
                    finally:
                        await response.aclose()

                recovery = HTTP_RECOVERY.get()
                fingerprint = digest([method,path,base_url,kwargs.get('params')])
                response = await recovery.request(method, path, send, fingerprint=fingerprint) if recovery else await send()
                response.raise_for_status()
                return decode_object(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            exc._sky_failure_signal = bridge_signal(method, path, exc)
            raise


def _event_type(value: Any) -> BridgeEventType:
    normalized = str(value)
    if normalized in {"command", "model_change", "progress", "critical_failure"}:
        return normalized  # type: ignore[return-value]
    return "critical_failure"


def _owned_bridge_token() -> str | None:
    # Import lazily: the process manager is the sole owner of this in-memory secret.
    from app.comsol.bridge_process import java_bridge_process_manager

    return java_bridge_process_manager.request_token()


java_bridge_client = JavaBridgeClient()
