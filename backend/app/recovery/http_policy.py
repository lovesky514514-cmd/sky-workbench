"""确定性 HTTP 恢复提案 / Deterministic HTTP recovery recommendations."""
from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import socket
import ssl

import httpx

from app.recovery.http_contracts import CommitState, HttpAction as A, HttpDecision, HttpObservation, OperationKind, TransportFailure as T


GROUPS = {
    A.PASS: (100,101,102,103,200,201,203,204,205,206,207,208,226,304),
    A.POLL: (202,423), A.USER: (300,402,407,426,505,507,511),
    A.REVALIDATE: (301,302,303,307,308,412,416,428),
    A.COMPATIBILITY: (104,305,306,418,510),
    A.FIX: (400,404,405,406,411,413,414,415,417,422,431),
    A.AUTH: (401,), A.STOP: (403,410,451,501,506,508),
    A.RETRY: (408,425), A.READBACK: (409,424,500,502,504),
    A.RECONNECT: (421,), A.COOLDOWN: (429,503),
}
STATUS_ACTIONS = {status: action for action, statuses in GROUPS.items() for status in statuses}
PROVIDER_CODES = frozenset({419,420,444,449,499,520,521,522,523,524,525,526,527,530})
TRANSPORT_ACTIONS = {
    T.DNS_NXDOMAIN: A.FIX, T.DNS_SERVFAIL: A.RETRY, T.REFUSED: A.RECONNECT,
    T.RESET: A.READBACK, T.CERTIFICATE: A.STOP, T.TLS: A.USER,
    T.CONNECT: A.RECONNECT, T.READ: A.READBACK, T.WRITE: A.READBACK,
    T.POOL: A.COOLDOWN, T.PROXY: A.USER, T.STREAM: A.READBACK,
    T.CRASH: A.READBACK, T.BRIDGE: A.READBACK, T.BUSINESS: A.FIX,
}


def parse_retry_after(value: str | None, *, now: datetime | None = None):
    """解析有界等待秒数 / Parse a bounded delay in seconds."""
    if value is None:
        return None, False
    if not isinstance(value, str) or len(value) > 128 or not value.strip():
        return None, True
    value = value.strip()
    if value.isascii() and value.isdecimal():
        return float(min(int(value), 31536000)), False
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None, True
        return min(31536000.0, max(0.0, (date - (now or datetime.now(UTC))).total_seconds())), False
    except (ValueError, OverflowError, TypeError):
        return None, True


def transport_failure(error: BaseException) -> T | None:
    # 按异常类型分类 / Classify by exception type, independent of log text.
    if isinstance(error, httpx.ProxyError):
        return T.PROXY
    for cls, code in ((httpx.PoolTimeout,T.POOL), (httpx.ConnectTimeout,T.CONNECT),
                      (httpx.ReadTimeout,T.READ), (httpx.WriteTimeout,T.WRITE)):
        if isinstance(error, cls):
            return code
    current, seen = error, set()
    while current and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return T.CERTIFICATE
        if isinstance(current, ssl.SSLError):
            return T.TLS
        if isinstance(current, socket.gaierror):
            return T.DNS_SERVFAIL if current.errno == socket.EAI_AGAIN else T.DNS_NXDOMAIN
        if isinstance(current, ConnectionRefusedError):
            return T.REFUSED
        if isinstance(current, ConnectionResetError):
            return T.RESET
        current = current.__cause__ or current.__context__
    return T.BRIDGE if isinstance(error, httpx.TransportError) else None


def decide(observation: HttpObservation) -> HttpDecision:
    o = observation
    side_effect = o.operation_kind != OperationKind.READ
    is_run = o.operation_kind == OperationKind.RUN
    evidence = ('current_operation_identity', 'current_plan_attempt_owner', 'independent_readback')

    def result(action, reason, *, retry=False, readback=False):
        # 只读操作才可重试 / Retries are limited to read-only operations.
        return HttpDecision(action=action, reason=reason, safe_to_retry=retry and not side_effect,
                            readback_required=readback, requires_run_approval=is_run,
                            retry_after_seconds=o.retry_after_seconds, evidence_required=evidence)

    if not o.response_valid:
        return result(A.READBACK if side_effect else A.STOP, 'invalid_response', readback=side_effect)
    if o.business_success is False or o.transport == T.BUSINESS:
        return result(A.FIX, 'business_failed')
    if o.transport in {T.CERTIFICATE,T.TLS,T.PROXY,T.DNS_NXDOMAIN}:
        return result(TRANSPORT_ACTIONS[o.transport], 'transport_permanent')
    if o.status in (401,403,407):
        if o.status == 401:
            action = A.AUTH if o.refresh_supported and not o.refresh_used and not side_effect else A.USER
            return result(action, 'authentication')
        return result(A.STOP if o.status == 403 else A.USER, 'permission_denied')
    if o.status is not None and o.status < 400 and o.status != 202 and o.transport is None:
        if o.status == 304 and not o.cache_valid:
            return result(A.REVALIDATE, 'cache_missing')
        action = STATUS_ACTIONS.get(o.status, A.STOP)
        return result(action, 'interim_response' if o.status < 200 else
                      'normal_response' if action == A.PASS else 'unknown_status')
    if side_effect and (o.transport is not None or o.status in {202,408,409,424,429,500,502,503,504,520,524}):
        if o.commit_state in {CommitState.RUNNING,CommitState.COMMITTED}:
            return result(A.POLL, 'already_submitted', readback=True)
        return result(A.READBACK, 'submission_unknown', readback=True)
    if o.transport is not None:
        action = TRANSPORT_ACTIONS[o.transport]
        # 已知只读端点的断线可以重建连接；持久预算层仍需核对权限。
        if action == A.READBACK:
            action = A.RECONNECT if not side_effect else A.READBACK
        return result(action, 'transport_transient', retry=action in {A.RETRY,A.RECONNECT,A.COOLDOWN},
                      readback=action == A.READBACK)
    if o.status in PROVIDER_CODES:
        if o.provider == 'cloudflare' and o.status in {520,521,522,523,524,525,526,530}:
            action = A.USER if o.status in {525,526} else A.READBACK
            return result(action, 'provider_specific', readback=action == A.READBACK)
        return result(A.COMPATIBILITY, 'provider_contract_required')
    action = STATUS_ACTIONS.get(o.status, A.STOP)
    if o.status == 202:
        return result(A.POLL, 'accepted_not_finished', readback=True)
    if o.status in {409,412,424,428}:
        return result(action, 'version_conflict', readback=True)
    if o.status in {500,502,504} and not side_effect:
        return result(A.RETRY, 'service_transient', retry=True)
    if action in {A.RETRY,A.RECONNECT,A.COOLDOWN}:
        return result(action, 'service_transient', retry=True)
    return result(action, 'request_invalid' if action == A.FIX else
                  'unsupported' if o.status in STATUS_ACTIONS else 'unknown_status')
