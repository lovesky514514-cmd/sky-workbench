"""HTTP 诊断只保留结构化事实；不接受 URL、原始头部或执行代码。"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.execution.expert_contracts import Contract


class OperationKind(StrEnum):
    READ = 'READ_ONLY'
    IDEMPOTENT = 'IDEMPOTENT_WRITE'
    WRITE = 'SIDE_EFFECT_WRITE'
    RUN = 'RUN'
    UNKNOWN = 'UNKNOWN'


class CommitState(StrEnum):
    NOT_SUBMITTED = 'NOT_SUBMITTED'
    RUNNING = 'RUNNING'
    COMMITTED = 'COMMITTED'
    FAILED = 'FAILED'
    UNKNOWN = 'UNKNOWN_COMMIT'


class HttpAction(StrEnum):
    PASS = 'PASS_THROUGH'
    POLL = 'WAIT_AND_POLL'
    RETRY = 'RETRY_SAFE'
    AUTH = 'REFRESH_AUTH_ONCE'
    REVALIDATE = 'REVALIDATE'
    FIX = 'FIX_REQUEST'
    RECONNECT = 'RECONNECT'
    COOLDOWN = 'COOLDOWN'
    READBACK = 'READBACK_FIRST'
    USER = 'REQUIRE_USER'
    STOP = 'HARD_STOP'
    COMPATIBILITY = 'COMPATIBILITY_ONLY'


class TransportFailure(StrEnum):
    DNS_NXDOMAIN = 'dns_nxdomain'
    DNS_SERVFAIL = 'dns_servfail'
    REFUSED = 'tcp_connection_refused'
    RESET = 'tcp_connection_reset'
    CERTIFICATE = 'tls_certificate_invalid'
    TLS = 'tls_handshake_failed'
    CONNECT = 'connect_timeout'
    READ = 'read_timeout'
    WRITE = 'write_timeout'
    POOL = 'pool_timeout'
    PROXY = 'proxy_auth_failed'
    STREAM = 'sse_truncated'
    CRASH = 'runtime_crash'
    BRIDGE = 'bridge_disconnect'
    BUSINESS = 'business_error_in_200'


class HttpObservation(Contract):
    status: int | None = Field(default=None, strict=True, ge=100, le=599)
    transport: TransportFailure | None = None
    operation_kind: OperationKind = OperationKind.UNKNOWN
    commit_state: CommitState = CommitState.UNKNOWN
    provider: Literal['generic', 'cloudflare', 'nginx', 'contract_unknown'] = 'generic'
    retry_after_seconds: float | None = Field(default=None, ge=0, le=31536000, allow_inf_nan=False)
    retry_after_invalid: bool = False
    response_valid: bool = True
    business_success: bool | None = None
    cache_valid: bool = False
    refresh_supported: bool = False
    refresh_used: bool = False


class HttpDecision(Contract):
    action: HttpAction
    reason: Literal['normal_response', 'interim_response', 'accepted_not_finished', 'cache_missing',
                    'invalid_response', 'business_failed', 'version_conflict', 'authentication',
                    'permission_denied', 'request_invalid', 'service_transient', 'transport_transient',
                    'transport_permanent', 'submission_unknown', 'already_submitted', 'unsupported',
                    'provider_contract_required', 'provider_specific', 'unknown_status']
    safe_to_retry: bool = False
    readback_required: bool = False
    requires_run_approval: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_required: tuple[str, ...] = ()


# 端点契约由宿主代码维护，GET 只是必要条件，不能据此获得权限。
BRIDGE_READ_ENDPOINTS = frozenset({'/health', '/events', '/model/tree', '/model/parameters', '/model/heat-core'})


def bridge_operation(method: str, route: str) -> OperationKind:
    if method in {'GET', 'HEAD'} and route in BRIDGE_READ_ENDPOINTS:
        return OperationKind.READ
    if method == 'POST' and route == '/model/study/run':
        return OperationKind.RUN
    if route in {'/model/parameter', '/model/boundary-feature', '/model/heat-field',
                 '/model/heat-export', '/commands/test-feature'}:
        return OperationKind.WRITE
    return OperationKind.UNKNOWN
