"""Contracts for diagnostic facts, hypotheses and approved simulations."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.execution.expert_contracts import Contract, VersionedRecord
from app.recovery.http_contracts import HttpDecision, HttpObservation

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$')]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9a-f]{64}$')]
Short = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=320)]
Revision = Annotated[int, Field(strict=True, ge=1)]


class Origin(StrEnum):
    API = 'API'
    FRONTEND = 'FRONTEND'
    EXPERT = 'EXPERT_SCHEDULER'
    TOOL = 'TOOL_REGISTRY'
    BRIDGE = 'JAVA_BRIDGE'
    COMSOL = 'COMSOL'
    SCIENCE = 'SCIENTIFIC_VALIDATOR'
    RUNTIME = 'RUNTIME'
    ENVIRONMENT = 'ENVIRONMENT'


class Cause(StrEnum):
    PLAN_REVISION = 'PLAN_REVISION_CONFLICT'
    REVIEW_DIGEST = 'REVIEW_DIGEST_STALE'
    PLAN_PRECONDITION = 'PLAN_PRECONDITION_FAILED'
    PROJECT_REQUIRED = 'WRITABLE_PROJECT_REQUIRED'
    EXPERT_STALE = 'EXPERT_TASK_STALE'
    EVIDENCE_SCOPE = 'EVIDENCE_SCOPE_VIOLATION'
    EVIDENCE_INVALID = 'SCIENTIFIC_EVIDENCE_INVALID'
    DEPENDENCY = 'DEPENDENCY_UNSATISFIED'
    TOOL_PERMISSION = 'TOOL_PERMISSION_DENIED'
    RUN_APPROVAL = 'RUN_APPROVAL_MISSING'
    OWNER_BUSY = 'BRIDGE_OWNER_BUSY'
    BRIDGE_START = 'BRIDGE_START_FAILED'
    BRIDGE_AUTH = 'BRIDGE_AUTH_FAILED'
    MODEL_TAG = 'COMSOL_MODEL_TAG_MISMATCH'
    HEAT_CORE = 'MODEL_READBACK_INCOMPLETE'
    MUTATION_READBACK = 'POST_MUTATION_READBACK_MISMATCH'
    SOLVE_UNKNOWN = 'SOLVE_COMMIT_UNKNOWN'
    SOLVE_UNVERIFIED = 'SOLVE_STATE_UNVERIFIED'
    FIELD_ARRAY = 'FIELD_ARRAY_MISMATCH'
    OUTPUT_EXISTS = 'ARTIFACT_ALREADY_EXISTS'
    ARTIFACT_INVALID = 'ARTIFACT_INTEGRITY_FAILED'
    ENV_MISSING = 'ENV_DEPENDENCY_MISSING'
    ENV_UNSUPPORTED = 'ENV_UNSUPPORTED'
    LICENSE = 'LICENSE_UNAVAILABLE'
    RESOURCE = 'RESOURCE_EXHAUSTED'
    PROCESS_EXIT = 'PROCESS_EXITED'
    DATABASE = 'DATABASE_DEGRADED'
    TIMEOUT = 'OPERATION_TIMEOUT'
    DISCONNECTED = 'TRANSPORT_DISCONNECTED'
    SERVICE_UNAVAILABLE = 'SERVICE_UNAVAILABLE'
    INTERNAL = 'INTERNAL_ERROR'
    HTTP = 'HTTP_POLICY_DIAGNOSIS'
    UNKNOWN = 'UNKNOWN'


class Action(StrEnum):
    DIAGNOSE = 'DIAGNOSE_ONLY'
    REFRESH = 'SAFE_REFRESH'
    SIMULATE = 'SIMULATE_STATUS_REFRESH'
    FORBIDDEN = 'FORBIDDEN'


class State(StrEnum):
    DETECTED = 'DETECTED'
    SNAPSHOTTING = 'SNAPSHOTTING'
    CLASSIFIED = 'CLASSIFIED'
    DIAGNOSING = 'DIAGNOSING'
    PROPOSED = 'PROPOSED'
    WAITING = 'WAITING_APPROVAL'
    SIMULATING = 'SIMULATING'
    VERIFYING = 'VERIFYING'
    RESOLVED = 'RESOLVED'
    UNRESOLVED = 'UNRESOLVED'
    BLOCKED = 'BLOCKED'
    ESCALATED = 'ESCALATED'
    CANCELLED = 'CANCELLED'
    EXPIRED = 'EXPIRED'


class FailureScope(Contract):
    project_id: Identifier | None = None
    session_id: Identifier | None = None
    plan_id: Identifier | None = None
    plan_revision: Revision | None = None
    expert_task_id: Identifier | None = None
    expert_attempt_id: Identifier | None = None
    scientific_run_id: Identifier | None = None
    scientific_attempt_id: Identifier | None = None

    @model_validator(mode='after')
    def bound_attempts(self):
        if self.session_id and not self.project_id:
            raise ValueError('session requires project identity')
        if self.scientific_attempt_id and not self.scientific_run_id:
            raise ValueError('scientific attempt requires RUN identity')
        if self.expert_attempt_id and not self.expert_task_id:
            raise ValueError('expert attempt requires task identity')
        return self


class Facts(Contract):
    phase: Literal['before_submit', 'after_send', 'readback', 'validate', 'export', 'unknown'] = 'unknown'
    command_success: bool | None = Field(default=None, strict=True)
    model_state_verified: bool | None = Field(default=None, strict=True)
    expected_revision: Revision | None = None
    observed_revision: Revision | None = None
    reviewed_digest_matches: bool | None = Field(default=None, strict=True)
    owner_matches: bool | None = Field(default=None, strict=True)
    field_shape_matches: bool | None = Field(default=None, strict=True)
    project_writable: bool | None = Field(default=None, strict=True)
    artifact_sha256: Digest | None = None
    artifact_expected_sha256: Digest | None = None
    engine_version: Literal['6.3', '6.4'] | None = None
    capability: Literal['discovered', 'compiled', 'connected', 'model_tree', 'heat_core',
                        'solved', 'field_readback', 'exported', 'physical_validation'] | None = None
    http: HttpObservation | None = None


class FailureEvent(VersionedRecord):
    event_id: Identifier
    occurred_at_utc: AwareDatetime
    ingested_at_utc: AwareDatetime
    origin: Origin
    operation: Identifier
    method: Literal['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'] | None = None
    route_template: Short | None = None
    http_status: Annotated[int, Field(strict=True, ge=400, le=599)] | None = None
    error_code: Identifier
    safe_summary: Short
    scope: FailureScope
    request_fingerprint: Digest
    correlation_id: Identifier
    parent_event_id: Identifier | None = None
    severity: Literal['WARNING', 'ERROR']
    facts: Facts = Field(default_factory=Facts)


class DiagnosticSnapshot(VersionedRecord):
    snapshot_id: Identifier
    collected_at: AwareDatetime
    source: Literal['session_store_and_run_journal']
    plan_revision: Revision | None
    plan_id: Identifier | None
    plan_hash: Digest | None
    run_id: Identifier | None
    attempt_id: Identifier | None
    run_state: Literal['none', 'active', 'terminal', 'cancelled', 'orphaned']
    owner_matches: bool | None
    sha256: Digest


class RuleDiagnosis(Contract):
    classification: Cause
    rule_id: Identifier
    action: Action
    required_evidence: tuple[Short, ...]
    counter_evidence: tuple[Short, ...]
    safe_next_probe: Short
    is_mutation: bool = False
    allow_retry: bool = False
    http: HttpDecision | None = None


class ModelCandidate(Contract):
    cause_code: Cause
    supporting_evidence_refs: tuple[Identifier, ...] = Field(min_length=1, max_length=6)
    contradicting_evidence_refs: tuple[Identifier, ...] = Field(default=(), max_length=6)


class Candidate(ModelCandidate):
    explanation: Short


class ModelDiagnosis(Contract):
    candidates: tuple[ModelCandidate, ...] = Field(min_length=1, max_length=3)


class DiagnosisReport(VersionedRecord):
    diagnosis_id: Identifier
    case_id: Identifier
    rule: RuleDiagnosis
    expert_task_id: Identifier | None = None
    expert_attempt_id: Identifier | None = None
    evidence_refs: tuple[Identifier, ...]
    missing_evidence: tuple[Short, ...] = ()
    knowledge_refs: tuple[Identifier, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    confidence_basis: Literal['rule', 'insufficient_evidence', 'verified_case_match', 'model_hypotheses_only']
    model_status: Literal['NOT_NEEDED', 'DISABLED', 'UNAVAILABLE', 'INVALID', 'CANDIDATES']


class Proposal(VersionedRecord):
    proposal_id: Identifier
    case_id: Identifier
    snapshot_id: Identifier
    plan_revision: Revision | None
    scientific_attempt_id: Identifier | None
    action: Action
    expected_preconditions: tuple[Short, ...]
    affected_tasks: tuple[Identifier, ...] = ()
    affected_resources: tuple[Literal['session_snapshot'], ...] = ()
    risk_level: Literal['READ_ONLY', 'SIMULATED', 'BLOCKED']
    approval_required: bool
    expected_result: Short
    rollback_available: bool
    verification_plan: tuple[Short, ...]
    evidence_refs: tuple[Identifier, ...]
    digest: Digest


class KnowledgeEntry(VersionedRecord):
    entry_id: Identifier
    cause: Cause
    origin: Origin
    case_signature: Digest | None = None
    component: Identifier | None = None
    versions: tuple[Literal['6.3', '6.4'], ...] = ()
    root_cause: Short
    action: Short
    symptoms: tuple[Short, ...] = ()
    preconditions: tuple[Short, ...] = ()
    diagnostic_checks: tuple[Short, ...] = ()
    negative_cases: tuple[Short, ...] = ()
    verification_steps: tuple[Short, ...] = ()
    source_ref: Identifier | None = None
    evidence_refs: tuple[Identifier, ...]
    verification_refs: tuple[Identifier, ...] = ()
    status: Literal['UNVERIFIED_CANDIDATE', 'REPRODUCED', 'VERIFIED_FIX', 'REJECTED', 'DEPRECATED']
    verification_mode: Literal['simulation', 'observed'] | None = None
    reviewer: Identifier | None = None
    reviewed_at: AwareDatetime | None = None
