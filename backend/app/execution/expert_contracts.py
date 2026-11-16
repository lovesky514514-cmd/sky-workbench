"""Versioned expert workflow records and the admission boundary for model output."""

from __future__ import annotations

from collections.abc import Iterable, Set
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
NonNegative = Annotated[int, Field(strict=True, ge=0)]
ReferenceList = Annotated[tuple[Text, ...], Field(min_length=1)]
Criterion = Literal['goal_alignment', 'scientific_correctness', 'evidence', 'reproducibility', 'delivery']
HEAT_RUBRIC_WEIGHTS = {
    'goal_alignment': 20,
    'scientific_correctness': 30,
    'evidence': 20,
    'reproducibility': 15,
    'delivery': 15,
}


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class VersionedRecord(Contract):
    schema_version: Literal[1]

    @field_validator('schema_version', mode='before')
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError('schema_version must be an integer')
        return value


class ExpertScope(Contract):
    """The coordinator's attempt identity, which can exist before a solver RUN."""

    project_id: Text
    session_id: Text
    case_id: Text
    plan_id: Text
    plan_revision: Annotated[int, Field(strict=True, ge=1)]
    run_id: Text
    attempt_id: Text


class ScopedRecord(VersionedRecord):
    scope: ExpertScope


class Budget(Contract):
    agent_calls: NonNegative
    solver_runs: NonNegative


class ExpertTask(ScopedRecord):
    task_id: Text
    capability: Text
    objective: Text
    dependencies: tuple[Text, ...] = ()
    requested_tools: tuple[Text, ...] = ()
    input_evidence_refs: tuple[Text, ...] = ()
    budget_estimate: Budget


class ArtifactOrigin(Contract):
    """The producing run journal identity and the digest of its original bytes."""

    run_id: Text
    attempt_id: Text
    sha256: Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9a-f]{64}$')]


class EvidenceRef(VersionedRecord):
    evidence_id: Text
    kind: Literal['reference', 'run_artifact']
    source_ref: Text
    locator: Text
    applicability: Text
    artifact: ArtifactOrigin | None = None

    @model_validator(mode='after')
    def require_artifact_origin(self) -> EvidenceRef:
        if (self.kind == 'run_artifact') != (self.artifact is not None):
            raise ValueError('Only run artifact evidence has a producing run, attempt and hash')
        return self


class ExpertClaim(Contract):
    claim_id: Text
    statement: Text
    evidence_refs: tuple[Text, ...] = ()


class ExpertResult(ScopedRecord):
    task_id: Text
    summary: Text
    claims: tuple[ExpertClaim, ...] = ()


class ReviewIssue(VersionedRecord):
    issue_id: Text
    claim_id: Text
    category: Literal['counterevidence', 'assumption_conflict', 'intent_mismatch', 'numerical',
                      'provenance', 'missing_evidence', 'tool_state', 'delivery']
    reason: Text
    evidence_refs: tuple[Text, ...] = ()
    affected_task_ids: ReferenceList

    @model_validator(mode='after')
    def require_evidence_for_observed_defects(self) -> ReviewIssue:
        if self.category != 'missing_evidence' and not self.evidence_refs:
            raise ValueError('An observed defect must cite evidence')
        return self


class ScoreDimension(Contract):
    criterion: Criterion
    points: NonNegative
    reason: Text
    evidence_refs: ReferenceList


class ReviewScore(ScopedRecord):
    rubric_version: Literal['heat-v1']
    dimensions: tuple[ScoreDimension, ...] = ()
    unverified_reason: Text | None = None

    @model_validator(mode='after')
    def require_complete_rubric(self) -> ReviewScore:
        if self.unverified_reason is not None:
            if self.dimensions:
                raise ValueError('An unverified review cannot contain a score')
            return self
        by_criterion = {item.criterion: item for item in self.dimensions}
        if len(self.dimensions) != len(HEAT_RUBRIC_WEIGHTS) or by_criterion.keys() != HEAT_RUBRIC_WEIGHTS.keys():
            raise ValueError('Each rubric criterion must be scored exactly once')
        for criterion, maximum in HEAT_RUBRIC_WEIGHTS.items():
            if by_criterion[criterion].points > maximum:
                raise ValueError(f'{criterion} exceeds the rubric maximum of {maximum}')
        return self

    @property
    def total(self) -> int | None:
        return None if self.unverified_reason is not None else sum(item.points for item in self.dimensions)


class RevisionAction(Contract):
    task_id: Text
    action: Literal['gather_evidence', 'revise_plan', 'revalidate']
    target: Text
    expected_evidence: Text


class RevisionProposal(ScopedRecord):
    revision_id: Text
    issues: Annotated[tuple[ReviewIssue, ...], Field(min_length=1)]
    actions: Annotated[tuple[RevisionAction, ...], Field(min_length=1)]
    budget_estimate: Budget


class ExpertIntake:
    """Check records against coordinator-owned scope and admitted evidence.

    Evidence is supplied by the host for this scope after provenance and reuse
    checks. A valid record carries no execution authority. The caller must still
    check current scope when publishing results and enforce RUN approval.
    """

    def __init__(self, *, scope: ExpertScope, tasks: Iterable[dict[str, Any]],
                 evidence: Iterable[EvidenceRef]) -> None:
        self._scope = scope
        self._evidence: dict[str, EvidenceRef] = {}
        self._tasks: dict[str, ExpertTask] = {}
        for reference in evidence:
            if reference.evidence_id in self._evidence:
                raise ValueError(f'Duplicate evidence ID: {reference.evidence_id}')
            self._evidence[reference.evidence_id] = reference
        for payload in tasks:
            task = ExpertTask.model_validate(payload)
            self._check_scope(task)
            self._require_known(task.input_evidence_refs, self._evidence.keys(), 'evidence')
            if task.task_id in self._tasks:
                raise ValueError(f'Duplicate task ID: {task.task_id}')
            self._tasks[task.task_id] = task
        if not self._tasks:
            raise ValueError('At least one expert task is required')
        for task in self._tasks.values():
            self._require_known(task.dependencies, self._tasks.keys(), 'task dependency')
        graph = TopologicalSorter({task.task_id: task.dependencies for task in self._tasks.values()})
        try:
            graph.prepare()
        except CycleError as error:
            raise ValueError('Expert task dependencies contain a cycle') from error

    @property
    def tasks(self) -> tuple[ExpertTask, ...]:
        return tuple(self._tasks.values())

    def evidence_for(self, ids: Iterable[str]) -> tuple[EvidenceRef, ...]:
        refs = tuple(dict.fromkeys(ids))
        self._require_known(refs, self._evidence.keys(), 'evidence')
        return tuple(self._evidence[ref] for ref in refs)

    def result(self, payload: dict[str, Any], *, dispatched_task_id: str) -> ExpertResult:
        result = ExpertResult.model_validate(payload)
        self._check_scope(result)
        self._require_known((dispatched_task_id,), self._tasks.keys(), 'task')
        if result.task_id != dispatched_task_id:
            raise ValueError('Result does not match the dispatched expert task')
        if len({claim.claim_id for claim in result.claims}) != len(result.claims):
            raise ValueError('A result cannot repeat a claim ID')
        for claim in result.claims:
            self._require_known(claim.evidence_refs, self._evidence.keys(), 'evidence')
        return result

    def score(self, payload: dict[str, Any]) -> ReviewScore:
        score = ReviewScore.model_validate(payload)
        self._check_scope(score)
        for dimension in score.dimensions:
            self._require_known(dimension.evidence_refs, self._evidence.keys(), 'evidence')
        return score

    def proposal(self, payload: dict[str, Any], *, known_claims: Set[str]) -> RevisionProposal:
        proposal = RevisionProposal.model_validate(payload)
        self._check_scope(proposal)
        if len({issue.issue_id for issue in proposal.issues}) != len(proposal.issues):
            raise ValueError('A proposal cannot repeat an issue ID')
        for issue in proposal.issues:
            self._require_known((issue.claim_id,), known_claims, 'claim')
            self._require_known(issue.evidence_refs, self._evidence.keys(), 'evidence')
            self._require_known(issue.affected_task_ids, self._tasks.keys(), 'task')
        self._require_known((action.task_id for action in proposal.actions), self._tasks.keys(), 'task')
        return proposal

    def _check_scope(self, record: ScopedRecord) -> None:
        if record.scope != self._scope:
            raise ValueError('Record does not match the current expert scope')

    @staticmethod
    def _require_known(ids: Iterable[str], known: Set[str], kind: str) -> None:
        missing = set(ids) - known
        if missing:
            raise ValueError(f'Unknown {kind} IDs: {", ".join(sorted(missing))}')
