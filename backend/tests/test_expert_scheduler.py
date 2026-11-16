import asyncio

import pytest

from app.execution.expert_contracts import Contract, EvidenceRef, ExpertIntake, ExpertScope, Text
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool


SCOPE = ExpertScope(project_id='project', session_id='session', case_id='heat', plan_id='plan',
                    plan_revision=1, run_id='expert-run', attempt_id='expert-attempt')


class Query(Contract):
    query: Text


def request(task_id, *, capability='research', dependencies=(), tools=(), refs=()):
    return {
        'schema_version': 1, 'scope': SCOPE.model_dump(), 'task_id': task_id,
        'capability': capability, 'objective': 'Check the Heat case', 'dependencies': dependencies,
        'requested_tools': tools, 'input_evidence_refs': refs,
        'budget_estimate': {'agent_calls': 1, 'solver_runs': 0},
    }


def reference(ref):
    return EvidenceRef(schema_version=1, evidence_id=ref, kind='reference', source_ref=f'fixture:{ref}',
                       locator='center_temperature', applicability='Synthetic Heat fixture')


def admitted(*tasks, evidence=()):
    return ExpertIntake(scope=SCOPE, tasks=tasks, evidence=evidence)


def reply(context, *, refs=()):
    return {
        'schema_version': 1, 'scope': context.task.scope.model_dump(), 'task_id': context.task.task_id,
        'summary': 'Simulated expert result',
        'claims': [{'claim_id': context.task.task_id, 'statement': 'Synthetic observation', 'evidence_refs': refs}],
    }


def scheduler(*, max_concurrency=2, capabilities=None, tools=None, authorize_run=None):
    return ExpertScheduler(capabilities={'research': ()} if capabilities is None else capabilities,
                           tools=tools or {}, max_concurrency=max_concurrency, authorize_run=authorize_run)


@pytest.mark.parametrize('count', [1, 3, 8])
@pytest.mark.asyncio
async def test_dynamic_tasks_are_bounded_and_use_isolated_scratch(tmp_path, count):
    active = peak = 0

    async def fake(context):
        nonlocal active, peak
        assert context.dependencies == ()
        value = context.task.task_id.encode()
        context.write_scratch('notes.txt', value)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            assert context.read_scratch('notes.txt') == value
            with pytest.raises(FileExistsError):
                context.write_scratch('notes.txt', b'replacement')
            return reply(context)
        finally:
            active -= 1

    tasks = admitted(*(request(f'expert-{index}') for index in range(count)))
    batch = await scheduler().run(tasks, workspace_root=tmp_path, execute=fake)
    assert len(batch.results) == count and not batch.failures and not batch.blocked
    assert peak == min(2, count)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_ready_child_starts_without_waiting_for_unrelated_task(tmp_path):
    unrelated_started = asyncio.Event()
    child_started = asyncio.Event()
    returned = set()

    async def fake(context):
        name = context.task.task_id
        if name == 'root':
            await unrelated_started.wait()
        elif name == 'unrelated':
            unrelated_started.set()
            await child_started.wait()
        else:
            assert [record.task_id for record in context.dependencies] == ['root']
            assert 'root' in returned and 'unrelated' not in returned
            child_started.set()
        returned.add(name)
        return reply(context)

    tasks = admitted(request('root'), request('unrelated'), request('child', dependencies=('root',)))
    batch = await asyncio.wait_for(scheduler().run(tasks, workspace_root=tmp_path, execute=fake), timeout=5)
    assert set(batch.results) == {'root', 'unrelated', 'child'}


@pytest.mark.asyncio
async def test_invalid_predecessor_blocks_descendants_but_not_independent_task(tmp_path):
    started = set()

    async def fake(context):
        name = context.task.task_id
        started.add(name)
        response = reply(context)
        if name == 'invalid':
            response['scope']['attempt_id'] = 'old-attempt'
        return response

    tasks = admitted(request('invalid'), request('child', dependencies=('invalid',)),
                     request('leaf', dependencies=('child',)), request('independent'))
    batch = await scheduler().run(tasks, workspace_root=tmp_path, execute=fake)
    assert started == {'invalid', 'independent'}
    assert set(batch.results) == {'independent'}
    assert 'current expert scope' in batch.failures['invalid']
    assert batch.blocked == {'child': ('invalid',), 'leaf': ('child',)}


@pytest.mark.parametrize('deps,match', [(('missing',), 'Unknown task dependency'), (('root',), 'cycle')])
def test_invalid_dag_is_rejected_before_dispatch(deps, match):
    with pytest.raises(ValueError, match=match):
        admitted(request('root', dependencies=deps))


@pytest.mark.parametrize('case', ['unknown_capability', 'outside_capability'])
@pytest.mark.asyncio
async def test_bad_capability_batch_is_rejected_before_any_execution(tmp_path, case):
    async def unreachable(*_):
        pytest.fail('No executor or tool should be called')

    registry = scheduler(capabilities={'research': (), 'solver': ('solve',)},
                         tools={'solve': ExpertTool(Contract, unreachable, True, 'comsol')})
    invalid = (request('bad', capability='unknown') if case == 'unknown_capability'
               else request('bad', tools=('solve',)))
    with pytest.raises((ValueError, PermissionError), match='capability'):
        await registry.run(admitted(request('valid'), invalid), workspace_root=tmp_path, execute=unreachable)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_tool_broker_enforces_task_grants_and_argument_schema(tmp_path):
    calls = []

    async def lookup(task, arguments):
        calls.append((task.task_id, arguments.query))
        return 'reference'

    async def fake(context):
        with pytest.raises(PermissionError, match='not granted'):
            await context.call_tool('other', {})
        with pytest.raises(ValueError, match='workspace'):
            await context.call_tool('lookup', {'query': 'heat', 'workspace': '../other'})
        assert await context.call_tool('lookup', {'query': 'heat'}) == 'reference'
        return reply(context)

    tool = ExpertTool(Query, lookup)
    host = scheduler(capabilities={'research': ('lookup', 'other')}, tools={'lookup': tool, 'other': tool})
    batch = await host.run(admitted(request('research', tools=('lookup',))), workspace_root=tmp_path, execute=fake)
    assert set(batch.results) == {'research'}
    assert calls == [('research', 'heat')]


@pytest.mark.parametrize('grant', [None, False, 1, True])
@pytest.mark.asyncio
async def test_solver_tool_requires_explicit_host_authority(tmp_path, grant):
    calls = []
    checks = []

    def authorize(task, tool, arguments):
        checks.append((task.scope, task.task_id, tool, arguments.model_dump()))
        return grant

    async def solve(task, arguments):
        calls.append(task.task_id)
        return 'synthetic solve'

    async def fake(context):
        with pytest.raises(ValueError, match='authorized'):
            await context.call_tool('solve', {'authorized': True})
        assert await context.call_tool('solve', {}) == 'synthetic solve'
        return reply(context)

    host = scheduler(capabilities={'solver': ('solve',)},
                     tools={'solve': ExpertTool(Contract, solve, True, 'comsol')},
                     authorize_run=None if grant is None else authorize)
    batch = await host.run(admitted(request('solve-task', capability='solver', tools=('solve',))),
                           workspace_root=tmp_path, execute=fake)
    assert checks == ([] if grant is None else [(SCOPE, 'solve-task', 'solve', {})])
    if grant is True:
        assert calls == ['solve-task'] and set(batch.results) == {'solve-task'}
    else:
        assert calls == [] and 'requires host RUN authorization' in batch.failures['solve-task']


@pytest.mark.asyncio
async def test_solver_authorization_is_bound_to_each_validated_call(tmp_path):
    calls = []
    approved = {'reviewed heat inputs'}

    async def solve(task, arguments):
        calls.append(arguments.query)

    async def fake(context):
        with pytest.raises(PermissionError, match='RUN authorization'):
            await context.call_tool('solve', {'query': 'changed inputs'})
        await context.call_tool('solve', {'query': 'reviewed heat inputs'})
        approved.clear()
        with pytest.raises(PermissionError, match='RUN authorization'):
            await context.call_tool('solve', {'query': 'reviewed heat inputs'})
        return reply(context)

    host = scheduler(capabilities={'solver': ('solve',)},
                     tools={'solve': ExpertTool(Query, solve, True, 'comsol')},
                     authorize_run=lambda task, tool, arguments: arguments.query in approved)
    tasks = admitted(request('solve-task', capability='solver', tools=('solve',)))
    batch = await host.run(tasks, workspace_root=tmp_path, execute=fake)
    assert set(batch.results) == {'solve-task'}
    assert calls == ['reviewed heat inputs']


@pytest.mark.asyncio
async def test_shared_resource_serializes_tools_without_blocking_other_work(tmp_path):
    order = []
    research_started = asyncio.Event()

    async def solve(task, arguments):
        order.append(f'{task.task_id}:start')
        if task.task_id == 'one':
            await research_started.wait()
        order.append(f'{task.task_id}:end')

    async def fake(context):
        if context.task.task_id == 'research':
            research_started.set()
        else:
            await context.call_tool(context.task.requested_tools[0], {})
        return reply(context)

    host = scheduler(capabilities={'solver': ('heat', 'diffusion'), 'research': ()},
                     tools={name: ExpertTool(Contract, solve, True, 'comsol') for name in ('heat', 'diffusion')},
                     authorize_run=lambda task, tool, arguments: True)
    tasks = admitted(request('one', capability='solver', tools=('heat',)),
                     request('two', capability='solver', tools=('diffusion',)), request('research'))
    batch = await asyncio.wait_for(host.run(tasks, workspace_root=tmp_path, execute=fake), timeout=5)
    assert len(batch.results) == 3
    assert order == ['one:start', 'one:end', 'two:start', 'two:end']


@pytest.mark.parametrize('name', ['../0/notes.txt', r'..\0\notes.txt', '/tmp/notes.txt',
                                  r'C:\temp\notes.txt', 'notes.txt:stream', 'CON.txt', 'notes.', '..'])
@pytest.mark.asyncio
async def test_scratch_broker_rejects_paths_and_windows_aliases(tmp_path, name):
    async def fake(context):
        with pytest.raises(PermissionError, match='safe basename'):
            context.read_scratch(name)
        with pytest.raises(PermissionError, match='safe basename'):
            context.write_scratch(name, b'denied')
        return reply(context)

    batch = await scheduler().run(admitted(request('research')), workspace_root=tmp_path, execute=fake)
    assert set(batch.results) == {'research'}


@pytest.mark.asyncio
async def test_expert_context_contains_only_declared_and_dependency_evidence(tmp_path):
    async def fake(context):
        ids = [ref.evidence_id for ref in context.evidence]
        if context.task.task_id == 'upstream':
            assert ids == ['source-1'] and context.dependencies == ()
            return reply(context, refs=('source-1',))
        if context.task.task_id == 'independent':
            assert ids == [] and context.dependencies == ()
            return reply(context, refs=('source-2',))
        assert ids == ['source-1']
        assert tuple(record.task_id for record in context.dependencies) == ('upstream',)
        return reply(context, refs=('source-1',))

    tasks = admitted(request('upstream', refs=('source-1',)), request('independent'),
                     request('child', dependencies=('upstream',)), evidence=(reference('source-1'), reference('source-2')))
    batch = await scheduler().run(tasks, workspace_root=tmp_path, execute=fake)
    assert set(batch.results) == {'upstream', 'child'}
    assert 'outside its task context' in batch.failures['independent']


@pytest.mark.asyncio
async def test_cancel_drains_executor_and_revokes_access(tmp_path):
    started = asyncio.Event()
    ended = asyncio.Event()
    retained = []

    async def fake(context):
        assert context.task.task_id == 'root'
        retained.append(context)
        context.write_scratch('notes.txt', b'working')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            with pytest.raises(PermissionError, match='active dispatched task'):
                context.write_scratch('late.txt', b'late')
            ended.set()

    tasks = admitted(request('root'), request('child', dependencies=('root',)))
    running = asyncio.create_task(scheduler().run(tasks, workspace_root=tmp_path, execute=fake))
    await asyncio.wait_for(started.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert ended.is_set() and list(tmp_path.iterdir()) == []
    with pytest.raises(PermissionError, match='dispatched task'):
        retained[0].read_scratch('notes.txt')
