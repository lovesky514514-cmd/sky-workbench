"""HTTP request models and runtime enums."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentMode(StrEnum):
    ASK = "ASK"
    PLAN = "PLAN"
    RUN = "RUN"


class AgentState(StrEnum):
    IDLE = "IDLE"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    PRECHECK = "PRECHECK"
    WAITING_FOR_EXECUTION = "WAITING_FOR_EXECUTION"
    BUILDING = "BUILDING"
    BUILD_VALIDATION = "BUILD_VALIDATION"
    SOLVING = "SOLVING"
    SOLUTION_VALIDATION = "SOLUTION_VALIDATION"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    REPAIRING = "REPAIRING"
    REVALIDATING = "REVALIDATING"
    EXPORTING = "EXPORTING"
    REPORTING = "REPORTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    INTERRUPTED = "INTERRUPTED"


class CreateSessionRequest(BaseModel):
    project_id: str = "workbench"
    title: str | None = None


class AgentMessageRequest(BaseModel):
    mode: AgentMode
    content: str = Field(min_length=1, max_length=20_000)


class IntentCompileRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class LegacyActionRequest(BaseModel):
    action: Literal["keep", "import_keep", "backup_remove", "ignore"]
    path: str = Field(min_length=1, max_length=2_000)
    confirmed: bool = False
    backup_root: str | None = None


class ModelOperationRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
    confirmed: bool = False


class ProviderSettingsRequest(BaseModel):
    providers: dict[str, dict[str, Any]]
    retrieval_mode: Literal["basic", "enhanced", "fast", "accurate", "deep"] | None = None
    active_provider: str | None = None
    comsol_root: str | None = Field(default=None, max_length=2000)


class ExtensionToggleRequest(BaseModel):
    enabled: bool


class LiteratureTextRequest(BaseModel):
    source_title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=50_000)


class LiteratureSourceRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class ProviderTestRequest(BaseModel):
    provider: str


class PlanUpdateRequest(BaseModel):
    plan: dict[str, Any]


class KnowledgeRefreshRequest(BaseModel):
    include_legacy: bool = True


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)
    mode: Literal["fast", "accurate", "deep"] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class JavaBridgeStartRequest(BaseModel):
    model_path: str | None = None


class JavaBridgeParameterRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    value: str = Field(min_length=1, max_length=512)


class JavaBridgeStudyRequest(BaseModel):
    study_tag: str = Field(default="std1", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class CancelRunRequest(BaseModel):
    force: bool = False


class RunCorrectionRequest(BaseModel):
    path: str = Field(pattern=r"^parameters\.[A-Za-z][A-Za-z0-9_]{0,63}$")
    value: float
    unit: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9*/^()._-]+$")
    reason: str = Field(min_length=3, max_length=500)
    review_id: str | None = None


class RunReviewRequest(BaseModel):
    path: str = Field(pattern=r"^parameters\.[A-Za-z][A-Za-z0-9_]{0,63}$")
    proposed_value: float
    unit: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9*/^()._-]+$")
    reason: str = Field(min_length=3, max_length=500)


class RunBoundaryReviewRequest(BaseModel):
    boundary_object_id: str = Field(min_length=8, max_length=128)
    proposed_value: float
    unit: Literal['K']
    reason: str = Field(min_length=3, max_length=500)


class RunBoundaryCorrectionRequest(BaseModel):
    review_id: str = Field(min_length=8, max_length=128)
