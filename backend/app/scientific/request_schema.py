"""Shared planning concepts; a model proposal is not execution authority."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class RequestFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    value: float | str
    unit: str = ""
    original: str
    span: tuple[int, int]
    normalized: bool = False
    issue: str | None = None
    quantity: dict[str, Any] = Field(default_factory=dict)
    entity_id: str | None = None
    role: str = "unspecified"
    utf16_span: tuple[int, int] | None = None


class RequestEntity(BaseModel):
    id: str
    label: str
    kind: Literal["body", "medium", "environment", "region"] = "body"
    material: str | None = None
    mentions: list[tuple[int, int]] = Field(default_factory=list)


class RequestRelation(BaseModel):
    kind: Literal["contact", "containment"]
    entity_ids: list[str]
    original: str
    span: tuple[int, int]
    condition: str = "requires_review"


class PlanningOption(BaseModel):
    id: str
    label: str
    consequence: str


class PlanningQuestion(BaseModel):
    key: str
    question: str
    options: list[PlanningOption]
    required: bool = True


class EngineCandidate(BaseModel):
    engine: str
    role: str
    execution_status: Literal[
        "NOT_ASSESSED", "ADAPTER_REQUIRED", "AVAILABLE_FOR_PREFLIGHT"
    ] = "NOT_ASSESSED"


class RequestBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domains: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    object_description: str = ""
    facts: list[RequestFact] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=list)
    questions: list[PlanningQuestion] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    engine_candidates: list[EngineCandidate] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    inference_method: Literal["deterministic_concepts", "provider_semantic"] = (
        "deterministic_concepts"
    )
    model_outline: dict[str, Any] = Field(default_factory=dict)
    symbolic_inputs: list[dict[str, str]] = Field(default_factory=list)
    entities: list[RequestEntity] = Field(default_factory=list)
    relations: list[RequestRelation] = Field(default_factory=list)
    request_sha256: str = ""
    span_indexing: Literal["unicode_codepoints"] = "unicode_codepoints"
