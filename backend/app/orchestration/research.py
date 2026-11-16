"""ResearchController 的科研讨论服务，复用 N01/N02/N04，不建立新调度器。"""
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.execution.expert_contracts import Budget, Contract, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.execution.revision_checkpoint import TaskRule
from app.execution.revision_coordinator import RevisionCoordinator
from app.orchestration.controller import ReadEvidence
from app.orchestration.evidence import EvidenceVault
from app.orchestration.llm_expert import LLMExpert, ModelUnavailable
from app.orchestration.research_contracts import ResearchEvidence, ResearchLimits
from app.orchestration.research_policy import audit_sources, required_roles, request_signature, rework_for
from app.orchestration.research_store import ResearchStore
from app.redaction import redact_object, redact_text
from app.session.store import utc_now


class LookupSources(Contract):
    query: str


def request_digest(goal, evidence, constraints, limits, strategy='sky'):
    source = {'goal': redact_text(goal), 'constraints': constraints,
              'evidence': [item.model_dump() for item in evidence], 'limits': limits.model_dump(), 'strategy': strategy}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ResearchDiscussion:
    def __init__(self, controller, workspace, provider=None):
        self.controller = controller
        self.sessions = controller.host.sessions
        self.workspace = Path(workspace)
        self.provider = provider or controller.host.providers
        self.store = ResearchStore(self.sessions)

    async def run(self, sid, goal, evidence=(), *, constraints=None, limits=None, request_key=None, strategy='sky'):
        limits = limits or ResearchLimits()
        constraints = constraints or {}
        if strategy not in {'single_agent', 'parallel_no_collaboration', 'full_rework', 'sky'}:
            raise ValueError('unknown_research_comparison_strategy')
        evidence = tuple(ResearchEvidence.model_validate(item) for item in evidence)
        if len({item.evidence_id for item in evidence}) != len(evidence) or any(item.evidence_id == 'research-goal' for item in evidence):
            raise ValueError('duplicate_or_reserved_research_evidence_id')
        # 相同请求只能领取一次；新资料/约束会产生新的研究请求，但不会创建科学 RUN。
        content_hash = request_digest(goal, evidence, constraints, limits, strategy)
        request_key = request_key or content_hash
        if previous := self.store.find(sid, request_key):
            if previous['goal'].get('content_sha256') != content_hash:
                raise ValueError('research_idempotency_key_conflict')
            return previous
        session = self.sessions.get(sid)
        identity = uuid4().hex
        scope = ExpertScope(project_id=session['project_id'], session_id=sid, case_id=identity,
            plan_id='research-' + identity, plan_revision=1, run_id='research-' + identity, attempt_id=uuid4().hex)
        self.store.create(scope, request_key, {'goal': redact_text(goal), 'constraints': constraints,
            'content_sha256': content_hash, 'limits': limits.model_dump(), 'strategy': strategy})
        vault = EvidenceVault(self.workspace, scope)
        produced, opinions, signatures = {}, {}, set()
        refs = []
        for item in evidence:
            cleaned = redact_object(item.model_dump())
            frozen = vault.freeze(item.evidence_id, 'registered.research_source', {
                'source': cleaned, 'registered_at': utc_now(), 'scope': scope.model_dump(),
                'original_sha256': hashlib.sha256(item.model_dump_json().encode()).hexdigest()})
            refs.append(frozen.ref)
        goal_record = vault.freeze('research-goal', 'user.research_goal', {
            'goal': redact_text(goal), 'constraints': redact_object(constraints), 'registered_at': utc_now(),
            'scope': scope.model_dump(), 'scientific_run_authorized': False})
        refs.append(goal_record.ref)
        # 初始目录只接受 reference，因此原始字节由宿主登记为引用，值由本服务按摘要读取。
        from app.execution.expert_contracts import EvidenceRef
        registered = {ref.evidence_id: ref for ref in refs}
        references = tuple(EvidenceRef(schema_version=1, evidence_id=ref.evidence_id, kind='reference',
            source_ref=ref.source_ref, locator=ref.artifact.sha256, applicability='this research goal and source snapshot only') for ref in refs)
        def rule(name, capability, objective, dependencies=()):
            return TaskRule(task=ExpertTask(schema_version=1, scope=scope, task_id=name, capability=capability,
                objective=objective, dependencies=tuple(dependencies), input_evidence_refs=tuple(registered),
                requested_tools=('research.lookup_sources',) if capability == 'source_acquisition' else ('research.read_evidence',),
                budget_estimate=Budget(agent_calls=int(capability != 'source_acquisition'), solver_runs=0)))
        checkpoint = self.sessions.create_checkpoint(scope, (rule('intent', 'research_intent', goal),),
            Budget(agent_calls=limits.model_calls, solver_runs=0), references)
        async def read(task, args):
            self.store.guard(scope)
            checkpoint = self.sessions.read_checkpoint(scope)
            visible = set(task.input_evidence_refs)
            for dep in task.dependencies:
                visible.update(ref.evidence_id for ref in checkpoint.verified[dep].evidence)
                visible.update(ref for claim in checkpoint.verified[dep].result.claims for ref in claim.evidence_refs)
            if args.evidence_id not in visible:
                raise PermissionError('research_evidence_outside_task_context')
            ref = registered.get(args.evidence_id) or next(ref for ref in checkpoint.catalog() if ref.evidence_id == args.evidence_id)
            self.controller.event(sid, 'expert_tool', case_id=scope.case_id, task_id=task.task_id,
                                  tool='research.read_evidence', evidence_id=ref.evidence_id)
            return vault.read(ref)
        async def lookup(task, args):
            self.store.guard(scope)
            if not args.query.strip() or len(args.query) > 12000:
                raise ValueError('research_source_query_limit')
            async with asyncio.timeout(30):
                results = await asyncio.to_thread(self.controller.host._retrieve, sid, redact_text(args.query), 3)
            self.store.guard(scope)
            self.controller.event(sid, 'expert_tool', case_id=scope.case_id, task_id=task.task_id,
                tool='research.lookup_sources', result_count=len(results))
            # 检索命中不等于材料数值已经验证，模型只能引用宿主冻结的检索包。
            return {'results': redact_object(results), 'registered_at': utc_now(),
                    'verification': 'UNVERIFIED_RETRIEVAL', 'scope': scope.model_dump()}
        from typing import get_args
        from app.orchestration.research_contracts import Role
        capabilities = {role: ('research.read_evidence',) for role in get_args(Role)}
        capabilities['source_acquisition'] = ('research.lookup_sources',)
        scheduler = ExpertScheduler(capabilities=capabilities,
            tools={'research.read_evidence': ExpertTool(ReadEvidence, read),
                   'research.lookup_sources': ExpertTool(LookupSources, lookup)}, max_concurrency=limits.concurrency)
        coordinator = RevisionCoordinator(self.sessions, scheduler)
        adapter = LLMExpert(self.provider, self.store, limits)
        async def execute(context):
            self.store.guard(scope)
            if context.task.capability == 'source_acquisition':
                packet = await context.call_tool('research.lookup_sources', {'query': context.task.objective})
                ref = vault.freeze('sources-' + context.task.task_id, 'knowledge.authorized_retrieval', packet).ref
                produced[context.task.task_id] = (ref,)
                return {'schema_version': 1, 'scope': scope.model_dump(), 'task_id': context.task.task_id,
                    'summary': 'Retrieved sources remain unverified; applicability review is required', 'claims': []}
            import time
            timeline = self.controller.host._timeline_start(sid, category='LLM', phase='Research',
                label='科研专家：' + context.task.capability, tool='research.llm_expert',
                input_summary={'case_id': scope.case_id, 'task_id': context.task.task_id,
                               'evidence_refs': [ref.evidence_id for ref in context.evidence]})
            started = time.perf_counter()
            try:
                opinion = await adapter.execute(context)
            except BaseException:
                self.controller.host._timeline_finish(sid, timeline, status='error',
                    output_summary='Expert output was not accepted; see persisted model-call receipt',
                    duration_ms=round((time.perf_counter()-started)*1000))
                raise
            self.controller.host._timeline_finish(sid, timeline, status='success',
                output_summary=redact_text(opinion.summary), duration_ms=round((time.perf_counter()-started)*1000))
            self.store.guard(scope)
            opinions[context.task.task_id] = opinion
            record = vault.freeze('opinion-' + context.task.task_id, 'llm.expert_opinion', {
                'opinion': redact_object(opinion.model_dump()), 'registered_at': utc_now(),
                'scope': scope.model_dump(), 'scientific_certified': False})
            produced[context.task.task_id] = (record.ref,)
            # 详细科学意见冻结在证据中；N01 交换最小主张，不允许模型伪造宿主身份。
            return {'schema_version': 1, 'scope': scope.model_dump(), 'task_id': context.task.task_id,
                'summary': redact_text(opinion.summary), 'claims': [
                    {'claim_id': claim.claim_id, 'statement': redact_text(claim.statement), 'evidence_refs': claim.evidence_refs}
                    for claim in opinion.claims]}
        async def dispatch():
            return await coordinator.dispatch(scope, workspace_root=self.workspace, execute=execute,
                admit_result=lambda result: produced.get(result.task_id, ()), raise_on_failure=True)
        issues = audit_sources(evidence, constraints)
        outcome = {'content_sha256': content_hash, 'issues': issues, 'scientific_status': 'NOT_RUN', 'strategy': strategy,
                   'rework': rework_for(issues), 'solver_runs': 0, 'source_count': len(evidence)}
        self.controller.event(sid, 'research_started', scope=scope.model_dump(), goal_sha256=content_hash)
        try:
            checkpoint = await dispatch()
            full_reworked = False
            for _ in range(limits.model_calls):
                if strategy == 'single_agent':
                    break
                known = {rule.task.capability for rule in checkpoint.rules}
                additions = []
                needed = required_roles(goal, evidence, issues) - known
                if strategy == 'parallel_no_collaboration':
                    needed.discard('scientific_counterevidence')
                acquire = 'literature_materials' in needed and not evidence and 'source_acquisition' not in known
                if acquire:
                    additions.append(rule('lookup-sources', 'source_acquisition', redact_text(goal), ('intent',)))
                for role in sorted(needed):
                    if role == 'literature_materials' and acquire:
                        continue
                    dependencies = ('intent', 'lookup-sources') if role == 'literature_materials' and not evidence else ('intent',)
                    additions.append(rule(role, role, '独立审查研究目标、证据适用性和未解决问题；不可自授科学通过',
                        () if strategy == 'parallel_no_collaboration' else dependencies))
                if not additions and strategy != 'parallel_no_collaboration':
                    # 专家可以要求补证，由宿主查重、限额、绑定问题与依赖后才创建任务。
                    for name, opinion in opinions.items():
                        for request in opinion.requests:
                            signature = request_signature(request)
                            if signature in signatures or request.capability in known:
                                continue
                            signatures.add(signature)
                            additions.append(rule('requested-' + signature[:12], request.capability, request.question, (name,)))
                            known.add(request.capability)
                if not additions and 'reproducibility' not in known:
                    additions.append(rule('independent-audit', 'reproducibility',
                        '独立列出未解决问题、证据引用和可复现步骤；不得宣布科学结果 PASS',
                        () if strategy == 'parallel_no_collaboration' else tuple(checkpoint.verified)))
                if not additions and strategy == 'full_rework' and issues and not full_reworked:
                    full_reworked = True
                    additions = [rule('full-rework-' + saved.task.task_id, saved.task.capability,
                        '审核仍有未解决问题，重新完成本项研究分析；不得重写总结宣称物理通过', (saved.task.task_id,))
                        for saved in checkpoint.rules]
                    outcome['full_rework_tasks'] = len(additions)
                if not additions:
                    break
                checkpoint = self.sessions.extend_checkpoint(scope, tuple(additions),
                    reason='host-approved capability and evidence requests', evidence_refs=('research-goal',))
                if checkpoint.status == 'STOPPED':
                    raise ValueError('research_model_budget_exhausted')
                checkpoint = await dispatch()
            open_questions = [{'task_id': name, 'claim_id': claim.claim_id, 'question': question,
                               'evidence_refs': list(claim.evidence_refs)}
                              for name, opinion in opinions.items() for claim in opinion.claims for question in claim.questions]
            outcome['open_questions'] = open_questions
            status = 'NEEDS_EVIDENCE' if issues or open_questions else 'READY_FOR_PLAN'
            outcome['accepted_opinions'] = list(opinions)
            self.controller.event(sid, 'research_reviewed', scope=scope.model_dump(), status=status,
                                  issues=issues, scientific_status='NOT_RUN')
            return self.store.finish(scope, status, outcome)
        except (Exception, asyncio.CancelledError) as exc:
            status = ('CANCELED' if isinstance(exc, asyncio.CancelledError) else 'DEGRADED' if isinstance(exc, ModelUnavailable)
                      else 'BUDGET_EXHAUSTED' if 'budget' in str(exc) else 'FAILED')
            self.sessions.stop_checkpoint(scope, reason='cancelled' if status == 'CANCELED' else 'task_failed')
            outcome.update(reason=type(exc).__name__, accepted_opinions=list(opinions), scientific_status='NOT_RUN')
            result = self.store.finish(scope, status, outcome)
            self.controller.event(sid, 'research_stopped', scope=scope.model_dump(), status=status,
                                  reason=type(exc).__name__, automatic_solver_replay=False)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return result
