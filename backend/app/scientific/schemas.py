"""Versioned scientific plan, citation and parameter provenance schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ParameterStatus(StrEnum):
    BENCHMARK_DEFINED = "Benchmark-defined"
    VERIFIED = "Verified"
    LITERATURE = "Literature"
    COMSOL_DEFAULT = "COMSOL Default"
    USER_PROVIDED = "User Provided"
    ASSUMPTION = "Assumption"
    UNKNOWN = "Unknown"


class PlanQualityStatus(StrEnum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"


class Citation(BaseModel):
    document_id: str
    file: str
    file_name: str
    title: str
    page: int | None = None
    page_status: Literal["precise", "page_unknown", "invalid"] = "page_unknown"
    section: str | None = None
    chunk_id: str = Field(validation_alias=AliasChoices("chunk_id", "chunk"))
    unit_id: str | None = None
    excerpt: str = ""
    source_type: str = Field(default="Project Knowledge", validation_alias=AliasChoices("source_type", "source"))
    ingestion_time: str = Field(default_factory=now_iso)
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    year: int | None = None

    @model_validator(mode="after")
    def normalize_page_status(self) -> "Citation":
        if self.page is not None and self.page_status == "page_unknown":
            self.page_status = "precise"
        if self.unit_id is None and self.chunk_id.startswith("KB-"):
            self.unit_id = self.chunk_id
        return self


class ScientificParameter(BaseModel):
    name: str
    symbol: str = ""
    value: float | int | str
    unit: str
    source: str
    source_page: int | None = None
    source_chunk: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    assumption: bool = False
    verified: bool = False
    status: ParameterStatus = ParameterStatus.UNKNOWN
    original_value: float | int | str | None = None
    original_unit: str | None = None
    normalized_value: float | int | str | None = None
    normalized_unit: str | None = None
    quantity: dict[str, Any] = Field(default_factory=dict)

    @field_validator("value", mode="before")
    @classmethod
    def coerce_numeric_string(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip():
            try:
                return float(value) if any(marker in value.lower() for marker in (".", "e")) else int(value)
            except ValueError:
                return value
        return value

    @model_validator(mode="after")
    def normalize_status(self) -> "ScientificParameter":
        source = self.source.lower()
        if self.assumption:
            self.status = ParameterStatus.ASSUMPTION
            self.verified = False
        elif self.status == ParameterStatus.UNKNOWN and "user" in source:
            self.status = ParameterStatus.USER_PROVIDED
        elif self.status == ParameterStatus.UNKNOWN and self.source_chunk:
            self.status = ParameterStatus.LITERATURE
        return self


class PlanStep(BaseModel):
    phase: str
    label: str
    purpose: str = ""


class PlanQualityCheck(BaseModel):
    category: str
    name: str
    status: str
    message: str
    observed: Any = None
    expected: Any = None


class PlanQualityReport(BaseModel):
    status: PlanQualityStatus = PlanQualityStatus.INVALID
    checks: list[PlanQualityCheck] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GeometryFeature(BaseModel):
    """Declarative geometry feature understood by a physics adapter."""

    tag: str
    kind: Literal[
        "rectangle", "circle", "ellipse", "polygon", "block", "cylinder", "sphere",
        "move", "rotate", "scale", "union", "difference", "intersection",
    ]
    dimension: Literal[2, 3]
    parameters: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)


class SelectionDefinition(BaseModel):
    tag: str
    label: str = ""
    entity_dimension: Literal[0, 1, 2, 3]
    method: Literal["explicit", "box", "ball", "adjacent", "named"] = "named"
    entities: list[int] = Field(default_factory=list)
    criteria: dict[str, Any] = Field(default_factory=dict)
    allow_empty: bool = False


class MaterialDefinition(BaseModel):
    tag: str
    label: str
    selection: str | None = None
    properties: dict[str, ScientificParameter] = Field(default_factory=dict)
    provenance: Literal[
        "user_provided", "verified_source", "literature", "comsol_library",
        "benchmark_defined", "assumption", "unknown",
    ] = "unknown"


class ConditionDefinition(BaseModel):
    tag: str
    kind: str
    selection: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class MeshDefinition(BaseModel):
    level: Literal["coarse", "normal", "fine", "custom"] = "normal"
    physics_controlled: bool = True
    custom: dict[str, Any] = Field(default_factory=dict)
    refinements: list[dict[str, Any]] = Field(default_factory=list)


class StudyDefinition(BaseModel):
    tag: str = "std1"
    kind: Literal["stationary", "time_dependent", "parametric_sweep"] = "stationary"
    times: list[float] = Field(default_factory=list)
    sweep_parameters: dict[str, list[float | int | str]] = Field(default_factory=dict)

    def case_count(self) -> int:
        count = 1
        for values in self.sweep_parameters.values():
            count *= max(1, len(values))
        return count


class OutputDefinition(BaseModel):
    kind: Literal["mph", "png", "csv", "json", "model_card"]
    expression: str | None = None
    filename: str | None = None


class ResourcePolicy(BaseModel):
    allow_high_risk: bool = False
    allow_critical_risk: bool = False
    monitor_interval_seconds: float = Field(default=2.0, ge=1.0, le=3.0)
    warning_ram_available_percent: float = Field(default=10.0, ge=1.0, le=50.0)
    critical_ram_available_percent: float = Field(default=5.0, ge=0.5, le=25.0)
    warning_disk_free_gb: float = Field(default=5.0, ge=0.1)
    critical_disk_free_gb: float = Field(default=1.0, ge=0.05)


class CouplingEdge(BaseModel):
    source: str
    target: str
    variables: list[str] = Field(default_factory=list)
    status: Literal["VERIFIED", "EXPERIMENTAL", "UNVERIFIED"] = "UNVERIFIED"


class BatteryCellDefinition(BaseModel):
    """Parameter/provenance foundation only; it is not an executable battery model."""

    chemistry: str
    geometry: str
    parameters: dict[str, ScientificParameter] = Field(default_factory=dict)
    initial_soc: ScientificParameter | None = None
    operating_mode: Literal["current", "voltage", "power", "load_cycle"] | None = None
    module_status: Literal["MODULE_MISSING", "UNVERIFIED", "EXPERIMENTAL", "VERIFIED"] = "UNVERIFIED"


class ScientificPlan(BaseModel):
    # Preserve the accepted wire version; v0.6 fields are optional extensions.
    schema_version: str = "2.0"
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str | None = None
    title: str
    research_goal: str
    original_request: str | None = None
    physics: list[str] = Field(default_factory=list)
    geometry: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    parameters: dict[str, ScientificParameter] = Field(default_factory=dict)
    boundary_conditions: list[str] = Field(default_factory=list)
    mesh_strategy: list[str] = Field(default_factory=list)
    study: list[str] = Field(default_factory=list)
    solver: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    sources: list[Citation] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    runnable: bool = False
    quality_status: PlanQualityStatus = PlanQualityStatus.INVALID
    quality_report: PlanQualityReport | None = None
    source: str = "offline-scientific-planner"
    ai_provider: str = "none"
    ai_model: str = "none"
    precheck_summary: dict[str, Any] | None = None
    geometry_features: list[GeometryFeature] = Field(default_factory=list)
    selections: list[SelectionDefinition] = Field(default_factory=list)
    material_definitions: list[MaterialDefinition] = Field(default_factory=list)
    initial_conditions: list[ConditionDefinition] = Field(default_factory=list)
    boundary_definitions: list[ConditionDefinition] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)
    functions: list[dict[str, Any]] = Field(default_factory=list)
    mesh_definition: MeshDefinition | None = None
    study_definitions: list[StudyDefinition] = Field(default_factory=list)
    solver_definition: dict[str, Any] = Field(default_factory=dict)
    output_definitions: list[OutputDefinition] = Field(default_factory=list)
    validation_definitions: list[dict[str, Any]] = Field(default_factory=list)
    resource_policy: ResourcePolicy = Field(default_factory=ResourcePolicy)
    couplings: list[CouplingEdge] = Field(default_factory=list)
    battery_cell: BatteryCellDefinition | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("parameters", mode="before")
    @classmethod
    def normalize_parameters(cls, value: Any) -> Any:
        if isinstance(value, list):
            value = {str(item.get("name", f"parameter_{index}")): item for index, item in enumerate(value) if isinstance(item, dict)}
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for name, raw in value.items():
            if isinstance(raw, ScientificParameter):
                normalized[str(name)] = raw
                continue
            if not isinstance(raw, dict):
                raw = {"value": raw, "unit": "", "source": "unspecified"}
            item = {**raw}
            item.setdefault("name", str(name))
            item.setdefault("symbol", str(name))
            item.setdefault("unit", "")
            item.setdefault("source", "unspecified")
            source = str(item["source"]).lower()
            if "assumption" in source or "verify" in source:
                item.setdefault("assumption", True)
                item.setdefault("status", ParameterStatus.ASSUMPTION)
            elif "user" in source:
                item.setdefault("verified", True)
                item.setdefault("status", ParameterStatus.USER_PROVIDED)
            normalized[str(name)] = item
        return normalized

    def execution_payload(self) -> dict[str, Any]:
        """Return a stable JSON payload; RUN consumes this instead of the prompt."""

        return self.model_dump(mode="json")


class PlanValidation(BaseModel):
    valid: bool
    ready_to_run: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
