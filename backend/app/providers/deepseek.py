"""DeepSeek Chat JSON mode uses a prompt contract plus local schema validation."""

import json
from typing import Any

DEFAULT_MODEL = "deepseek-flash"


def planning_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Send scientific fields, leaving IDs, authority and bookkeeping to Runtime."""
    if schema.get("title") != "ScientificPlan":
        return schema
    result = json.loads(json.dumps(schema))

    def select(node, names):
        properties = node.get("properties", {})
        node["properties"] = {
            key: value for key, value in properties.items() if key in names
        }
        node["required"] = [key for key in node.get("required", []) if key in names]
        node["additionalProperties"] = False

    select(
        result,
        {
            "title",
            "research_goal",
            "physics",
            "geometry",
            "materials",
            "parameters",
            "boundary_conditions",
            "mesh_strategy",
            "study",
            "solver",
            "validation",
            "expected_outputs",
            "assumptions",
            "missing_information",
            "warnings",
            "modeling_brief",
        },
    )
    definitions = result.pop("$defs", {})
    parameter_fields = {
        "name",
        "symbol",
        "value",
        "unit",
        "source",
        "assumption",
        "rationale",
        "applicability",
    }
    if "ScientificParameter" in definitions:
        select(definitions["ScientificParameter"], parameter_fields)
    if "RequestBrief" in definitions:
        select(
            definitions["RequestBrief"],
            {
                "domains",
                "concepts",
                "object_description",
                "equations",
                "requested_outputs",
                "questions",
                "model_outline",
                "symbolic_inputs",
            },
        )

    def references(value):
        if isinstance(value, dict):
            properties = value.get("properties", {})
            if "authorization" in properties and "value" in properties:
                select(value, parameter_fields)
            for key, child in value.items():
                if (
                    key == "$ref"
                    and isinstance(child, str)
                    and child.startswith("#/$defs/")
                ):
                    yield child.removeprefix("#/$defs/")
                else:
                    yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    pending = list(references(result))
    used = {}
    while pending:
        name = pending.pop()
        if name in used or name not in definitions:
            continue
        used[name] = definitions[name]
        pending.extend(references(used[name]))
    if used:
        result["$defs"] = used
    return result


def chat_payload(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    *,
    stream: bool = False,
    json_mode: bool = False,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = list(messages)
    if json_mode:
        instruction = "Return one complete JSON object only, without Markdown."
        if schema:
            instruction += " The JSON must conform to this schema: " + json.dumps(
                planning_schema(schema), ensure_ascii=False, separators=(",", ":")
            )
        content = [{"role": "system", "content": instruction}, *content]
    payload: dict[str, Any] = {
        "model": model,
        "messages": content,
        "temperature": temperature,
        "stream": stream,
        "max_tokens": 16384,
        "reasoning_effort": "low",
    }
    if json_mode:
        # Chat accepts json_object; json_schema belongs to the Responses API.
        # ScientificPlan and unit/approval checks remain authoritative locally.
        payload["response_format"] = {"type": "json_object"}
    return payload
