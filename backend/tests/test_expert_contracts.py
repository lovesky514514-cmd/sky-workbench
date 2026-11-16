import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.execution.expert_contracts import Contract, EvidenceRef, ExpertIntake, ExpertScope, ExpertTask
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.session.store import SessionStore


@pytest.fixture
def case():
    sample = Path(__file__).with_name('fixtures') / 'expert_heat_contract_v1.json'
    case = json.loads(sample.read_text(encoding='utf-8'))
    for record in (*case['tasks'], case['result'], case['score'], case['proposal']):
        record['scope'] = dict(case['scope'])
    return case


def intake(case):
    return ExpertIntake(scope=ExpertScope.model_validate(case['scope']), tasks=case['tasks'],
                        evidence=[EvidenceRef.model_validate(record) for record in case['evidence']])


def test_heat_fixture_round_trips_without_gaining_authority(case):
    host = intake(case)
    assert [task.task_id for task in host.tasks] == ['materials', 'solve', 'review']
    assert host.tasks[1].requested_tools == ('heat.run',)
    result = host.result(case['result'], dispatched_task_id='review')
    assert host.result(json.loads(result.model_dump_json()), dispatched_task_id='review') == result
    score = host.score(case['score'])
    assert score.total == 94
    assert host.score(score.model_dump(mode='json')).total == 94
    proposal = host.proposal(case['proposal'], known_claims={claim.claim_id for claim in result.claims})
    assert proposal.actions[0].target == 'boundary_conditions.left'
    assert 'authorized' not in proposal.model_dump(mode='json')
    assert 'total' not in score.model_dump(mode='json')
    assert EvidenceRef.model_validate(case['evidence'][1]).artifact.attempt_id != result.scope.attempt_id
    assert ExpertTask.model_json_schema()['additionalProperties'] is False


@pytest.mark.asyncio
async def test_heat_fixture_dispatches_through_the_scheduler(case, tmp_path):
    called = []

    async def lookup(task, arguments):
        called.append((task.task_id, 'knowledge.search'))

    async def solve(task, arguments):
        called.append((task.task_id, 'heat.run'))

    async def fake(context):
        for tool in context.task.requested_tools:
            await context.call_tool(tool, {})
        if context.task.task_id == 'review':
            assert [result.task_id for result in context.dependencies] == ['solve']
            return case['result']
        return {'schema_version': 1, 'scope': context.task.scope.model_dump(),
                'task_id': context.task.task_id, 'summary': 'Synthetic Heat task completed'}

    host = ExpertScheduler(
        capabilities={'materials_research': ('knowledge.search',), 'heat_execution': ('heat.run',),
                      'scientific_review': ()},
        tools={'knowledge.search': ExpertTool(Contract, lookup),
               'heat.run': ExpertTool(Contract, solve, requires_run=True, exclusive_resource='comsol')},
        max_concurrency=2,
        authorize_run=lambda task, tool, arguments: task.task_id == 'solve' and tool == 'heat.run',
    )
    batch = await host.run(intake(case), workspace_root=tmp_path, execute=fake)
    assert set(batch.results) == {'materials', 'solve', 'review'} and not batch.failures and not batch.blocked
    assert called == [('materials', 'knowledge.search'), ('solve', 'heat.run')]


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', '1'), ('schema_version', 2),
    ('task_id', '  '), ('workspace', '../another-project'), ('granted_tools', ['heat.run']),
    ('budget_estimate', {'agent_calls': -1, 'solver_runs': 1}),
    ('budget_estimate', {'agent_calls': True, 'solver_runs': 1}),
])
def test_invalid_task_fields_are_rejected(case, field, value):
    case['tasks'][0][field] = value
    with pytest.raises(ValidationError):
        intake(case)


def test_missing_attempt_identity_is_rejected(case):
    del case['tasks'][0]['scope']['attempt_id']
    with pytest.raises(ValidationError, match='attempt_id'):
        intake(case)


@pytest.mark.parametrize('scope_field,stale_value', [
    ('case_id', 'other-case'), ('project_id', 'other-project'), ('session_id', 'other-session'),
    ('plan_id', 'other-plan'), ('plan_revision', 2), ('run_id', 'other-run'), ('attempt_id', 'old-attempt'),
])
@pytest.mark.parametrize('record', ['result', 'score', 'proposal'])
def test_wrong_scope_cannot_enter_current_attempt(case, scope_field, stale_value, record):
    host = intake(case)
    case[record]['scope'][scope_field] = stale_value
    kwargs = {
        'result': {'dispatched_task_id': 'review'},
        'score': {},
        'proposal': {'known_claims': {'center-temperature'}},
    }[record]
    with pytest.raises(ValueError, match='current expert scope'):
        getattr(host, record)(case[record], **kwargs)


@pytest.mark.parametrize('dependencies,message', [(['unknown'], 'Unknown task dependency'),
                                                  (['review'], 'cycle'), (['materials'], 'cycle')])
def test_unknown_and_cyclic_dependencies_are_rejected(case, dependencies, message):
    case['tasks'][0]['dependencies'] = dependencies
    with pytest.raises(ValueError, match=message):
        intake(case)


def test_task_and_evidence_ids_must_be_unique(case):
    case['tasks'].append(case['tasks'][0])
    with pytest.raises(ValueError, match='Duplicate task ID'):
        intake(case)
    case['tasks'].pop()
    case['evidence'].append(case['evidence'][0])
    with pytest.raises(ValueError, match='Duplicate evidence ID'):
        intake(case)


def test_result_cannot_claim_a_sibling_task(case):
    host = intake(case)
    case['result']['task_id'] = 'solve'
    with pytest.raises(ValueError, match='dispatched expert task'):
        host.result(case['result'], dispatched_task_id='review')


def test_unadmitted_evidence_cannot_be_used_in_any_record(case):
    host = intake(case)
    case['result']['claims'][0]['evidence_refs'] = ['fabricated']
    case['score']['dimensions'][0]['evidence_refs'] = ['fabricated']
    case['proposal']['issues'][0]['evidence_refs'] = ['fabricated']
    with pytest.raises(ValueError, match='Unknown evidence IDs: fabricated'):
        host.result(case['result'], dispatched_task_id='review')
    with pytest.raises(ValueError, match='Unknown evidence IDs: fabricated'):
        host.score(case['score'])
    with pytest.raises(ValueError, match='Unknown evidence IDs: fabricated'):
        host.proposal(case['proposal'], known_claims={'center-temperature'})
    case['tasks'][0]['input_evidence_refs'] = ['fabricated']
    with pytest.raises(ValueError, match='Unknown evidence IDs: fabricated'):
        intake(case)


@pytest.mark.parametrize('mutation', ['missing_origin', 'missing_hash', 'bad_hash', 'reference_with_origin'])
def test_artifact_evidence_requires_producer_identity_and_hash(case, mutation):
    source = case['evidence'][1]
    if mutation == 'missing_origin':
        del source['artifact']
    elif mutation == 'missing_hash':
        del source['artifact']['sha256']
    elif mutation == 'bad_hash':
        source['artifact']['sha256'] = 'not-a-sha256'
    else:
        source['kind'] = 'reference'
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate(source)


@pytest.mark.parametrize('points', [-1, 21, True, 20.0, '20'])
def test_rubric_points_are_strict_and_bounded(case, points):
    case['score']['dimensions'][0]['points'] = points
    with pytest.raises(ValidationError):
        intake(case).score(case['score'])


def test_incomplete_and_unverified_scores_are_distinct(case):
    host = intake(case)
    case['score']['dimensions'].pop()
    with pytest.raises(ValidationError, match='exactly once'):
        host.score(case['score'])
    case['score']['dimensions'].append(case['score']['dimensions'][0])
    with pytest.raises(ValidationError, match='exactly once'):
        host.score(case['score'])
    case['score']['unverified_reason'] = 'Missing independent readback'
    with pytest.raises(ValidationError, match='cannot contain a score'):
        host.score(case['score'])
    case['score']['dimensions'] = []
    assert host.score(case['score']).total is None


@pytest.mark.parametrize('field', ['authorized', 'needs_human_approval', 'hard_gates_passed', 'passed'])
def test_model_proposal_cannot_supply_approval_or_verdict(case, field):
    host = intake(case)
    case['proposal']['actions'][0][field] = True
    with pytest.raises(ValidationError, match=field):
        host.proposal(case['proposal'], known_claims={'center-temperature'})


def test_proposal_must_bind_claim_and_affected_tasks(case):
    host = intake(case)
    with pytest.raises(ValueError, match='Unknown claim'):
        host.proposal(case['proposal'], known_claims=set())
    case['proposal']['issues'][0]['affected_task_ids'] = ['unknown']
    with pytest.raises(ValueError, match='Unknown task'):
        host.proposal(case['proposal'], known_claims={'center-temperature'})


def test_missing_evidence_issue_can_be_recorded_without_fabricating_a_reference(case):
    host = intake(case)
    issue = case['proposal']['issues'][0]
    issue['evidence_refs'] = []
    with pytest.raises(ValidationError, match='must cite evidence'):
        host.proposal(case['proposal'], known_claims={'center-temperature'})
    issue['category'] = 'missing_evidence'
    proposal = host.proposal(case['proposal'], known_claims={'center-temperature'})
    assert proposal.issues[0].evidence_refs == ()


def test_session_reads_legacy_events_without_expert_fields(tmp_path):
    path = tmp_path / 'sessions.db'
    store = SessionStore(path)
    store.initialize()
    session_id = store.create('3.1 history')['id']
    legacy = {'run_id': 'legacy-run', 'attempt_id': 'legacy-attempt',
              'execution_verdict': {'schema_version': 2, 'completed': True}}
    store.append_event(session_id, 'run.completed', legacy)
    history = SessionStore(path).get(session_id)['events']
    assert next(event['payload'] for event in history if event['type'] == 'run.completed') == legacy
