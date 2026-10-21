"""Research model identity seam; identity never implies model availability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelRequest:
    name: str
    provider: str
    size: str = "medium"
    quantization: str | None = None
    revision: str | None = None
    allow_fallback: bool = False


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    requested: dict[str, Any]
    actual: dict[str, Any]
    fallback_chain: list[dict[str, Any]]
    fallback_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested,
            "actual_model": self.actual,
            "fallback_chain": self.fallback_chain,
            "fallback_reason": self.fallback_reason,
        }


class ModelAdapter:
    """Resolve only an explicitly declared target or an explicitly allowed fallback."""

    def __init__(self, catalog: list[dict[str, Any]]) -> None:
        self.catalog = [dict(item) for item in catalog]

    @classmethod
    def from_provider_statuses(cls, statuses: list[dict[str, Any]]) -> "ModelAdapter":
        catalog: list[dict[str, Any]] = []
        for status in statuses:
            if status.get("status") != "Ready":
                continue
            details = status.get("model_details") or []
            if details:
                for detail in details:
                    catalog.append({
                        "provider": status.get("name"), "name": detail.get("name"), "status": "Ready",
                        "size": detail.get("parameter_size"), "quantization": detail.get("quantization_level"),
                        "revision": None,
                    })
            elif status.get("model"):
                catalog.append({"provider": status.get("name"), "name": status.get("model"), "status": "Ready"})
        return cls(catalog)

    def resolve(self, request: ModelRequest) -> ModelIdentity:
        requested = {
            "name": request.name, "provider": request.provider, "size": request.size,
            "quantization": request.quantization, "revision": request.revision,
        }
        exact = next((item for item in self.catalog if item.get("status") == "Ready" and item.get("provider") == request.provider and item.get("name") == request.name), None)
        if exact:
            actual = self._actual(exact)
            return ModelIdentity(requested, actual, [], None)
        fallback = None
        if request.allow_fallback:
            fallback = next((item for item in self.catalog if item.get("status") == "Ready" and item.get("provider") == request.provider), None)
        if fallback:
            actual = self._actual(fallback)
            return ModelIdentity(requested, actual, [actual], "declared_post_resolution_fallback")
        unavailable = {
            "name": None, "provider": None, "size": None, "quantization": None,
            "revision": None, "status": "UNAVAILABLE",
        }
        return ModelIdentity(requested, unavailable, [], "no_declared_target_available")

    @staticmethod
    def _actual(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": item.get("name"), "provider": item.get("provider"), "size": item.get("size"),
            "quantization": item.get("quantization"), "revision": item.get("revision"), "status": "READY",
        }
