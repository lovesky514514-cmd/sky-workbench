"""总控在研究讨论后派发可计量的提案服务，验收后才发布 ScientificPlan。"""
import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from app.execution.expert_contracts import Budget, Contract, EvidenceRef, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.execution.revision_checkpoint import TaskRule
from app.execution.revision_coordinator import RevisionCoordinator
from app.orchestration.planning import PlanningProvider
from app.orchestration.research_store import ResearchStore


async def propose_reviewed_plan(controller, sid, content, parent, config, workspace):
    host = controller.host
    studies = ResearchStore(host.sessions)
    spent_calls = len(parent['calls'])
    spent_tokens = sum(row['reserved_tokens'] for row in parent['calls'])
    remaining = min(2, int(config.get('model_calls', 8)) - spent_calls)
    tokens = int(config.get('token_limit', 160000)) - spent_tokens
    if remaining < 1 or tokens < 8192:
        controller.event(sid, 'planning_budget_exhausted', parent_case_id=parent['case_id'])
        return None
    identity = uuid4().hex
    claimed = studies.claim_plan(parent['case_id'], identity)
    if claimed != identity:
        controller.event(sid, 'planning_claim_reused', parent_case_id=parent['case_id'], case_id=claimed,
                         automatic_model_replay=False)
        return None
    scope = ExpertScope(project_id=parent['project_id'], session_id=sid, case_id=identity,
        plan_id='planning-' + identity, plan_revision=1, run_id='planning-' + identity, attempt_id=uuid4().hex)
    limits = SimpleNamespace(model_calls=remaining, token_limit=tokens, output_tokens=8192)
    studies.create(scope, identity, {'goal': content, 'parent_case_id': parent['case_id'],
        'phase': 'scientific_plan_proposal', 'limits': {'model_calls': remaining, 'token_limit': tokens, 'output_tokens': 8192}})
    from app.agent.planner import ScientificPlanner
    provider = PlanningProvider(host.providers, studies, scope, limits)
    planner = ScientificPlanner(provider)
    draft = None
    async def propose(task, _):
        nonlocal draft
        studies.guard(scope)
        draft = await host._propose_plan(sid, content, planner=planner)
        if draft is None:
            raise ValueError('scientific_plan_proposal_failed')
        from app.orchestration.plan_binding import bind_supported_draft
        bound = bind_supported_draft(draft.plan)
        if bound is not draft.plan:
            draft = replace(draft, plan=bound)
            report = host._validate_plan(bound)
            bound.precheck_summary = report.model_dump(mode='json') if report else None
            controller.event(sid, 'plan_adapter_bound', case_id=scope.case_id,
                workflow_id=bound.workflow_id, scientific_status='NOT_RUN', runnable=False)
        if parent['status'] == 'NEEDS_EVIDENCE':
            draft.plan.runnable = False
            draft.plan.planning_status = 'DRAFT_NEEDS_INPUT'
            unresolved = [item['required'] for item in parent['outcome'].get('issues', [])]
            unresolved.extend(item['question'] for item in parent['outcome'].get('open_questions', []))
            draft.plan.missing_information = list(dict.fromkeys([*draft.plan.missing_information, *unresolved]))
    scheduler = ExpertScheduler(capabilities={'research_planning': ('planning.propose',)},
        tools={'planning.propose': ExpertTool(Contract, propose)}, max_concurrency=1)
    ref = EvidenceRef(schema_version=1, evidence_id='reviewed-research', kind='reference',
        source_ref='controller.research_review', locator=hashlib.sha256(json.dumps(parent['outcome'], sort_keys=True).encode()).hexdigest(),
        applicability='research readiness only; not a scientific result or RUN approval')
    task = ExpertTask(schema_version=1, scope=scope, task_id='plan-proposal', capability='research_planning',
        objective='Propose ScientificPlan without publishing or solving', input_evidence_refs=(ref.evidence_id,),
        requested_tools=('planning.propose',), budget_estimate=Budget(agent_calls=remaining, solver_runs=0))
    host.sessions.create_checkpoint(scope, (TaskRule(task=task),), Budget(agent_calls=remaining, solver_runs=0), (ref,))
    async def execute(context):
        await context.call_tool('planning.propose', {})
        return {'schema_version': 1, 'scope': scope.model_dump(), 'task_id': task.task_id,
                'summary': 'Proposal schema and deterministic checks completed; publication remains with controller', 'claims': []}
    try:
        async with asyncio.timeout(240):
            await RevisionCoordinator(host.sessions, scheduler).dispatch(scope, workspace_root=workspace,
                execute=execute, admit_result=lambda _: (), raise_on_failure=True)
        studies.guard(scope)
        records = studies.read(scope.case_id)['calls']
        status = 'NEEDS_EVIDENCE' if parent['status'] == 'NEEDS_EVIDENCE' else 'DEGRADED' if draft.provider.startswith('offline-') else 'READY_FOR_PLAN'
        studies.finish(scope, status, {'scientific_status': 'NOT_RUN', 'parent_case_id': parent['case_id'],
            'plan_id': draft.plan.plan_id, 'expected_revision': draft.expected_revision,
            'parent_reserved_model_calls': spent_calls, 'model_requests': sum(row['receipt'].get('requests') or 0 for row in records),
            'proposal_provider': draft.provider})
    except (Exception, asyncio.CancelledError) as exc:
        host.sessions.stop_checkpoint(scope, reason='cancelled' if isinstance(exc, asyncio.CancelledError) else 'task_failed')
        studies.finish(scope, 'CANCELED' if isinstance(exc, asyncio.CancelledError) else 'FAILED',
            {'scientific_status': 'NOT_RUN', 'reason': type(exc).__name__, 'parent_case_id': parent['case_id']})
        if isinstance(exc, asyncio.CancelledError):
            raise
        controller.event(sid, 'planning_stopped', reason=type(exc).__name__, case_id=scope.case_id)
        return None
    # N02 已收齐回执，原计划版本仍一致；此时才执行原发布服务。发布失败不能重封已经结束的讨论。
    return await host._publish_plan(sid, draft)
