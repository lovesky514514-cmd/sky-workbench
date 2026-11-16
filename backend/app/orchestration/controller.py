"""科研任务总控 / Evidence-driven research orchestration."""
from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

from app.execution.expert_contracts import Budget, Contract, EvidenceRef, ExpertIntake, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.execution.revision_checkpoint import TaskRule
from app.execution.revision_coordinator import RevisionCoordinator
from app.execution.heat_review import BENCHMARK_ID, RECEIPT_ID, ExecutionReceipt, review_heat
from app.validators.physics.heat_benchmark import FIELD_SOURCE, MODEL_SOURCE, HeatBenchmark, HeatFieldReadback, HeatModelReadback, check_field, check_model
from app.orchestration.evidence import EvidenceVault
from app.orchestration.run_services import ExecutionServices
from app.orchestration.policy import next_decision
from app.recovery.ingest import requires_commit_readback


class ReadEvidence(Contract):
    evidence_id: str


class ResearchController:
    readback_timeout_seconds = 180

    def __init__(self, runtime):
        self.host = runtime
        self.services = ExecutionServices(runtime)
        self._research_tasks = {}

    def event(self, sid, kind, **facts):
        self.host.sessions.append_event(sid, 'orchestrator.' + kind, facts)

    def discussion(self):
        from app.agent import runtime as env
        from app.orchestration.research import ResearchDiscussion
        env.WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        return ResearchDiscussion(self, env.WORKSPACE_ROOT)

    async def discuss(self, sid, goal, evidence=(), **options):
        return await self.begin_research(sid, goal, evidence, **options)

    def begin_research(self, sid, goal, evidence=(), **options):
        active = self._research_tasks.get(sid)
        if active is not None and not active.done():
            raise ValueError('research_already_active')
        task = asyncio.create_task(self.discussion().run(sid, goal, evidence, **options), name='research-discussion-' + sid)
        self._research_tasks[sid] = task
        return task

    async def cancel_research(self, sid):
        task = self._research_tasks.get(sid)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            from app.models import AgentMode, AgentState
            if self.host.sessions.get(sid)['state'] in {'RESEARCHING', 'PLANNING'}:
                self.host.sessions.set_state(sid, AgentState.PLAN_READY)
                self.host.sessions.add_message(sid, 'assistant', '研究讨论或计划提案已取消，没有提交科学 RUN。', AgentMode.PLAN)

    async def prepare_plan(self, sid, content, parent, config):
        active = self._research_tasks.get(sid)
        if active is not None and not active.done():
            raise ValueError('research_already_active')
        from app.agent import runtime as env
        from app.orchestration.plan_proposal import propose_reviewed_plan
        task = asyncio.create_task(propose_reviewed_plan(self, sid, content, parent, config, env.WORKSPACE_ROOT),
                                   name='scientific-plan-proposal-' + sid)
        self._research_tasks[sid] = task
        return await task

    def rebind_draft(self, sid):
        from app.orchestration.plan_binding import bind_supported_draft
        from app.scientific.schemas import ScientificPlan
        current = self.host.sessions.get(sid).get('plan')
        if not current or current.get('runnable'):
            raise ValueError('only_unapproved_drafts_can_be_rebound')
        run = self.host.sessions.latest_run(sid)
        if run and run['outcome'] is None:
            raise ValueError('active_scientific_run_cannot_be_rebound')
        plan = ScientificPlan.model_validate(current)
        bound = bind_supported_draft(plan)
        if bound.model_dump(mode='json') == plan.model_dump(mode='json'):
            return current
        bound.revision = plan.revision + 1
        report = self.host._validate_plan(bound)
        bound.precheck_summary = report.model_dump(mode='json') if report else None
        self.host.sessions.commit_plan_revision(sid, bound.model_dump(mode='json'), plan.revision)
        self.event(sid, 'plan_adapter_rebound', previous_revision=plan.revision, plan_revision=bound.revision,
            workflow_id=bound.workflow_id, scientific_status='NOT_RUN', model_requests=0, solver_submissions=0)
        return bound.model_dump(mode='json')

    async def shutdown_research(self):
        for task in self._research_tasks.values():
            if not task.done():
                task.cancel()
        if self._research_tasks:
            await asyncio.gather(*self._research_tasks.values(), return_exceptions=True)

    def reconcile_research(self):
        from app.orchestration.research_store import ResearchStore
        return ResearchStore(self.host.sessions).interrupt_active()

    async def plan(self, sid, content):
        """调用受限草案服务 / Call the bounded draft service."""
        discussion = None
        provider = self.host.providers
        configuration = provider.store.load() if hasattr(provider, 'store') else {}
        research_config = configuration.get('research', {})
        # 沿用现有本地优先策略 / Reuse the provider's local-first policy.
        use_model = bool(configuration.get('active_provider'))
        if research_config.get('enabled', False) and hasattr(provider, 'complete_metered') and not use_model:
            use_model = await provider.preferred() is not None
        if use_model and research_config.get('enabled', False) and hasattr(provider, 'complete_metered'):
            from app.orchestration.research_contracts import ResearchLimits
            discussion = await self.discuss(sid, content, request_key='plan-turn-' + uuid4().hex, limits=ResearchLimits.model_validate(
                {key: value for key, value in research_config.items() if key in ResearchLimits.model_fields}))
            if discussion['status'] not in {'READY_FOR_PLAN', 'NEEDS_EVIDENCE', 'DEGRADED'}:
                from app.models import AgentMode, AgentState
                current = self.host.sessions.get(sid).get('plan')
                if current:
                    from app.scientific.schemas import ScientificPlan
                    draft = ScientificPlan.model_validate(current)
                    previous = draft.revision
                    draft.revision += 1
                    draft.runnable = False
                    draft.planning_status = 'DRAFT_NEEDS_INPUT'
                    draft.missing_information = list(dict.fromkeys([*draft.missing_information,
                        'Research evidence or budget unresolved; see case ' + discussion['case_id']]))
                    self.host.sessions.commit_plan_revision(sid, draft.model_dump(mode='json'), previous)
                self.host.sessions.add_message(sid, 'assistant',
                    '科研讨论尚需补证或处理预算限制，未生成可 RUN 的计划。状态：' + discussion['status'],
                    AgentMode.PLAN, research_case_id=discussion['case_id'], research_outcome=discussion['outcome'])
                self.host.sessions.set_state(sid, AgentState.PLAN_READY)
                return
            return await self.prepare_plan(sid, content, discussion, research_config)
        session = self.host.sessions.get(sid)
        scope = ExpertScope(project_id=session['project_id'], session_id=sid, case_id=uuid4().hex,
            plan_id=(session.get('plan') or {}).get('plan_id', 'planning'),
            plan_revision=(session.get('plan') or {}).get('revision', 1), run_id='planning-' + uuid4().hex,
            attempt_id=uuid4().hex)
        ref = EvidenceRef(schema_version=1, evidence_id='research-request', kind='reference',
            source_ref='user.request', locator=hashlib.sha256(content.encode()).hexdigest(), applicability='this planning turn')
        async def propose(task, _):
            if task.scope != scope:
                raise PermissionError('planning_scope_mismatch')
            if discussion is not None:
                current = self.host.sessions.get(sid).get('plan') or {}
                if discussion['goal'].get('session_plan_binding') != [current.get('plan_id'), current.get('revision')]:
                    raise ValueError('research_plan_binding_changed')
            await self.host._plan(sid, content)
        tool = 'planning.propose'
        scheduler = ExpertScheduler(capabilities={'research_planning': (tool,)},
            tools={tool: ExpertTool(Contract, propose)}, max_concurrency=1)
        task = ExpertTask(schema_version=1, scope=scope, task_id='plan-proposal', capability='research_planning',
            objective='Produce a ScientificPlan proposal; execution requires separate approval',
            requested_tools=(tool,), input_evidence_refs=(ref.evidence_id,), budget_estimate=Budget(agent_calls=1, solver_runs=0))
        async def execute(context):
            await context.call_tool(tool, {})
            return {'schema_version': 1, 'scope': scope.model_dump(), 'task_id': task.task_id,
                    'summary': 'Planning proposal returned', 'claims': []}
        from app.agent import runtime as env
        env.WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        self.event(sid, 'planning_dispatched', scope=scope.model_dump())
        batch = await scheduler.run(ExpertIntake(scope=scope, tasks=[task.model_dump()], evidence=(ref,)),
            workspace_root=env.WORKSPACE_ROOT, execute=execute)
        if batch.failures:
            raise RuntimeError('planning_expert_failed')

    async def start(self, sid):
        accepted = await self.host._prepare_run(sid)
        if accepted is None:
            return
        plan, run_id = accepted
        self.host._pending_runs[sid] = run_id
        self.event(sid, 'run_admitted', run_id=run_id, plan_id=plan.plan_id, authority='research_controller')
        task = asyncio.create_task(self.execute(sid, plan), name='research-controller-' + sid)
        self.host._tasks[sid] = task

    async def execute(self, sid, plan):
        from app.agent import runtime as env
        store = self.host.sessions
        run_id = self.host._pending_runs.pop(sid, None)
        if run_id is None:
            run_id = str(uuid4())
            store.begin_run(sid, run_id, plan.model_dump(mode='json'))
        frame = self.services.frame(sid, plan, run_id)
        self.event(sid, 'execution_started', run_id=run_id, authority='research_controller')
        try:
            if not await self.services.precheck(frame):
                return
            frame.attempt_id = store.begin_attempt(run_id)
            checkpoint, vault, review = await self._coordinate(frame)
            review_ref = next(ref for ref in checkpoint.catalog() if ref.evidence_id == 'final-review')
            frame.execution['artifacts']['expert_review'] = str(vault.root / review_ref.locator)
            frame.execution['expert_review'] = vault.read(review_ref)
            store.update_run(run_id, frame.attempt_id, artifacts=frame.execution['artifacts'])
            # 最终成功仍由原科学账本发布；检查点 SETTLED 本身不代表科学通过。
            await self.services.publish(frame)
            self.event(sid, 'completed', run_id=run_id, attempt_id=frame.attempt_id,
                       expert_review=review.status if review else 'existing_domain_validation')
        except (Exception, asyncio.CancelledError) as exc:
            if getattr(frame, 'coordinator_scope', None):
                store.stop_checkpoint(frame.coordinator_scope, reason='cancelled' if
                    isinstance(exc, asyncio.CancelledError) or store.get_run(run_id)['cancel_requested'] else 'task_failed')
            if requires_commit_readback(exc) and frame.attempt_id:
                store.update_run(run_id, frame.attempt_id, cleanup={'status': 'orphaned', 'commit_state': 'UNKNOWN_COMMIT', 'reaped': False})
            self.event(sid, 'stopped', run_id=run_id, attempt_id=frame.attempt_id,
                       reason=type(exc).__name__, automatic_solver_replay=False)
            if not isinstance(exc, asyncio.CancelledError) and not store.get_run(run_id)['cancel_requested']:
                try:
                    from app.recovery.ingest import Signal, capture, attached_signals
                    from app.recovery.schemas import Origin, Cause
                    from app.recovery.service import RecoveryService
                    signal = Signal(Origin.SCIENCE if isinstance(exc, env.ScientificValidationError) else Origin.RUNTIME,
                        'scientific_controller', Cause.EVIDENCE_INVALID if isinstance(exc, env.ScientificValidationError) else 'runtime_failed')
                    identity = dict(session_id=sid, run_id=run_id, attempt_id=frame.attempt_id,
                        plan_id=frame.plan.plan_id, plan_revision=frame.plan.revision)
                    if getattr(exc, '_sky_expert_scope', None) == getattr(frame, 'coordinator_scope', None):
                        identity.update(expert_task_id=getattr(exc, '_sky_expert_task_id', None),
                                        expert_attempt_id=frame.attempt_id)
                    case_id = capture(store, signal, **identity)
                    for related in attached_signals(exc):
                        capture(store, related, **identity, correlation=signal.event_id, parent=signal.event_id)
                    if case_id:
                        recovery = RecoveryService(store, env.WORKSPACE_ROOT / 'recovery-experts')
                        self.event(sid, 'recovery_dispatched', run_id=run_id, case_id=case_id)
                        await recovery.diagnose(case_id, store.get(sid)['project_id'], sid, allow_model=False)
                        exc._sky_controller_captured = True
                except Exception:
                    self.event(sid, 'recovery_unavailable', run_id=run_id, authority='diagnostic_only')
            await self.services.fail(frame, exc)

    async def _coordinate(self, frame, *, recertified=False):
        from app.agent import runtime as env
        store = self.host.sessions
        sid, plan, run_id = frame.session_id, frame.plan, frame.run_id
        scope = ExpertScope(project_id=store.get(sid)['project_id'], session_id=sid, case_id=run_id + '-r' + str(plan.revision),
            plan_id=plan.plan_id, plan_revision=plan.revision, run_id=run_id, attempt_id=frame.attempt_id)
        vault = EvidenceVault(env.WORKSPACE_ROOT, scope)
        cooling = plan.workflow_id == 'heat_transfer_3d_cooling'
        if cooling:
            from app.validators.physics.cooling_audit import MODEL_SOURCE as model_source, FIELD_SOURCE as field_source, HISTORY_SOURCE
        else:
            model_source, field_source = MODEL_SOURCE, FIELD_SOURCE
        readback_tool = 'comsol.read_saved_cooling' if cooling else 'comsol.read_saved_heat'
        outputs, produced = {}, {}
        reference = EvidenceRef(schema_version=1, evidence_id='approved-plan', kind='reference',
            source_ref='session.approved_scientific_plan', locator=hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
            applicability='this approved revision only')
        def rule(name, capability, dependencies=(), refs=()):
            tools = {'execution': ('scientific.execute_once',), 'acquisition': (readback_tool,),
                     'inspection': (), 'review': ('evidence.read',), 'challenge': ('evidence.read',)}[capability]
            return TaskRule(task=ExpertTask(schema_version=1, scope=scope, task_id=name, capability=capability,
                objective=name, dependencies=tuple(dependencies), input_evidence_refs=tuple(refs),
                requested_tools=tools, budget_estimate=Budget(agent_calls=0, solver_runs=int(capability == 'execution' and not recertified))))
        initial = rule('inspect-approved-plan', 'inspection', refs=(reference.evidence_id,))
        checkpoint = store.create_checkpoint(scope, (initial,), Budget(agent_calls=0, solver_runs=int(not recertified)), (reference,))
        frame.coordinator_scope = scope

        def guard(task):
            if task.scope != scope:
                raise PermissionError('foreign_controller_scope')
            store.assert_current(run_id, frame.attempt_id)
            store.assert_plan_binding(sid, frame.plan.model_dump(mode='json'))

        async def solve(task, _):
            guard(task)
            if recertified:
                raise PermissionError('recertification_has_no_solver_budget')
            await self.services.execute_once(frame)
            await self.services.validate(frame)
            if not frame.verdict.completed:
                raise env.ScientificValidationError('; '.join(frame.verdict.issues))
            return True

        async def read_saved(task, _):
            guard(task)
            async with asyncio.timeout(self.readback_timeout_seconds):
                value = await self.host.tools.execute(readback_tool, {
                    'run_id': run_id, 'attempt_id': frame.attempt_id, 'session_id': sid,
                    'plan': frame.plan.execution_payload(), 'workspace': str(env.WORKSPACE_ROOT)},
                    lambda event: self.host._handle_attempt_event(sid, run_id, frame.attempt_id, event), retries=0)
            guard(task)
            expected = frame.execution['evidence_contract']['artifacts']['model']['sha256']
            if value.get('run_id') != run_id or value.get('attempt_id') != frame.attempt_id:
                raise ValueError('independent_readback_identity_mismatch')
            if value.get('model_sha256') != expected or value.get('solver_calls') != 0 or value.get('saved_model_unchanged') is not True:
                raise ValueError('independent_readback_producer_or_integrity_mismatch')
            if cooling:
                from app.orchestration.cooling_evidence import read_histories
                value['histories'] = read_histories(frame.execution,
                    env.WORKSPACE_ROOT / 'models' / 'attempts' / run_id / frame.attempt_id)
            outputs['independent'] = value
            return True

        async def read_evidence(task, args):
            guard(task)
            current = store.read_checkpoint(scope)
            visible = set(task.input_evidence_refs)
            for dependency in task.dependencies:
                item = current.verified[dependency]
                visible.update(ref.evidence_id for ref in item.evidence)
                visible.update(ref for claim in item.result.claims for ref in claim.evidence_refs)
            if args.evidence_id not in visible:
                raise PermissionError('cross_expert_evidence_not_admitted')
            ref = next(ref for ref in current.catalog() if ref.evidence_id == args.evidence_id)
            return vault.read(ref)

        tools = {'scientific.execute_once': ExpertTool(Contract, solve, requires_run=True, exclusive_resource='COMSOL'),
            readback_tool: ExpertTool(Contract, read_saved, exclusive_resource='COMSOL'),
            'evidence.read': ExpertTool(ReadEvidence, read_evidence)}
        scheduler = ExpertScheduler(capabilities={cap: names for cap, names in (
            ('inspection', ()), ('execution', ('scientific.execute_once',)),
            ('acquisition', (readback_tool,)), ('review', ('evidence.read',)),
            ('challenge', ('evidence.read',)))}, tools=tools, max_concurrency=3,
            authorize_run=lambda task, *_: task.scope == scope and store.attempt_is_current(run_id, frame.attempt_id))
        coordinator = RevisionCoordinator(store, scheduler)

        def freeze(task_id, evidence_id, source, data):
            item = vault.freeze(evidence_id, source, data)
            produced.setdefault(task_id, []).append(item.ref)
            return item

        async def expert(context):
            guard(context.task)
            name = context.task.task_id
            claims = []
            refs = [ref.evidence_id for ref in context.evidence]
            if context.task.capability == 'inspection':
                value = {'workflow': plan.workflow_id, 'plan_revision': plan.revision,
                         'requires_independent_field': plan.workflow_id == 'heat_transfer_2d_stationary'}
                outputs[name] = value
                freeze(name, 'plan-inspection', 'scientific.plan_inspection', value)
            elif context.task.capability == 'execution':
                if not recertified:
                    await context.call_tool('scientific.execute_once', {})
                freeze(name, 'worker-execution', 'runtime.execution_verdict', {
                    'verdict': frame.verdict.as_dict(), 'evidence_contract': frame.execution['evidence_contract']})
            elif context.task.capability == 'acquisition':
                await context.call_tool(readback_tool, {})
                value = outputs['independent']
                freeze(name, 'heat-model', model_source, value['model_readback'])
                freeze(name, 'heat-field', field_source, value['field_readback'])
                if cooling:
                    freeze(name, 'cooling-histories', HISTORY_SOURCE, value['histories'])
            elif name == 'identify-evidence-gaps':
                await context.call_tool('evidence.read', {'evidence_id': 'worker-execution'})
                current = store.read_checkpoint(scope)
                sources = {ref.source_ref for ref in current.catalog()}
                missing = [label for label, source in (('independent_model', model_source), ('independent_field', field_source))
                           if source not in sources] if plan.workflow_id in {'heat_transfer_2d_stationary','heat_transfer_3d_cooling'} else []
                preliminary = self.heat_review(scope, frame, current, vault, visible=set(refs)) if missing and not cooling else None
                outputs[name] = {'missing': missing, 'review_status': preliminary.status if preliminary else 'DOMAIN_VALIDATION',
                                 'reasons': list(preliminary.reasons) if preliminary else []}
                if preliminary:
                    freeze(name, 'review-before-rework', 'scientific.independent_review', preliminary.model_dump(mode='json'))
                freeze(name, 'evidence-gaps', 'scientific.evidence_requirements', outputs[name])
            elif name in {'challenge-model', 'challenge-numerics'}:
                model = await context.call_tool('evidence.read', {'evidence_id': 'heat-model'})
                field = await context.call_tool('evidence.read', {'evidence_id': 'heat-field'})
                if cooling:
                    from app.validators.physics.cooling_audit import audit_cooling
                    histories = await context.call_tool('evidence.read', {'evidence_id': 'cooling-histories'})
                    checks = audit_cooling(plan, model, field, histories)['checks']
                    outputs[name] = {k:v for k,v in checks.items() if k.startswith(('model_','boundary_','parameter_')) == (name=='challenge-model')}
                else:
                    target = self.benchmark(scope, plan)
                    checks = (check_model(target, HeatModelReadback.model_validate(model), 'heat-model') if name == 'challenge-model'
                              else check_field(target, HeatFieldReadback.model_validate(field), 'heat-field'))
                    outputs[name] = {key: check.model_dump() for key, check in checks.items()}
                freeze(name, name + '-evidence', 'scientific.counterevidence', outputs[name])
            elif name == 'mesh-refinement-assessment':
                value = await context.call_tool('evidence.read', {'evidence_id': 'challenge-numerics-evidence'})
                freeze(name, 'mesh-assessment', 'scientific.revision_advice', {
                    'reason': 'Numerical disagreement requires investigation and new approval before any solver rerun',
                    'observations': value, 'solver_authorized': False})
            elif name == 'independent-review':
                for ref in context.evidence:
                    if ref.kind == 'run_artifact':
                        await context.call_tool('evidence.read', {'evidence_id': ref.evidence_id})
                report = self.heat_review(scope, frame, store.read_checkpoint(scope), vault, visible=set(refs)) if plan.workflow_id == 'heat_transfer_2d_stationary' else None
                if cooling:
                    from app.execution.cooling_review import review_cooling
                    catalog = store.read_checkpoint(scope).catalog()
                    artifacts = [vault.freeze(ref.evidence_id,ref.source_ref,vault.read(ref)) for ref in catalog
                        if ref.evidence_id in refs and ref.source_ref in {model_source,field_source,HISTORY_SOURCE}]
                    report = review_cooling(scope,plan,artifacts,frame.verdict)
                outputs['review'] = report
                freeze(name, 'final-review', 'scientific.independent_review', report.model_dump(mode='json') if report else {
                    'status': 'PASS' if frame.verdict.completed else 'BLOCKED', 'basis': 'existing_domain_validator',
                    'verdict': frame.verdict.as_dict()})
            else:
                raise ValueError('unregistered_expert_task')
            if refs:
                claims = [{'claim_id': name, 'statement': 'Scoped expert findings', 'evidence_refs': refs}]
            return {'schema_version': 1, 'scope': scope.model_dump(), 'task_id': name,
                    'summary': name, 'claims': claims}

        async def dispatch():
            result = await coordinator.dispatch(scope, workspace_root=env.WORKSPACE_ROOT, execute=expert,
                admit_result=lambda result: tuple(produced.get(result.task_id, ())),
                raise_on_failure=True,
                pause_before=lambda saved: not outputs.get('numerics_routed') and 'challenge-numerics' in saved.verified and
                    'mesh-refinement-assessment' in saved.pending)
            if result.status == 'STOPPED':
                raise RuntimeError('controller_stopped:' + str(result.stop_reason))
            return result

        checkpoint = await dispatch()
        postprocessed = recertified
        for _ in range(20):
            if not postprocessed and 'execute-reviewed-plan' in checkpoint.verified:
                previous_revision = frame.plan.revision
                await self.services.postprocess(frame)
                postprocessed = True
                if frame.plan.revision != previous_revision:
                    # 人工修正经原有审核入口确认后，旧图退休；从新输入重新取证。
                    store.stop_checkpoint(scope, reason='plan_changed')
                    self.event(sid, 'reviewed_plan_rebound', run_id=run_id,
                        previous_revision=previous_revision, plan_revision=frame.plan.revision,
                        additional_controller_solver_runs=0)
                    return await self._coordinate(frame, recertified=True)
            decision = next_decision(checkpoint, outputs)
            if 'challenge-numerics' in checkpoint.verified and 'mesh-refinement-assessment' in checkpoint.pending:
                outputs['numerics_routed'] = True
            if decision.finish:
                break
            if any(name == 'acquire-independent-readback' for name, _, _ in decision.additions):
                self.event(sid, 'revision_proposed', run_id=run_id, attempt_id=frame.attempt_id,
                    reason='missing_independent_physical_evidence', evidence_refs=list(decision.evidence_refs),
                    affected_tasks=[name for name, _, _ in decision.additions], solver_runs=0)
            checkpoint = store.extend_checkpoint(scope,
                tuple(rule(name, capability, dependencies) for name, capability, dependencies in decision.additions),
                decision.cancellations, reason=decision.reason, evidence_refs=decision.evidence_refs)
            if checkpoint.status == 'STOPPED':
                raise RuntimeError('controller_budget_exhausted')
            if checkpoint.status == 'ACTIVE':
                checkpoint = await dispatch()
        else:
            raise RuntimeError('controller_no_progress_limit')
        review = outputs.get('review')
        if review is not None and review.status != 'PASS':
            raise env.ScientificValidationError('independent_review:' + review.status + ':' + ';'.join(review.reasons))
        return checkpoint, vault, review

    @staticmethod
    def benchmark(scope, plan):
        def value(name):
            item = plan.parameters[name]
            return float(item.normalized_value if item.normalized_value is not None else item.value)
        def length(name):
            item = plan.parameters[name]
            return value(name) * {'m': 1., 'cm': .01, 'mm': .001}[item.normalized_unit or item.unit]
        return HeatBenchmark(schema_version=1, scope=scope, width_m=length('width'), height_m=length('height'),
            conductivity_W_m_K=value('thermal_conductivity'), left_K=value('left_temperature'), right_K=value('right_temperature'))

    def heat_review(self, scope, frame, checkpoint, vault, *, visible):
        artifacts = []
        for ref in checkpoint.catalog():
            if ref.evidence_id in visible and ref.source_ref in {MODEL_SOURCE, FIELD_SOURCE}:
                payload = vault.read(ref)
                artifacts.append(vault.freeze(ref.evidence_id, ref.source_ref, payload))
        refs = {'goal_alignment': (BENCHMARK_ID, 'heat-model'),
            'scientific_correctness': (BENCHMARK_ID, 'heat-model', 'heat-field'),
            'evidence': ('heat-model', 'heat-field', RECEIPT_ID),
            'reproducibility': ('heat-model', RECEIPT_ID), 'delivery': ('heat-field', RECEIPT_ID)}
        from app.execution.expert_contracts import HEAT_RUBRIC_WEIGHTS
        findings = {}
        for artifact in artifacts:
            payload = json.loads(artifact.data)
            findings.update(check_model(self.benchmark(scope, frame.plan), HeatModelReadback.model_validate(payload), artifact.ref.evidence_id)
                            if artifact.ref.source_ref == MODEL_SOURCE else
                            check_field(self.benchmark(scope, frame.plan), HeatFieldReadback.model_validate(payload), artifact.ref.evidence_id))
        checks_by_criterion = {'goal_alignment': ('model_geometry', 'boundary_selection'),
            'scientific_correctness': ('material_and_source', 'boundary_selection', 'temperature', 'heat_flux'),
            'evidence': ('model_geometry', 'temperature'), 'reproducibility': ('material_and_source',),
            'delivery': ('temperature', 'heat_flux')}
        score = {'schema_version': 1, 'scope': scope.model_dump(), 'rubric_version': 'heat-v1',
            'dimensions': [{'criterion': name, 'points': maximum if all(key in findings and findings[key].status == 'PASS' for key in checks_by_criterion[name]) else 0,
                            'reason': '按独立观测逐项判分，保留冲突，不以文字修改提高分数',
                            'evidence_refs': refs[name]} for name, maximum in HEAT_RUBRIC_WEIGHTS.items()]}
        return review_heat(scope=scope, scientific_run_id=scope.run_id, scientific_attempt_id=scope.attempt_id,
            benchmark=self.benchmark(scope, frame.plan), artifacts=artifacts,
            receipt=ExecutionReceipt(scope, scope.attempt_id, frame.verdict),
            tasks=tuple(rule.task for rule in checkpoint.rules), score_payload=score,
            references=checkpoint.references,
            task_by_criterion={name: 'independent-review' for name in refs})
