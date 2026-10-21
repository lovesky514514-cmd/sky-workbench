"""Bounded, inspectable context selection for local models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agent.scientific_tools import scientific_tool_layer


@dataclass(slots=True)
class ContextBundle:
    messages: list[dict[str, str]]
    supplemental_context: str
    estimated_input_tokens: int
    budget_tokens: int
    selected_tools: list[str]
    citation_count: int
    recent_message_count: int
    truncated: bool

    def summary(self) -> dict[str, Any]:
        return {
            "estimated_input_tokens": self.estimated_input_tokens,
            "budget_tokens": self.budget_tokens,
            "selected_tools": self.selected_tools,
            "citation_count": self.citation_count,
            "recent_message_count": self.recent_message_count,
            "truncated": self.truncated,
        }


class ContextBuilder:
    """Keep durable history in storage while presenting a bounded working set to the LLM."""

    def __init__(self, max_input_tokens: int = 9_500, recent_messages: int = 8) -> None:
        self.max_input_tokens = max_input_tokens
        self.recent_messages = recent_messages

    def build(
        self,
        *,
        session: dict[str, Any],
        mode: str,
        prompt: str,
        citations: list[dict[str, Any]],
        system_prompt: str,
        external_context_tokens: int = 0,
        context_snapshot: dict[str, Any] | None = None,
    ) -> ContextBundle:
        char_budget = max(512, self.max_input_tokens - external_context_tokens) * 4
        used = 0
        truncated = False

        def bounded(text: str, preferred: int) -> str:
            nonlocal used, truncated
            allowed = max(0, min(preferred, char_budget - used))
            selected = text[:allowed]
            used += len(selected)
            if len(selected) < len(text):
                truncated = True
            return selected

        tools = scientific_tool_layer.relevant(mode, prompt)
        authority = context_snapshot or session
        plan = authority.get("plan") or {}
        model = authority.get("model") or {}
        plan_summary = {
            "plan_id": plan.get("plan_id"),
            "workflow_id": plan.get("workflow_id"),
            "quality_status": plan.get("quality_status"),
            "runnable": plan.get("runnable"),
            "physics": plan.get("physics", []),
            "geometry": plan.get("geometry", []),
            "materials": plan.get("materials", []),
            "missing_information": plan.get("missing_information", []),
        } if plan else None
        model_summary = {
            "geometry": model.get("geometry", []),
            "physics": model.get("physics", []),
            "study": model.get("study", []),
            "solver": model.get("solver", []),
            "results": model.get("results", []),
        } if model else None

        evidence = "\n\n".join(
            f"SOURCE {index + 1} | {item['citation'].get('title')} | page={item['citation'].get('page')} | chunk={item['citation'].get('chunk_id')}\n{item['text']}"
            for index, item in enumerate(citations)
        )
        supplemental_parts = [
            "SCIENTIFIC TOOL VOCABULARY (descriptive only; ASK cannot execute):\n" + scientific_tool_layer.prompt_summary(mode, prompt),
            "CURRENT PLAN SUMMARY:\n" + json.dumps(plan_summary, ensure_ascii=False) if plan_summary else "CURRENT PLAN SUMMARY: none",
            "CURRENT MODEL SUMMARY:\n" + json.dumps(model_summary, ensure_ascii=False) if model_summary else "CURRENT MODEL SUMMARY: none",
            "TRACEABLE LOCAL EVIDENCE:\n" + evidence if evidence else "TRACEABLE LOCAL EVIDENCE: none retrieved",
            "DURABLE STATE FACTS:\n" + json.dumps(authority.get('facts', {}), ensure_ascii=False) if context_snapshot else "DURABLE STATE FACTS: legacy context path",
            "DURABLE RECENT EVENTS:\n" + json.dumps(authority.get('recent_events', []), ensure_ascii=False) if context_snapshot else "DURABLE RECENT EVENTS: legacy context path",
        ]
        supplemental = "\n\n".join(supplemental_parts)

        raw_recent = [] if context_snapshot else list(session.get("messages", []))[-self.recent_messages:]
        scientific_memory = [
            str(item.get("content", ""))[:500]
            for item in ([] if context_snapshot else list(session.get("messages", []))[-16:])
            if item.get("role") == "user" and item.get("content")
        ][-4:]
        recent: list[dict[str, str]] = []
        for item in raw_recent:
            content = str(item.get("content", ""))
            if item.get("role") == "user" and content.strip() == prompt.strip() and item is raw_recent[-1]:
                continue
            if not content:
                continue
            recent.append({"role": "assistant" if item.get("role") == "assistant" else "user", "content": bounded(content, 1_600)})

        memory_text = "SCIENTIFIC MEMORY (compact user-goal summary):\n" + ("\n".join(f"- {item}" for item in scientific_memory) if scientific_memory else "none")
        supplemental = bounded(supplemental + "\n\n" + memory_text, 24_000)
        messages = [{"role": "system", "content": bounded(system_prompt, 4_000)}]
        messages.append({"role": "system", "content": supplemental})
        messages.extend(recent)
        messages.append({"role": "user", "content": bounded(prompt, 12_000)})
        estimated = estimate_tokens(messages) + max(0, external_context_tokens)
        return ContextBundle(
            messages=messages,
            supplemental_context=supplemental,
            estimated_input_tokens=estimated,
            budget_tokens=self.max_input_tokens,
            selected_tools=[tool.name for tool in tools],
            citation_count=len(citations),
            recent_message_count=len(recent),
            truncated=truncated,
        )


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    characters = sum(len(item.get("content", "")) for item in messages)
    return max(1, (characters + 3) // 4 + len(messages) * 4)


context_builder = ContextBuilder()
