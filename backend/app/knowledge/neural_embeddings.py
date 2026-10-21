"""Optional neural embedding adapter; never required for Workbench startup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class NeuralEmbeddingConfig:
    provider: str
    base_url: str
    model: str
    api_key: str = ""


class NeuralEmbeddingProvider:
    """Call Ollama or OpenAI-compatible embeddings only when explicitly configured."""

    def __init__(self, config: NeuralEmbeddingConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        timeout = httpx.Timeout(120, connect=5)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            if self.config.provider == "ollama":
                response = await client.post(
                    f"{self.config.base_url.rstrip('/')}/api/embed",
                    json={"model": self.config.model, "input": texts},
                )
                response.raise_for_status()
                vectors = response.json().get("embeddings", [])
            else:
                headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
                response = await client.post(
                    f"{self.config.base_url.rstrip('/')}/embeddings",
                    headers=headers,
                    json={"model": self.config.model, "input": texts},
                )
                response.raise_for_status()
                vectors = [item.get("embedding", []) for item in response.json().get("data", [])]
        if len(vectors) != len(texts) or any(not isinstance(vector, list) or not vector for vector in vectors):
            raise ValueError("Embedding provider returned an invalid vector batch")
        return [[float(value) for value in vector] for vector in vectors]

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "optional": True,
            "startup_dependency": False,
        }
