"""Candidate-only Scientific Skill Engine used for Batch 001 shadow routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class SkillManifest:
    id: str
    version: str
    description: str
    risk: str
    invocation: str
    status: str
    entrypoint: str
    physics_tags: tuple[str, ...] = ()
    step_tags: tuple[str, ...] = ()
    error_tags: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillManifest":
        data = dict(value)
        for name in ("physics_tags", "step_tags", "error_tags", "required_tools", "references", "tests", "completion_criteria"):
            data[name] = tuple(data.get(name) or ())
        data.setdefault("provenance", {})
        data.pop("content_hash", None)
        digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=list).encode("utf-8")).hexdigest()
        return cls(**data, content_hash=digest)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("physics_tags", "step_tags", "error_tags", "required_tools", "references", "tests", "completion_criteria"):
            result[name] = list(result[name])
        return result


@dataclass(frozen=True, slots=True)
class SkillValidation:
    valid: bool
    issues: tuple[str, ...]


class SkillValidator:
    def __init__(self, available_tools: set[str]) -> None:
        self.available_tools = set(available_tools)

    def validate(self, manifest: SkillManifest) -> SkillValidation:
        issues: list[str] = []
        if not manifest.completion_criteria:
            issues.append("missing_completion_criteria")
        if not manifest.tests:
            issues.append("missing_tests")
        for tool in manifest.required_tools:
            if tool not in self.available_tools:
                issues.append(f"missing_required_tool:{tool}")
        material = " ".join((manifest.description, *manifest.completion_criteria))
        if re.search(r"\b(?:boundary|domain|edge)\s*(?:id\s*)?[0-9]+\b", material, flags=re.I):
            issues.append("stale_entity_assumption")
        if manifest.status == "production":
            issues.append("batch001_production_forbidden")
        return SkillValidation(not issues, tuple(issues))


class SkillRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], SkillManifest] = {}

    def register(self, manifest: SkillManifest) -> None:
        key = (manifest.id, manifest.version)
        if key in self._items:
            raise ValueError(f"duplicate skill manifest: {manifest.id}@{manifest.version}")
        self._items[key] = manifest

    def get(self, skill_id: str, version: str | None = None) -> SkillManifest:
        matches = [item for (sid, _), item in self._items.items() if sid == skill_id and (version is None or item.version == version)]
        if not matches:
            raise KeyError(skill_id)
        return sorted(matches, key=lambda item: item.version)[-1]

    def all(self) -> tuple[SkillManifest, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.id, item.version)))

    def promote(self, skill_id: str) -> None:
        self.get(skill_id)
        raise ValueError("production promotion is forbidden in Batch 001")

    @classmethod
    def with_reference_candidates(cls) -> "SkillRegistry":
        registry = cls()
        common = {"version": "0.1.0", "invocation": "router-only", "status": "candidate", "entrypoint": "reference-only",
                  "tests": ["held-out-required"], "provenance": {"source": "human", "source_refs": ["Codex_DSH_v0.3:MATTSKILLS"]}}
        entries = [
            {"id": "boundary-selection", "description": "Use current geometry evidence to map physical boundaries.", "risk": "high", "physics_tags": ["generic"], "step_tags": ["selection", "boundary-condition"], "error_tags": ["wrong-boundary", "ambiguous-selection"], "required_tools": ["model_inspect"], "completion_criteria": ["Current entity evidence justifies every selection."]},
            {"id": "transport-diluted-species", "description": "Configure or review diffusion, concentration and species flux.", "risk": "high", "physics_tags": ["transport of diluted species", "tds"], "step_tags": ["plan", "solve"], "error_tags": ["flux"], "required_tools": ["model_inspect"], "completion_criteria": ["Declared species checks are evidenced."]},
            {"id": "unit-sanity-check", "description": "Normalize and audit mixed scientific units before solving.", "risk": "medium", "physics_tags": ["generic"], "step_tags": ["plan", "precheck"], "error_tags": ["unit", "magnitude"], "required_tools": [], "completion_criteria": ["All supplied quantities retain source units and SI values."]},
            {"id": "mass-conservation-verification", "description": "Evaluate species or mass balance with explicit sign and tolerance.", "risk": "high", "physics_tags": ["transport of diluted species", "electrochemistry"], "step_tags": ["validation"], "error_tags": ["mass-balance", "flux"], "required_tools": ["result_evaluate"], "completion_criteria": ["Balance residual meets a declared tolerance."]},
            {"id": "solver-convergence-recovery", "description": "Diagnose convergence failure before changing solver settings.", "risk": "high", "physics_tags": ["generic"], "step_tags": ["solve", "repair"], "error_tags": ["nonconvergence", "singular"], "required_tools": ["solver_status"], "completion_criteria": ["Original failure is gone and physical checks still pass."]},
            {"id": "battery-model-workflow", "description": "Coordinate the existing battery workflow without inventing missing fields.", "risk": "critical", "physics_tags": ["battery", "electrochemistry"], "step_tags": ["plan", "validation"], "error_tags": ["missing-check"], "required_tools": ["model_inspect"], "completion_criteria": ["All required existing workflow checks are present."]},
        ]
        for entry in entries:
            registry.register(SkillManifest.from_dict({**common, **entry}))
        return registry


class SkillLoader:
    """Progressive-disclosure loader; Batch 001 may see pointers only."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def pointers(self) -> list[dict[str, Any]]:
        return [{"id": item.id, "version": item.version, "description": item.description,
                 "risk": item.risk, "status": item.status, "manifest_hash": item.content_hash}
                for item in self.registry.all()]

    def load(self, skill_id: str, *, skill_condition: str) -> dict[str, Any]:
        if skill_condition == "S0_no_skill":
            raise PermissionError("S0_no_skill forbids Skill body loading")
        manifest = self.registry.get(skill_id)
        if manifest.status != "production":
            raise PermissionError("candidate Skill cannot be loaded into scientific context")
        return manifest.as_dict()


class SkillTelemetry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def record(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)


@dataclass(frozen=True, slots=True)
class SkillRoute:
    candidates: list[dict[str, Any]]
    selected: list[dict[str, Any]]
    router_confidence: float | None
    reason_tags: list[str]
    skill_condition: str = "S0_no_skill"
    skill_context_injected: bool = False
    skill_tokens: int = 0
    skill_hashes: list[str] = field(default_factory=list)
    wrong_skill_suspected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillRouter:
    version = "rule-shadow-v0.3"

    @staticmethod
    def _matches(tags: tuple[str, ...], values: set[str]) -> bool:
        return any(tag.lower() in value or value in tag.lower() for tag in tags for value in values)

    def shadow_route(self, state: dict[str, Any], registry: SkillRegistry) -> SkillRoute:
        physics = {str(item).lower() for item in state.get("physics", [])}
        steps = {str(state.get("step_type", "")).lower()}
        errors = {str(item).lower() for item in state.get("error_tags", [])}
        tools = {str(item) for item in state.get("available_tools", [])}
        risk = str(state.get("risk", "")).lower()
        candidates: list[dict[str, Any]] = []
        for manifest in registry.all():
            score = 0.0
            reasons: list[str] = []
            if self._matches(manifest.physics_tags, physics): score += 2.0; reasons.append("physics_match")
            if "generic" in manifest.physics_tags and physics: score += 0.25; reasons.append("generic_physics")
            if self._matches(manifest.step_tags, steps): score += 1.5; reasons.append("step_type_match")
            if self._matches(manifest.error_tags, errors): score += 2.0; reasons.append("error_signature_match")
            if manifest.required_tools and all(tool in tools for tool in manifest.required_tools): score += 0.5; reasons.append("tool_match")
            if manifest.risk in {"high", "critical"} and risk in {"high", "critical"}: score += 0.25; reasons.append("risk_relevance")
            if score > 0:
                candidates.append({"id": manifest.id, "version": manifest.version, "score": score, "reason_tags": reasons,
                                   "risk": manifest.risk, "manifest_hash": manifest.content_hash})
        candidates.sort(key=lambda item: (-item["score"], item["id"]))
        selected = [dict(item) for item in candidates if item["score"] >= 1.5][:3]
        confidence = round(min(1.0, selected[0]["score"] / 5.0), 3) if selected else None
        reasons = sorted({tag for item in selected for tag in item["reason_tags"]})
        return SkillRoute(candidates, selected, confidence, reasons,
                          wrong_skill_suspected=bool(state.get("wrong_skill_suspected", False)))
