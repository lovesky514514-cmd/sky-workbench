"""只读 HTTP 恢复执行器。每次发送都核对现有计划、尝试和资源所有权。"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
import random

import httpx

from app.recovery.http_contracts import CommitState, HttpObservation, OperationKind, bridge_operation
from app.recovery.http_policy import decide, parse_retry_after, transport_failure
from app.recovery.ingest import digest


HTTP_RECOVERY: ContextVar['HttpRetrySession | None'] = ContextVar('sky_http_recovery', default=None)


class HttpRetrySession:
    def __init__(self, store, scope, operation_id, *, case_id=None, maximum=3, timeout=12,
                 sleep=asyncio.sleep, jitter=random.uniform):
        self.store, self.scope, self.operation_id = store, scope, operation_id
        self.case_id, self.maximum, self.timeout = case_id, maximum, timeout
        self.sleep, self.jitter = sleep, jitter

    async def request(self, method, route, send, *, fingerprint=None):
        if bridge_operation(method, route) != OperationKind.READ:
            raise PermissionError('http_recovery_read_only_endpoint_required')
        row = self.store.open_http_operation(self.operation_id, self.scope, fingerprint or digest([method,route]),
                                            case_id=self.case_id, maximum=self.maximum, timeout=self.timeout)
        remaining = (datetime.fromisoformat(row['deadline']) - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise TimeoutError('http_deadline_exceeded')
        try:
            async with asyncio.timeout(remaining):
                while True:
                    attempt = self.store.claim_http_send(self.operation_id, self.scope)
                    response, error = None, None
                    try:
                        response = await send()
                        seconds, invalid = parse_retry_after(response.headers.get('Retry-After'))
                        observation = HttpObservation(status=response.status_code, operation_kind=OperationKind.READ,
                                                      commit_state=CommitState.NOT_SUBMITTED,
                                                      retry_after_seconds=seconds, retry_after_invalid=invalid)
                    except httpx.TransportError as exc:
                        error = exc
                        observation = HttpObservation(transport=transport_failure(exc), operation_kind=OperationKind.READ,
                                                      commit_state=CommitState.NOT_SUBMITTED)
                    decision = decide(observation)
                    delay = max(observation.retry_after_seconds or 0, self.jitter(0, min(2, 0.2 * 2**(attempt-1))))
                    retry = self.store.record_http_attempt(self.operation_id, self.scope, observation, decision,
                                                           retry=decision.safe_to_retry, delay=delay)
                    if not retry:
                        if error:
                            raise error
                        return response
                    if response:
                        await response.aclose()
                    left = (datetime.fromisoformat(row['deadline']) - datetime.now(UTC)).total_seconds()
                    if delay >= left:
                        raise TimeoutError('http_cooldown_exceeds_deadline')
                    await self.sleep(delay)
        except BaseException as exc:
            # 即使上层取消也关闭在途预算；崩溃遗留 SENDING 则保持拒绝重放。
            self.store.stop_http_operation(self.operation_id, self.scope, cancelled=isinstance(exc, asyncio.CancelledError))
            raise
