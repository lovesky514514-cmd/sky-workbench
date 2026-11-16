"""总控复核 N03 提案，并使用 N02 调度已经预留的任务。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.execution.expert_contracts import EvidenceRef, ExpertIntake, ExpertResult, ExpertScope, RevisionProposal
from app.execution.expert_scheduler import ExpertContext, ExpertExecutor, ExpertScheduler
from app.execution.heat_review import HeatReview
from app.execution.revision_checkpoint import RevisionCheckpoint, add_budget, changed_paths, dependent_tasks, within
from app.execution.revision_store import CheckpointConflict
from app.scientific.execution_contract import execution_digest, scientific_inputs, verify_binding


def _under(path: tuple[str, ...], root: str) -> bool:
    parts = tuple(root.split('.'))
    return path[:len(parts)] == parts


def _validate_proposal(checkpoint: RevisionCheckpoint, review: HeatReview,
                       proposal: RevisionProposal) -> None:
    if review.scope != checkpoint.scope or proposal.scope != checkpoint.scope:
        raise CheckpointConflict('stale_review_or_proposal')
    if review.status == 'PASS':
        raise CheckpointConflict('review_does_not_require_revision')
    claims = {item.claim.claim_id: item for item in review.claims}
    current = {item.evidence_id: item for item in checkpoint.catalog()}
    evidence = {item.evidence_id: item for item in review.evidence}
    historical_ids = set(checkpoint.issued_evidence_ids) - current.keys()
    if len(evidence) != len(review.evidence) or historical_ids.intersection(evidence) or any(
            current[ref] != evidence[ref] for ref in current.keys() & evidence.keys()):
        raise CheckpointConflict('review_evidence_not_current')
    intake = ExpertIntake(scope=checkpoint.scope, tasks=(checkpoint.task(rule.task.task_id).model_dump()
                         for rule in checkpoint.rules), evidence={**current, **evidence}.values())
    intake.proposal(proposal.model_dump(), known_claims=claims.keys())
    for issue in proposal.issues:
        claim = claims[issue.claim_id].claim
        cited = set(issue.evidence_refs)
        if cited and (not cited <= evidence.keys() or not cited.intersection(claim.evidence_refs)):
            raise CheckpointConflict('issue_evidence_not_linked')


def _roots(checkpoint: RevisionCheckpoint, proposal: RevisionProposal, paths: set[tuple[str, ...]]) -> set[str]:
    rules = {rule.task.task_id: rule for rule in checkpoint.rules}
    issue_tasks = {name for issue in proposal.issues for name in issue.affected_task_ids}
    roots = {name for name, rule in rules.items() if any(
        _under(path, target) for path in paths for target in rule.plan_inputs)}
    plan_targets = set()
    backed_tasks = {name for issue in proposal.issues if issue.evidence_refs for name in issue.affected_task_ids}
    for action in proposal.actions:
        rule = rules[action.task_id]
        if action.task_id not in issue_tasks:
            return set()
        if action.action == 'revise_plan':
            if (action.task_id not in backed_tasks or action.target not in rule.plan_inputs
                    or not any(_under(path, action.target) for path in paths)):
                return set()
            plan_targets.add(action.target)
        else:
            if action.target not in rule.review_targets:
                return set()
            roots.add(action.task_id)
    if any(not any(_under(path, target) for target in plan_targets) for path in paths):
        return set()
    return roots


def _signature(plan: dict, proposal: RevisionProposal, review: HeatReview) -> str:
    return execution_digest({
        'inputs': scientific_inputs(plan),
        'actions': sorted({(item.task_id, item.action, item.target) for item in proposal.actions}),
        'assessment': sorted((claim.claim.claim_id, claim.status) for claim in review.claims),
        'score': review.score_total,
    })


class RevisionCoordinator:
    def __init__(self, store, scheduler: ExpertScheduler):
        self.store = store
        self.scheduler = scheduler

    def approve_revision(self, scope: ExpertScope, proposal: RevisionProposal,
                         review: HeatReview) -> RevisionCheckpoint:
        """复核已经独立审核的建议；科学计划变更须先走现有计划保存和审核。"""
        store = self.store
        with store._checkpoint_write() as db:
            checkpoint = store._checkpoint_row(db, scope)
            if db.execute('SELECT 1 FROM run_journal WHERE run_id=?', (scope.run_id,)).fetchone():
                raise CheckpointConflict('scientific_revisions_require_controller_rebind')
            if checkpoint.status != 'SETTLED' or checkpoint.dispatch_token:
                raise CheckpointConflict('checkpoint_not_settled')
            _validate_proposal(checkpoint, review, proposal)
            plan = store._checkpoint_plan(db, scope)
            plan_edit = any(action.action == 'revise_plan' for action in proposal.actions)
            if plan_edit:
                if plan.get('revision') != scope.plan_revision + 1:
                    raise CheckpointConflict('next_plan_revision_required')
                verify_binding(db, scope.session_id, plan)
                if plan.get('runnable') is not True:
                    raise CheckpointConflict('next_plan_is_not_runnable')
                paths = changed_paths(scientific_inputs(checkpoint.plan), scientific_inputs(plan))
                if not paths:
                    return store._stop_checkpoint(db, checkpoint, 'no_effect')
            else:
                store._require_checkpoint_plan(checkpoint, plan)
                paths = set()
            roots = _roots(checkpoint, proposal, paths)
            affected = dependent_tasks(checkpoint.rules, roots)
            if not affected:
                return store._stop_checkpoint(db, checkpoint, 'unidentifiable')
            signature = _signature(plan, proposal, review)
            verified = {name: item for name, item in checkpoint.verified.items() if name not in affected}
            invalidated = [ref.evidence_id for name in affected for ref in checkpoint.verified[name].evidence]
            next_scope = scope.model_copy(update={'plan_revision': plan['revision'], 'attempt_id': uuid4().hex})
            changed = checkpoint.model_copy(update={'scope': next_scope, 'plan': plan, 'verified': verified})
            facts = dict(revision_id=proposal.revision_id, previous_attempt_id=scope.attempt_id,
                         affected_tasks=affected, changed_paths=['.'.join(path) for path in sorted(paths)],
                         invalidated_evidence=invalidated,
                         retained_tasks=list(verified), decision_sha256=signature)
            if signature in checkpoint.revision_signatures:
                return store._stop_checkpoint(db, changed, 'repeated_no_progress', **facts)
            total = add_budget((checkpoint.spent, *(checkpoint.task(name).budget_estimate for name in affected)))
            if not within(checkpoint.limit, total):
                return store._stop_checkpoint(db, changed, 'budget_exhausted', **facts)
            changed = changed.model_copy(update={
                'status': 'ACTIVE', 'pending': affected,
                'revision_signatures': (*checkpoint.revision_signatures, signature),
            })
            return store._save_checkpoint(db, changed, 'revision_approved', **facts)

    async def dispatch(self, scope: ExpertScope, *, workspace_root: Path, execute: ExpertExecutor,
                       admit_result: Callable[[ExpertResult], tuple[EvidenceRef, ...]],
                       pause_before: Callable[[RevisionCheckpoint], bool] | None = None,
                       raise_on_failure: bool = False) -> RevisionCheckpoint:
        """已认领的任务必须由宿主验收后才能成为下一波的依赖。"""
        store = self.store
        token = store.begin_checkpoint_dispatch(scope)

        async def invoke(context: ExpertContext):
            store.claim_checkpoint_task(scope, context.task.task_id, token=token)
            return await execute(context)

        def publish(result: ExpertResult):
            store.publish_checkpoint_task(result, admit_result(result), token=token)

        try:
            while (checkpoint := store.read_checkpoint(scope)).status == 'ACTIVE':
                if pause_before is not None and pause_before(checkpoint):
                    return store.finish_checkpoint_dispatch(scope, token)
                ready = checkpoint.ready()
                if not ready:
                    return store.finish_checkpoint_dispatch(scope, token, failed=True)
                tasks = []
                for name in (*checkpoint.verified, *ready):
                    task = checkpoint.task(name)
                    output_refs = (ref.evidence_id for dep in task.dependencies
                                   for ref in checkpoint.verified[dep].evidence)
                    refs = tuple(dict.fromkeys((*task.input_evidence_refs, *output_refs)))
                    tasks.append(task.model_copy(update={'input_evidence_refs': refs}).model_dump())
                intake = ExpertIntake(scope=scope, tasks=tasks, evidence=checkpoint.catalog())
                batch = await self.scheduler.run(
                    intake, workspace_root=workspace_root, execute=invoke, publish=publish,
                    retained=(item.result for item in checkpoint.verified.values()), enforce_solver_budget=True,
                    assert_active=lambda task: store.assert_checkpoint_claim(task.scope, task.task_id, token),
                )
                if batch.failures or batch.blocked:
                    stopped = store.finish_checkpoint_dispatch(scope, token, failed=True)
                    if raise_on_failure and batch.errors:
                        raise next(iter(batch.errors.values()))
                    return stopped
            return store.finish_checkpoint_dispatch(scope, token)
        except asyncio.CancelledError:
            # 派发账保留为已认领。恢复时不能把没有回执的任务当成未执行。
            raise
        except (ValueError, OSError):
            stopped = store.finish_checkpoint_dispatch(scope, token, failed=True)
            if raise_on_failure:
                raise
            return stopped
