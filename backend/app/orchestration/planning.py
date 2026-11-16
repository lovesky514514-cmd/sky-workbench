"""计划提案与发布分离；提案调用复用 Provider 并占用总控的剩余预算。"""
from dataclasses import dataclass
import asyncio
import hashlib
import json
from typing import Any

from app.redaction import redact_object
from app.scientific.schemas import ScientificPlan


@dataclass(frozen=True)
class PlanDraft:
    plan: ScientificPlan
    expected_revision: int
    provider: str
    model: str
    citations: list[dict[str, Any]]
    timeline_id: str
    started: float


class PlanningProvider:
    def __init__(self, provider, studies, scope, limits):
        self.provider, self.studies, self.scope, self.limits = provider, studies, scope, limits
        self.calls = 0

    async def complete(self, messages, **options):
        self.studies.guard(self.scope)
        self.calls += 1
        if self.calls > self.limits.model_calls:
            raise ValueError('planning_repair_requires_additional_controller_budget')
        messages = redact_object(messages)
        schema = options.get('json_schema') or {}
        reserved = len(json.dumps(messages, ensure_ascii=False).encode()) + len(json.dumps(schema).encode()) + 4096 + self.limits.output_tokens
        call_id = self.studies.reserve(self.scope, 'plan-proposal-' + str(self.calls), reserved, self.limits)
        trace = None
        def observe(value):
            nonlocal trace
            trace = value
        result, state = None, 'FAILED'
        receipt = {'capability': 'research_planning', 'request_snapshot': messages, 'output_schema': schema,
            'prompt_sha256': hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
            'reserved_tokens_estimate': reserved}
        try:
            result, trace = await self.provider.complete_metered(messages, json_schema=schema,
                max_output_tokens=self.limits.output_tokens, observe=observe)
            self.studies.guard(self.scope)
            state = 'COMPLETED' if result else 'UNAVAILABLE'
            if result:
                receipt.update(provider=result.provider, model=result.model,
                    response_sha256=hashlib.sha256(result.text.encode()).hexdigest())
            if trace['requests'] > 1 or (trace['usage_known'] and trace['input_tokens'] + trace['output_tokens'] > reserved):
                state = 'BUDGET_EXCEEDED'
                raise ValueError('planning_provider_exceeded_reserved_budget')
            return result
        except asyncio.CancelledError:
            state = 'INTERRUPTED'
            raise
        finally:
            self.studies.receipt(call_id, state, {**receipt, **(trace or {'requests': None, 'usage_known': False}),
                'result_origin': 'provider_response' if result else 'no_accepted_provider_response'})
