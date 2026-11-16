import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest

from app.execution.expert_contracts import ArtifactOrigin, Budget, Contract, EvidenceRef, ExpertResult, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.execution.heat_review import ExecutionReceipt, review_heat
from app.execution.revision_checkpoint import TaskRule
from app.execution.revision_coordinator import RevisionCoordinator
from app.execution.revision_store import CheckpointConflict
from app.session.store import SessionStore
from app.validators.execution_verdict import ExecutionVerdict
from app.validators.physics.heat_benchmark import FIELD_SOURCE, HeatBenchmark
from test_heat_review import ATTEMPT, FIELD, MODEL, RUN, WORKFLOW, freeze, inputs, score

CHAIN = ('build', 'solve', 'validate', 'export')
ALL = ('research', *CHAIN)
PARAMETER = 'parameters.conductivity'


def make_case(tmp_path, *, calls=12, runs=3, solver_tool=False):
    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    sid = store.create('project')['id']
    scope = ExpertScope(project_id='project', session_id=sid, case_id='heat', plan_id='heat-plan',
                        plan_revision=1, run_id='expert-run', attempt_id='initial-attempt')
    plan = {'plan_id': scope.plan_id, 'revision': 1, 'runnable': True, 'workflow_id': WORKFLOW,
            'parameters': {'conductivity': {'value': 20, 'unit': 'W/(m*K)'}}}
    store.commit_plan_revision(sid, plan, 0)
    basis = EvidenceRef(schema_version=1, kind='reference', evidence_id='material-source', source_ref='fixture',
                        locator='material', applicability='Synthetic Heat')
    rules = tuple(TaskRule(task=ExpertTask(
        schema_version=1, scope=scope, task_id=name, capability='heat_fixture', objective=name,
        dependencies=() if index <= 1 else (ALL[index - 1],),
        input_evidence_refs=(basis.evidence_id,) if index <= 1 else (),
        requested_tools=('heat.run',) if name == 'solve' and solver_tool else (),
        budget_estimate=Budget(agent_calls=1, solver_runs=int(name == 'solve')),
    ), plan_inputs=(PARAMETER,) if name == 'build' else (),
       review_targets=('delivery',) if name == 'export' else ()) for index, name in enumerate(ALL))
    checkpoint = store.create_checkpoint(scope, rules, Budget(agent_calls=calls, solver_runs=runs), (basis,))
    return store, coordinator(store), checkpoint


def coordinator(store):
    return RevisionCoordinator(store, ExpertScheduler(capabilities={'heat_fixture': ()}, tools={}, max_concurrency=3))


def result(scope, task_id, refs=()):
    return ExpertResult(schema_version=1, scope=scope, task_id=task_id, summary='Simulated observed result',
                        claims=[{'claim_id': 'observation', 'statement': 'Synthetic readback',
                                 'evidence_refs': refs}])


def admitted(item):
    evidence_id = f'{item.task_id}-{item.scope.attempt_id}'
    return (EvidenceRef(schema_version=1, kind='run_artifact', evidence_id=evidence_id, source_ref='fixture',
                        locator=item.task_id, applicability='Synthetic output',
                        artifact=ArtifactOrigin(run_id=RUN, attempt_id=item.scope.attempt_id,
                                                sha256=sha256(evidence_id.encode()).hexdigest())),)


async def dispatch(service, checkpoint, tmp_path, log):
    async def execute(context):
        task = context.task
        log.append(task.task_id)
        assert tuple(dep.task_id for dep in context.dependencies) == task.dependencies
        for dep in context.dependencies:
            assert admitted(dep)[0] in context.evidence
        refs = tuple(ref.evidence_id for ref in context.evidence)
        return result(task.scope, task.task_id, refs).model_dump()

    return await service.dispatch(checkpoint.scope, workspace_root=tmp_path, execute=execute, admit_result=admitted)


def heat_audit(checkpoint, inputs, *, plan_edit=False):
    scope = checkpoint.scope
    verdict = ExecutionVerdict(RUN, WORKFLOW, True, True, True, (), True)
    if plan_edit:
        inputs['model']['conductivity_W_m_K'] = 19
    audit = review_heat(
        scope=scope, scientific_run_id=RUN, scientific_attempt_id=ATTEMPT,
        benchmark=HeatBenchmark(scope=scope, **inputs['benchmark']),
        artifacts=(freeze(inputs['model'], scope=scope),
                   freeze(inputs['field'], source=FIELD_SOURCE, evidence_id=FIELD, scope=scope)),
        receipt=ExecutionReceipt(scope, ATTEMPT, verdict), tasks=tuple(
            checkpoint.task(rule.task.task_id) for rule in checkpoint.rules),
        score_payload=score(100 if plan_edit else 94, scope=scope), task_by_criterion={'delivery': 'export'},
        references=checkpoint.references,
    )
    assert audit.status == ('BLOCKED' if plan_edit else 'REVISION_REQUIRED')
    return audit


def edit_proposal(checkpoint):
    from app.execution.expert_contracts import RevisionProposal

    return RevisionProposal(
        schema_version=1, revision_id='correct-conductivity', scope=checkpoint.scope,
        issues=[{'schema_version': 1, 'issue_id': 'material', 'claim_id': 'material_and_source',
                 'category': 'counterevidence', 'reason': 'Direct conductivity readback disagrees with the plan',
                 'evidence_refs': [MODEL], 'affected_task_ids': ['build']}],
        actions=[{'task_id': 'build', 'action': 'revise_plan', 'target': PARAMETER,
                  'expected_evidence': 'Readback with approved conductivity'}],
        budget_estimate=Budget(agent_calls=0, solver_runs=0),
    )


def save_conductivity(store, checkpoint, *, value=25, bound=True):
    plan = {**checkpoint.plan, 'revision': checkpoint.scope.plan_revision + 1,
            'parameters': {'conductivity': {'value': value, 'unit': 'W/(m*K)'}}}
    if bound:
        store.commit_plan_revision(checkpoint.scope.session_id, plan, checkpoint.scope.plan_revision)
    else:
        store.set_plan(checkpoint.scope.session_id, plan)


@pytest.mark.asyncio
async def test_conductivity_revision_invalidates_only_consumers_and_keeps_original_provenance(tmp_path, inputs):
    store, service, checkpoint = make_case(tmp_path)
    initial = await dispatch(service, checkpoint, tmp_path, [])
    assert initial.status == 'SETTLED' and initial.spent == Budget(agent_calls=5, solver_runs=1)
    review = heat_audit(initial, inputs, plan_edit=True)
    proposal = edit_proposal(initial)
    with pytest.raises(CheckpointConflict, match='next_plan_revision_required'):
        service.approve_revision(initial.scope, proposal, review)
    save_conductivity(store, initial)

    # 即使两个总控请求同时复核，同一组预算和尝试也只能提交一次。
    reopened = SessionStore(store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approvals = [pool.submit(worker.approve_revision, initial.scope, proposal, review)
                     for worker in (service, coordinator(reopened))]
    revisions = []
    for approval in approvals:
        try:
            revisions.append(approval.result())
        except CheckpointConflict as error:
            assert str(error) == 'stale_coordinator_attempt'
    revised, = revisions
    assert revised.pending == CHAIN and revised.scope.plan_revision == 2
    assert revised.reserved() == Budget(agent_calls=4, solver_runs=1)
    assert revised.verified == {'research': initial.verified['research']}
    assert revised.verified['research'].result.scope.plan_revision == 1
    assert revised.references == initial.references
    old = {ref.evidence_id for name in CHAIN for ref in initial.verified[name].evidence}
    assert old.isdisjoint(ref.evidence_id for ref in revised.catalog())
    with pytest.raises(CheckpointConflict, match='stale_coordinator_attempt'):
        store.publish_checkpoint_task(result(initial.scope, 'solve'))
    log = []
    final = await dispatch(service, revised, tmp_path, log)
    assert tuple(log) == CHAIN and final.status == 'SETTLED'
    assert final.spent == Budget(agent_calls=9, solver_runs=2)
    assert final.verified['research'] == initial.verified['research']
    events = store.get(initial.scope.session_id)['events']
    approved, = [event['payload'] for event in events if event['type'] == 'experts.revision_approved']
    assert set(approved['invalidated_evidence']) == old
    assert approved['changed_paths'] == ['parameters.conductivity.value']


@pytest.mark.asyncio
async def test_rejects_unbound_plan_and_halts_when_revision_cannot_be_afforded(tmp_path, inputs):
    store, service, checkpoint = make_case(tmp_path, calls=8, runs=2)
    initial = await dispatch(service, checkpoint, tmp_path, [])
    review = heat_audit(initial, inputs, plan_edit=True)
    proposal = edit_proposal(initial)
    save_conductivity(store, initial, bound=False)
    with pytest.raises(ValueError, match='unbound'):
        service.approve_revision(initial.scope, proposal, review)
    store.set_plan(initial.scope.session_id, initial.plan)
    save_conductivity(store, initial)
    stopped = service.approve_revision(initial.scope, proposal, review)
    assert stopped.status == 'STOPPED' and stopped.stop_reason == 'budget_exhausted'
    assert stopped.spent == initial.spent and stopped.reserved() == Budget(agent_calls=0, solver_runs=0)
    assert set(stopped.verified) == {'research'}
    with pytest.raises(CheckpointConflict, match='dispatch_unavailable'):
        await dispatch(service, stopped, tmp_path, [])


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['no_effect', 'unidentifiable', 'repeated_no_progress'])
async def test_stops_when_evidence_does_not_identify_productive_revision(tmp_path, inputs, reason):
    store, service, checkpoint = make_case(tmp_path)
    initial = await dispatch(service, checkpoint, tmp_path, [])
    if reason == 'no_effect':
        review = heat_audit(initial, inputs, plan_edit=True)
        save_conductivity(store, initial, value=20)
        stopped = service.approve_revision(initial.scope, edit_proposal(initial), review)
    elif reason == 'unidentifiable':
        review = heat_audit(initial, inputs)
        action, = review.revision.actions
        proposal = review.revision.model_copy(update={'actions': (action.model_copy(update={'target': 'unknown'}),)})
        stopped = service.approve_revision(initial.scope, proposal, review)
    else:
        review = heat_audit(initial, inputs)
        retry = service.approve_revision(initial.scope, review.revision, review)
        assert retry.pending == ('export',)
        log = []
        settled = await dispatch(service, retry, tmp_path, log)
        assert log == ['export']
        review = heat_audit(settled, inputs)
        new_id = review.revision.model_copy(update={'revision_id': 'same-effect-new-id'})
        stopped = service.approve_revision(settled.scope, new_id, review)
    assert stopped.status == 'STOPPED' and stopped.stop_reason == reason
    assert not stopped.pending and not stopped.ready()


@pytest.mark.asyncio
async def test_restart_resumes_only_unclaimed_work_and_rejects_old_results(tmp_path):
    store, _, checkpoint = make_case(tmp_path)
    token = store.begin_checkpoint_dispatch(checkpoint.scope)
    store.claim_checkpoint_task(checkpoint.scope, 'research', token=token)
    original = result(checkpoint.scope, 'research', ('material-source',))
    store.publish_checkpoint_task(original, admitted(original), token=token)
    restarted = SessionStore(store.path)
    restarted.initialize()
    with pytest.raises(KeyError):
        restarted.load_checkpoint('different-project', checkpoint.scope.session_id, 'heat')
    saved = restarted.load_checkpoint('project', checkpoint.scope.session_id, 'heat')
    current = restarted.resume_checkpoint(saved.scope)
    assert current.scope.attempt_id != checkpoint.scope.attempt_id
    with pytest.raises(CheckpointConflict, match='stale_coordinator_attempt'):
        store.publish_checkpoint_task(original, token=token)
    log = []
    complete = await dispatch(coordinator(restarted), current, tmp_path, log)
    assert tuple(log) == CHAIN and complete.status == 'SETTLED'
    assert complete.verified['research'].result == original


@pytest.mark.asyncio
async def test_interrupted_solve_is_not_submitted_twice(tmp_path):
    store, service, checkpoint = make_case(tmp_path)
    solving = asyncio.Event()
    submit_count = 0

    async def execute(context):
        nonlocal submit_count
        if context.task.task_id == 'solve':
            submit_count += 1
            solving.set()
            await asyncio.Future()
        return result(context.task.scope, context.task.task_id).model_dump()

    pending = asyncio.create_task(service.dispatch(
        checkpoint.scope, workspace_root=tmp_path, execute=execute, admit_result=admitted))
    await asyncio.wait_for(solving.wait(), 3)
    with pytest.raises(CheckpointConflict, match='dispatch_unavailable'):
        await dispatch(coordinator(SessionStore(store.path)), checkpoint, tmp_path, [])
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    reopened = SessionStore(store.path)
    reopened.initialize()
    stopped = reopened.resume_checkpoint(checkpoint.scope)
    assert stopped.stop_reason == 'submission_unknown' and stopped.in_flight == ('solve',)
    assert stopped.spent.solver_runs == 1 and stopped.reserved().solver_runs == 0
    with pytest.raises(CheckpointConflict, match='stale_coordinator_attempt'):
        store.publish_checkpoint_task(result(checkpoint.scope, 'solve'))
    with pytest.raises(CheckpointConflict, match='dispatch_unavailable'):
        await dispatch(coordinator(reopened), stopped, tmp_path, [])
    assert submit_count == 1


def test_cancellation_revokes_publish_and_returns_unstarted_budget(tmp_path):
    store, _, checkpoint = make_case(tmp_path)
    store.claim_checkpoint_task(checkpoint.scope, 'build')
    stopped = store.stop_checkpoint(checkpoint.scope)
    assert stopped.stop_reason == 'cancelled' and stopped.spent.agent_calls == 1
    assert stopped.reserved() == Budget(agent_calls=0, solver_runs=0)
    with pytest.raises(CheckpointConflict, match='task_not_in_flight'):
        store.publish_checkpoint_task(result(checkpoint.scope, 'build'))


def test_restart_after_last_receipt_clears_abandoned_dispatch_ownership(tmp_path):
    store, _, checkpoint = make_case(tmp_path)
    token = store.begin_checkpoint_dispatch(checkpoint.scope)
    for name in ALL:
        store.claim_checkpoint_task(checkpoint.scope, name, token=token)
        record = result(checkpoint.scope, name)
        store.publish_checkpoint_task(record, admitted(record), token=token)
    assert store.read_checkpoint(checkpoint.scope).status == 'SETTLED'
    restored = SessionStore(store.path).resume_checkpoint(checkpoint.scope)
    assert restored.status == 'SETTLED' and not restored.dispatch_token
    assert restored.scope.attempt_id != checkpoint.scope.attempt_id
    assert set(restored.verified) == set(ALL) and restored.spent.agent_calls == 5


@pytest.mark.asyncio
async def test_rejects_undeclared_scientific_change_even_when_name_resembles_registered_path(tmp_path, inputs):
    store, service, checkpoint = make_case(tmp_path)
    initial = await dispatch(service, checkpoint, tmp_path, [])
    audit = heat_audit(initial, inputs, plan_edit=True)
    plan = {**initial.plan, 'revision': 2, 'parameters': {
        'conductivity': {'value': 25, 'unit': 'W/(m*K)'}, 'conductivity.extra': {'value': 4},
    }}
    store.commit_plan_revision(initial.scope.session_id, plan, 1)
    stopped = service.approve_revision(initial.scope, edit_proposal(initial), audit)
    assert stopped.stop_reason == 'unidentifiable'
    assert stopped.spent == initial.spent and not stopped.pending


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['approved', 'no_approval', 'cancel_before_call'])
async def test_solver_broker_requires_current_claim_run_approval_and_available_call(tmp_path, scenario):
    store, _, checkpoint = make_case(tmp_path, solver_tool=True)
    solver_calls = []

    async def solve(task, args):
        solver_calls.append(task.task_id)

    scheduler = ExpertScheduler(
        capabilities={'heat_fixture': ('heat.run',)},
        tools={'heat.run': ExpertTool(Contract, solve, requires_run=True, exclusive_resource='fixture-solver')},
        max_concurrency=2, authorize_run=lambda *args: scenario != 'no_approval',
    )
    service = RevisionCoordinator(store, scheduler)

    async def execute(context):
        task = context.task
        if task.task_id == 'solve':
            if scenario == 'cancel_before_call':
                store.stop_checkpoint(task.scope)
                with pytest.raises(CheckpointConflict, match='task_claim_revoked'):
                    await context.call_tool('heat.run', {})
            elif scenario == 'no_approval':
                with pytest.raises(PermissionError, match='RUN authorization'):
                    await context.call_tool('heat.run', {})
                raise RuntimeError('simulated refusal')
            else:
                await context.call_tool('heat.run', {})
                with pytest.raises(PermissionError, match='reserved solver calls'):
                    await context.call_tool('heat.run', {})
        return result(task.scope, task.task_id).model_dump()

    final = await service.dispatch(checkpoint.scope, workspace_root=tmp_path, execute=execute, admit_result=admitted)
    if scenario == 'approved':
        assert final.status == 'SETTLED' and solver_calls == ['solve']
    else:
        assert not solver_calls and final.status == 'STOPPED' and 'solve' not in final.verified
