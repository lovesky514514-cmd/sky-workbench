"""Configurable embedding and reranker capability registry.

Capability reporting is deliberately conservative.  A configured model is not
called installed merely because its name appears in settings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.config import settings_store
from app.knowledge.semantic_runtime import semantic_runtime


Availability = Literal["ready", "loading", "error", "unavailable", "disabled", "installed"]


@dataclass(frozen=True, slots=True)
class ModelBackendStatus:
    kind: Literal["embedding", "reranker"]
    provider: str
    model: str
    status: Availability
    selected: bool
    dimensions: int | None
    context_size: int | None
    backend: str
    detail: str
    emergency_fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalModelRegistry:
    """Describe configured retrieval models without probing or downloading them."""

    def status(self) -> dict[str, Any]:
        settings = settings_store.load()
        rag = settings.get("rag", {})
        dense = rag.get("dense", {}) if isinstance(rag, dict) else {}
        reranker = rag.get("reranker", {}) if isinstance(rag, dict) else {}
        fallback = rag.get("fallback", {}) if isinstance(rag, dict) else {}
        dense_enabled = bool(dense.get("enabled", True))
        reranker_enabled = bool(reranker.get("enabled", True))
        runtime = semantic_runtime.status()
        embedding_runtime = runtime["embedding"]
        reranker_runtime = runtime["reranker"]
        neural_embedding_available = embedding_runtime["status"] == "Ready"
        neural_reranker_available = reranker_runtime["status"] == "Ready"
        fallback_allowed = bool(fallback.get("hash_embedding_allowed", True))
        embedding = ModelBackendStatus(
            kind="embedding",
            provider=str(dense.get("provider", "local")),
            model=str(embedding_runtime["model"]),
            status="disabled" if not dense_enabled else str(embedding_runtime["status"]).lower(),
            selected=dense_enabled,
            dimensions=embedding_runtime.get("dimensions"),
            context_size=32768,
            backend="project-local semantic subprocess",
            detail=(
                "Real local Qwen inference verified"
                if neural_embedding_available
                else runtime.get("error") or "Model files may be present, but readiness requires successful real inference"
            ),
        )
        reranker_status = ModelBackendStatus(
            kind="reranker",
            provider=str(reranker.get("provider", "local")),
            model=str(reranker_runtime["model"]),
            status="disabled" if not reranker_enabled else str(reranker_runtime["status"]).lower(),
            selected=reranker_enabled,
            dimensions=None,
            context_size=None,
            backend="project-local semantic subprocess",
            detail=(
                "Real local Qwen reranker inference verified"
                if neural_reranker_available
                else runtime.get("error") or "Reranker readiness requires successful real scoring"
            ),
        )
        emergency = ModelBackendStatus(
            kind="embedding",
            provider="builtin",
            model="local-hash-384",
            status="installed" if fallback_allowed else "disabled",
            selected=bool(dense_enabled and not neural_embedding_available and fallback_allowed),
            dimensions=384,
            context_size=None,
            backend="numpy",
            detail="Deterministic lexical feature embedding; emergency fallback only; no neural semantics are claimed",
            emergency_fallback=True,
        )
        return {
            "embedding": embedding.as_dict(),
            "reranker": reranker_status.as_dict(),
            "fallback_embedding": emergency.as_dict(),
            "active_embedding": emergency.model if emergency.selected else embedding.model if embedding.status == "ready" else "unavailable",
            "active_reranker": reranker_status.model if reranker_status.status == "ready" else "unavailable",
            "semantic_runtime": runtime,
        }


retrieval_model_registry = RetrievalModelRegistry()
