"""将异常转换成固定字段；外部错误正文只做精确识别，不进入记录。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
import sqlite3
from uuid import uuid4

import httpx

from app.recovery.classifier import COMPLETE, CONNECT, PLAN, PREVIEW, RUN
from app.recovery.schemas import Cause, Facts, FailureEvent, FailureScope, Origin
from app.recovery.http_contracts import CommitState, HttpObservation, OperationKind, TransportFailure, bridge_operation
from app.recovery.http_policy import parse_retry_after, transport_failure

_API_DETAILS = {
    (PLAN, 'Plan changed; reload the current revision before saving'): 'plan_revision',
    (COMPLETE, 'Proposal changed; reload and review the current draft'): 'review_digest',
    (COMPLETE, 'Reviewed values changed; reload the current proposal'): 'review_digest',
    (COMPLETE, 'Create a plan before accepting suggestions'): 'plan_precondition',
    (COMPLETE, 'Wait for the active run before revising its plan'): 'plan_precondition',
    (PREVIEW, 'Another COMSOL model currently owns the Bridge'): 'owner_busy',
    (CONNECT, 'Select a writable project first'): 'writable_project_required',
    (CONNECT, 'Connector handshake did not pass; review its local installation'): 'connector_handshake',
}
_BRIDGE_ROUTES = frozenset({RUN, '/health', '/events', '/model/tree', '/model/parameters',
                            '/model/parameter', '/model/boundary-feature', '/model/heat-core',
                            '/model/heat-field', '/model/heat-export', '/model/test-feature'})
_BRIDGE_DETAILS = {
    ('/model/heat-core', 'Core Heat geometry, mesh, study or solver readback incomplete'): 'heat_core_incomplete',
    ('/model/heat-field', 'Numerical field arrays are missing or misaligned'): 'field_array',
    ('/model/heat-export', 'Corrected artifacts already exist; overwrite refused'): 'export_exists',
}
_CODES = frozenset(c.value for c in Cause) | frozenset(t.value for t in TransportFailure) | frozenset(_API_DETAILS.values()) | frozenset(
    _BRIDGE_DETAILS.values()) | {
        'unclassified', 'plan_revision', 'bridge_auth', 'read_timeout', 'connect_timeout', 'disconnected',
        'adapter_unavailable', 'validation_failed', 'review_unavailable', 'boundary_review_unavailable',
        'boundary_correction_unavailable', 'worker_failed', 'runtime_failed', 'bridge_http_error',
    }
_API_ROUTE = re.compile(r'/api(?:/[a-z][a-z0-9-]*|/\{[a-z][a-z0-9_]*\}){1,8}')
_OPS = {'api_request', 'bridge_request', 'scientific_run', 'expert_dispatch', 'tool_execution',
        'science_validation', 'environment_probe', 'readback'}
_MAX_BODY = 8192


@dataclass(frozen=True, slots=True)
class Signal:
    origin: Origin
    operation: str
    error_code: str = 'unclassified'
    method: str | None = None
    route: str | None = None
    status: int | None = None
    facts: Facts = field(default_factory=Facts)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BridgeAuthenticationUnavailable(ValueError):
    pass


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return sha256(canonical(value).encode('utf-8')).hexdigest()


def build_event(signal: Signal, scope: FailureScope, *, correlation: str | None = None,
                parent: str | None = None, occurred_at: datetime | None = None) -> FailureEvent:
    now = datetime.now(UTC)
    route = signal.route
    if signal.origin in {Origin.API, Origin.SCIENCE}:
        route = route if route and _API_ROUTE.fullmatch(route) else None
    elif signal.origin == Origin.BRIDGE:
        route = route if route in _BRIDGE_ROUTES else None
    else:
        route = None
    code = signal.error_code if signal.error_code in _CODES else 'unclassified'
    operation = signal.operation if signal.operation in _OPS else 'readback'
    method = signal.method if signal.method in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'} else None
    summary = f'{signal.origin.value} · {code}'
    facts = signal.facts
    if facts.http is None and code in TransportFailure._value2member_map_:
        facts = facts.model_copy(update={'http': HttpObservation(transport=TransportFailure(code))})
    return FailureEvent(schema_version=1, event_id=signal.event_id, occurred_at_utc=occurred_at or signal.occurred_at,
                        ingested_at_utc=now, origin=signal.origin, operation=operation,
                        method=method, route_template=route,
                        http_status=signal.status if signal.status is not None and 400 <= signal.status <= 599 else None,
                        error_code=code, safe_summary=summary, scope=scope, facts=facts,
                        correlation_id=correlation or signal.event_id, parent_event_id=parent,
                        request_fingerprint=digest([signal.origin, method, route, signal.status, code]),
                        severity='ERROR' if signal.status is None or signal.status >= 500 else 'WARNING')


def api_signal(method: str, route: str | None, status: int, detail: object,
               facts: Facts | None = None, headers=None) -> Signal:
    if type(detail) is str and len(detail) <= 320:
        code = _API_DETAILS.get((route, detail), 'unclassified')
    elif type(detail) is dict and type(detail.get('code')) is str and detail['code'] in _CODES:
        code = detail['code']
    else:
        code = 'unclassified'
    if facts is None:
        facts = Facts(project_writable=False if code == 'writable_project_required' else None,
                      owner_matches=False if code == 'owner_busy' else None)
    seconds, invalid = parse_retry_after((headers or {}).get('Retry-After'))
    reads = {'/api/health', '/api/comsol/bridge/status', '/api/comsol/bridge/model-tree',
             '/api/comsol/bridge/parameters', '/api/engine-tasks/{run_id}'}
    kind = OperationKind.READ if method in {'GET','HEAD'} and route in reads else OperationKind.UNKNOWN
    facts = facts.model_copy(update={'http': HttpObservation(status=status, operation_kind=kind,
                                                           retry_after_seconds=seconds, retry_after_invalid=invalid)})
    return Signal(Origin.API, 'api_request', code, method, route, status, facts)


def bridge_signal(method: str, route: str, error: Exception) -> Signal:
    status = None
    code = 'bridge_http_error'
    facts = Facts()
    seconds, invalid = None, False
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        seconds, invalid = parse_retry_after(error.response.headers.get('Retry-After'))
        try:
            raw = error.response.content
            payload = json.loads(raw) if len(raw) <= _MAX_BODY else {}
        except (ValueError, RecursionError, httpx.ResponseNotRead):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        detail = payload.get('detail')
        if type(detail) is str and len(detail) <= 320:
            code = _BRIDGE_DETAILS.get((route, detail), code)
        if status in (401, 403):
            code = 'bridge_auth'
        # 该字段仅表示命令返回，不是科学验证，也不证明异常发生在提交前。
        success, verified = payload.get('command_success'), payload.get('model_state_verified')
        facts = Facts(phase='readback' if success is True else 'unknown',
                      command_success=success if type(success) is bool else None,
                      model_state_verified=verified if type(verified) is bool else None)
    elif isinstance(error, (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError)):
        code = 'connect_timeout' if isinstance(error, httpx.TimeoutException) else 'disconnected'
        facts = Facts(phase='before_submit')
    elif isinstance(error, httpx.TimeoutException):
        code, facts = 'read_timeout', Facts(phase='after_send')
    elif isinstance(error, httpx.TransportError):
        code, facts = 'disconnected', Facts(phase='unknown')
    elif isinstance(error, BridgeAuthenticationUnavailable):
        code, facts = 'bridge_auth', Facts(phase='before_submit')
    elif isinstance(error, ValueError):
        facts = Facts(phase='after_send')
    kind = bridge_operation(method, route)
    observation = HttpObservation(status=status, transport=transport_failure(error), operation_kind=kind,
                                  commit_state=CommitState.NOT_SUBMITTED if facts.phase == 'before_submit' else CommitState.UNKNOWN,
                                  response_valid=not isinstance(error, ValueError) or isinstance(error, BridgeAuthenticationUnavailable),
                                  retry_after_seconds=seconds, retry_after_invalid=invalid)
    facts = facts.model_copy(update={'http': observation})
    return Signal(Origin.BRIDGE, 'bridge_request', code, method, route, status, facts)


def attached_signals(error: BaseException):
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        signal = getattr(error, '_sky_failure_signal', None)
        if isinstance(signal, Signal):
            yield signal
        error = error.__cause__


def requires_commit_readback(error: BaseException) -> bool:
    """下层已发出的 RUN 不能被上层通用返工循环再次提交。"""
    return any(signal.origin == Origin.BRIDGE and signal.method == 'POST' and signal.route == RUN
               and signal.facts.phase != 'before_submit' for signal in attached_signals(error))


def capture(store, signal: Signal, *, session_id=None, project_id=None, plan_id=None, plan_revision=None,
            run_id=None, attempt_id=None, expert_task_id=None, expert_attempt_id=None,
            correlation=None, parent=None):
    """旁路错误采集不改变原调用结果，也不保存失败的采集内容。"""
    try:
        scope = store.recovery_scope(session_id=session_id, project_id=project_id, plan_id=plan_id,
                                     plan_revision=plan_revision, run_id=run_id, attempt_id=attempt_id,
                                     expert_task_id=expert_task_id, expert_attempt_id=expert_attempt_id)
        return store.ingest_recovery(build_event(signal, scope, correlation=correlation, parent=parent))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        # 调用者可从固定计数看到旁路降级，不把原始异常和路径记入诊断日志。
        store.note_recovery_drop()
        return None
