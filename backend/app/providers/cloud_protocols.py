"""Small HTTP adapters for cloud APIs that do not use Chat Completions.

Only text is sent. ScientificPlan, readback, and evidence validation remain in
the Runtime; a model response cannot certify a scientific result.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx


def _claude_request(messages: list[dict[str, str]], model: str, temperature: float, key: str) -> tuple[dict[str, str], dict[str, Any]]:
    system = "\n".join(item["content"] for item in messages if item["role"] == "system")
    turns = [{"role": item["role"], "content": item["content"]} for item in messages if item["role"] in {"user", "assistant"}]
    if not turns:
        raise ValueError("No user message")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    body: dict[str, Any] = {"model": model, "max_tokens": 8192, "temperature": temperature, "messages": turns}
    if system:
        body["system"] = system
    return headers, body


def _gemini_request(messages: list[dict[str, str]], temperature: float, key: str, json_mode: bool) -> tuple[dict[str, str], dict[str, Any]]:
    system = "\n".join(item["content"] for item in messages if item["role"] == "system")
    turns = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]}
             for item in messages if item["role"] in {"user", "assistant"}]
    if not turns:
        raise ValueError("No user message")
    body: dict[str, Any] = {"contents": turns, "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    return {"x-goog-api-key": key, "Content-Type": "application/json"}, body


def _gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise ValueError("Gemini returned no candidate")
    content = candidates[0].get("content")
    parts = (content.get("parts") or []) if isinstance(content, dict) else []
    if not isinstance(parts, list):
        raise ValueError("Gemini returned no text")
    text = "".join(item["text"] for item in parts if isinstance(item, dict) and isinstance(item.get("text"), str))
    if not text:
        raise ValueError("Gemini returned no text")
    return text


async def complete(
    client: httpx.AsyncClient, provider: str, base_url: str, model: str,
    key: str, messages: list[dict[str, str]], temperature: float, json_mode: bool,
) -> str:
    """Perform one request to the explicitly chosen provider; never fallback."""
    if provider == "claude":
        headers, body = _claude_request(messages, model, temperature, key)
        if json_mode:
            body["system"] = (body.get("system", "") + "\nReturn only a valid JSON object.").strip()
        response = await client.post(f"{base_url}/messages", headers=headers, json=body)
        response.raise_for_status()
        blocks = response.json().get("content", [])
        if not isinstance(blocks, list):
            raise ValueError("Claude returned no text")
        text = "".join(item["text"] for item in blocks if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
        if not text:
            raise ValueError("Claude returned no text")
        return text
    if provider == "gemini":
        headers, body = _gemini_request(messages, temperature, key, json_mode)
        response = await client.post(f"{base_url}/models/{quote(model, safe='')}:generateContent", headers=headers, json=body)
        response.raise_for_status()
        return _gemini_text(response.json())
    raise ValueError("Unsupported cloud protocol")


async def stream(
    client: httpx.AsyncClient, provider: str, base_url: str, model: str,
    key: str, messages: list[dict[str, str]], temperature: float,
) -> AsyncIterator[str]:
    """Emit provider-native text deltas, ignoring metadata and thought blocks."""
    if provider == "claude":
        headers, body = _claude_request(messages, model, temperature, key)
        body["stream"] = True
        url = f"{base_url}/messages"
    elif provider == "gemini":
        headers, body = _gemini_request(messages, temperature, key, False)
        url = f"{base_url}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"
    else:
        raise ValueError("Unsupported cloud protocol")
    async with client.stream("POST", url, headers=headers, json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line.removeprefix("data:").strip()
            if not raw or raw == "[DONE]":
                continue
            payload = json.loads(raw)
            if provider == "claude":
                if payload.get("type") == "content_block_delta" and payload.get("delta", {}).get("type") == "text_delta":
                    yield str(payload["delta"].get("text", ""))
            else:
                yield _gemini_text(payload)
