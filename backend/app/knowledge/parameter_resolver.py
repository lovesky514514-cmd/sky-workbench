"""Parameter evidence hook with strict official/user namespace separation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ParameterEvidence:
    parameter_name: str
    value: float | int | str
    unit: str
    material: str | None
    temperature: str | None
    condition: str | None
    source: str
    page: int | None
    confidence: float
    namespace: Literal["official_comsol", "user_research"]
    unit_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParameterResolver:
    """Expose a safe hook; numeric extraction remains opt-in and evidence-bound."""

    def resolve(
        self,
        parameter_name: str,
        *,
        official_evidence: list[dict[str, Any]] | None = None,
        user_parameters: list[ParameterEvidence] | None = None,
    ) -> dict[str, Any]:
        official = [
            item
            for item in (official_evidence or [])
            if str(item.get("parameter_name", "")).lower() == parameter_name.lower()
        ]
        user = [
            item.as_dict()
            for item in (user_parameters or [])
            if item.parameter_name.lower() == parameter_name.lower()
            and item.namespace == "user_research"
        ]
        return {
            "parameter_name": parameter_name,
            "official_comsol": official,
            "user_research": user,
            "resolved": False,
            "status": "unverified" if not official and not user else "candidate_evidence_only",
            "detail": "Candidates remain separated by namespace and require explicit scientific validation before use.",
        }


parameter_resolver = ParameterResolver()
