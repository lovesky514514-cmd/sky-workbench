"""单次真实请求的计量上下文；不读取或记录认证头。"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompletionTrace:
    output_limit: int
    requests: int = 0
    responses: list[dict[str, Any]] = field(default_factory=list)

    def started(self):
        self.requests += 1

    def received(self, payload):
        usage = payload.get('usage') or payload.get('usageMetadata') or {}
        prompt = usage.get('prompt_tokens', usage.get('input_tokens', usage.get('promptTokenCount', payload.get('prompt_eval_count'))))
        output = usage.get('completion_tokens', usage.get('output_tokens', usage.get('candidatesTokenCount', payload.get('eval_count'))))
        def number(value):
            return value if type(value) is int and value >= 0 else None
        self.responses.append({'input_tokens': number(prompt), 'output_tokens': number(output),
            'finish_reason': (payload.get('choices') or [{}])[0].get('finish_reason'),
            'reasoning_tokens': number((usage.get('completion_tokens_details') or {}).get('reasoning_tokens'))})

    def as_dict(self):
        known = len(self.responses) == self.requests and all(
            row['input_tokens'] is not None and row['output_tokens'] is not None for row in self.responses)
        return {'requests': self.requests, 'usage_known': bool(self.requests and known),
                'input_tokens': sum(row['input_tokens'] or 0 for row in self.responses) if known and self.requests else None,
                'output_tokens': sum(row['output_tokens'] or 0 for row in self.responses) if known and self.requests else None,
                'responses': list(self.responses)}


active_trace: ContextVar[CompletionTrace | None] = ContextVar('provider_completion_trace', default=None)


def request_started():
    if (trace := active_trace.get()) is not None:
        trace.started()


def response_received(payload):
    if (trace := active_trace.get()) is not None:
        trace.received(payload)
