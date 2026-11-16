"""N04 总控检查点的版本化记录与纯计算。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from app.execution.expert_contracts import Budget, Contract, EvidenceRef, ExpertResult, ExpertTask, ScopedRecord, Text

StopReason = Literal['budget_exhausted', 'repeated_no_progress', 'no_effect', 'unidentifiable',
                     'submission_unknown', 'task_failed', 'cancelled', 'plan_changed']


class TaskRule(Contract):
    task: ExpertTask
    plan_inputs: tuple[Text, ...] = ()
    review_targets: tuple[Text, ...] = ()


class VerifiedTask(Contract):
    result: ExpertResult
    evidence: tuple[EvidenceRef, ...] = ()


class RevisionCheckpoint(ScopedRecord):
    plan: dict
    rules: tuple[TaskRule, ...]
    references: tuple[EvidenceRef, ...]
    verified: dict[str, VerifiedTask]
    issued_evidence_ids: tuple[Text, ...]
    pending: tuple[Text, ...]
    in_flight: tuple[Text, ...]
    dispatch_token: Text | None = None
    limit: Budget
    spent: Budget
    revision_signatures: tuple[Text, ...] = ()
    status: Literal['ACTIVE', 'SETTLED', 'STOPPED']
    stop_reason: StopReason | None = None

    def task(self, task_id: str) -> ExpertTask:
        return next(rule.task.model_copy(update={'scope': self.scope})
                    for rule in self.rules if rule.task.task_id == task_id)

    def catalog(self) -> tuple[EvidenceRef, ...]:
        return (*self.references, *(ref for task in self.verified.values() for ref in task.evidence))

    def ready(self) -> tuple[str, ...]:
        if self.status != 'ACTIVE':
            return ()
        return tuple(name for name in self.pending if set(self.task(name).dependencies) <= self.verified.keys())

    def reserved(self) -> Budget:
        return add_budget(self.task(name).budget_estimate for name in self.pending)


def add_budget(budgets: Iterable[Budget]) -> Budget:
    calls = runs = 0
    for budget in budgets:
        calls += budget.agent_calls
        runs += budget.solver_runs
    return Budget(agent_calls=calls, solver_runs=runs)


def within(limit: Budget, total: Budget) -> bool:
    return total.agent_calls <= limit.agent_calls and total.solver_runs <= limit.solver_runs


def dependent_tasks(rules: tuple[TaskRule, ...], roots: set[str]) -> tuple[str, ...]:
    affected = set(roots)
    while True:
        more = {rule.task.task_id for rule in rules if affected.intersection(rule.task.dependencies)}
        if more <= affected:
            return tuple(rule.task.task_id for rule in rules if rule.task.task_id in affected)
        affected.update(more)


def changed_paths(before: dict, after: dict, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    changes = set()
    for key in before.keys() | after.keys():
        path = (*prefix, key)
        if key not in before or key not in after:
            changes.add(path)
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            changes.update(changed_paths(before[key], after[key], path))
        elif type(before[key]) is not type(after[key]) or before[key] != after[key]:
            changes.add(path)
    return changes
