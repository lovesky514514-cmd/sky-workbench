"""讨论与计量的产品级正反测试；HTTP 响应为合成数据，不冒充真实模型。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent.runtime import AgentRuntime
from app.config import SettingsStore
from app.orchestration.research import ResearchDiscussion
from app.orchestration.research_contracts import ResearchEvidence, ResearchLimits
from app.providers.manager import ProviderManager, ProviderStatus
from app.session.store import SessionStore


class TestProvider(ProviderManager):
    __test__ = False
    async def preferred(self):
        return ProviderStatus('deepseek', 'DeepSeek', 'cloud', True, 'Ready', '', 'https://provider.invalid', 'test-model')


@pytest.fixture
def setup(tmp_path):
    state = SimpleNamespace(requests=[], damage=None, delay=0)
    async def respond(request):
        body = json.loads(request.content)
        state.requests.append(body)
        if state.delay: await asyncio.sleep(state.delay)
        opinion = {'summary': 'Synthetic expert output', 'claims': [{'claim_id': 'claim',
            'statement': 'The user goal is a research constraint, not a calculation result.',
            'evidence_refs': ['research-goal'], 'conditions': ['planning only'],
            'uncertainty': 'no computed result', 'counterexamples': [], 'questions': []}], 'requests': []}
        if state.damage: state.damage(opinion)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(opinion)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    config = SettingsStore(tmp_path / 'settings.json')
    config.update({'providers': {'deepseek': {'enabled': True, 'base_url': 'https://provider.invalid',
                                            'model': 'test-model', 'api_key': 'synthetic-test-key'}}})
    state.provider = TestProvider(config, httpx.MockTransport(respond))
    state.store = SessionStore(tmp_path / 'sessions.db'); state.store.initialize()
    state.sid = state.store.create('workbench')['id']
    state.runtime = AgentRuntime(sessions=state.store, providers=state.provider)
    state.discussion = ResearchDiscussion(state.runtime.controller, tmp_path, state.provider)
    return state


def source(name, **updates):
    return {'evidence_id': name, 'source': 'public synthetic fixture', 'captured_at': '2026-11-08T00:00:00Z',
            'applicability': 'benchmark premise only', 'text': 'A synthetic statement for testing.', **updates}


def test_real_provider_adapter_preserves_physical_request_count_usage_and_limit(setup):
    async def execute():
        return await setup.provider.complete_metered([{'role': 'user', 'content': 'test'}], json_schema={}, max_output_tokens=300)
    result, metering = asyncio.run(execute())
    assert result.provider == 'deepseek' and metering['requests'] == 1
    assert metering['input_tokens'] == 100 and metering['output_tokens'] == 50 and metering['usage_known']
    assert setup.requests[0]['max_tokens'] == 300


def test_role_model_routing_is_isolated_and_requires_enabled_provider():
    from app.providers.routing import settings_for_role
    config = {'active_provider': 'deepseek', 'research': {'role_models': {
        'physical_modeling': {'provider': 'kimi', 'model': 'specific-model'}}},
        'providers': {'deepseek': {'enabled': True, 'model': 'base'}, 'kimi': {'enabled': True, 'model': 'old'}}}
    store = SimpleNamespace(load=lambda: deepcopy(config), secret=lambda name: 'not-logged')
    routed = settings_for_role(store, 'physical_modeling')
    assert routed.load()['active_provider'] == 'kimi'
    assert routed.load()['providers']['kimi']['model'] == 'specific-model'
    assert config['active_provider'] == 'deepseek' and config['providers']['kimi']['model'] == 'old'
    config['providers']['kimi']['enabled'] = False
    with pytest.raises(ValueError, match='role_provider_not_enabled'): settings_for_role(store, 'physical_modeling')


def test_dynamic_discussion_uses_existing_scheduler_without_scientific_run(setup):
    assert setup.runtime.planner.providers is setup.provider
    result = asyncio.run(setup.discussion.run(setup.sid, 'Heat research', request_key='one'))
    assert result['status'] == 'READY_FOR_PLAN' and len(setup.requests) == 3
    assert all(row['receipt']['requests'] == 1 and row['receipt']['usage_known'] for row in result['calls'])
    assert result['outcome']['scientific_status'] == 'NOT_RUN'
    assert setup.store.latest_run(setup.sid) is None
    repeated = asyncio.run(setup.discussion.run(setup.sid, 'Heat research', request_key='one'))
    assert repeated['scope'] == result['scope'] and len(setup.requests) == 3
    with pytest.raises(ValueError, match='idempotency'):
        asyncio.run(setup.discussion.run(setup.sid, 'changed research', request_key='one'))


def test_parameter_range_is_not_collapsed_to_single_value(setup):
    evidence = [source('material', parameter_ranges=[{'parameter': 'conductivity', 'lower': 40., 'upper': 55., 'unit': 'W/(m*K)'}])]
    result = asyncio.run(setup.discussion.run(setup.sid, 'iron material heat', evidence))
    assert result['status'] == 'NEEDS_EVIDENCE'
    assert {issue['kind'] for issue in result['outcome']['issues']} == {'parameter_uncertainty', 'applicability_missing'}
    assert all(item['solver_runs'] == 0 for item in result['outcome']['rework'])
    assert 'scientific_counterevidence' in {row['receipt']['capability'] for row in result['calls']}


def test_missing_material_sources_use_authorized_retrieval_and_freeze_unverified_packet(setup):
    queries = []
    def retrieve(sid, query, limit):
        queries.append((sid, query, limit))
        return [{'citation': {'title': 'Public fixture'}, 'text': 'Applicability still requires review'}]
    setup.runtime._retrieve = retrieve
    result = asyncio.run(setup.discussion.run(setup.sid, 'Review iron material heat'))
    assert result['status'] == 'READY_FOR_PLAN' and len(queries) == 1
    scope = result['scope']
    checkpoint = setup.store.load_checkpoint(scope['project_id'], scope['session_id'], scope['case_id'])
    assert checkpoint.verified['lookup-sources'].evidence[0].source_ref == 'knowledge.authorized_retrieval'
    assert checkpoint.spent.solver_runs == 0 and setup.store.latest_run(setup.sid) is None


def test_conflicting_boundaries_are_not_resolved_by_expert_agreement(setup):
    evidence = [source('left-note', boundary_interpretation='insulated'), source('right-note', boundary_interpretation='convection')]
    result = asyncio.run(setup.discussion.run(setup.sid, 'convection Heat research', evidence))
    assert result['status'] == 'NEEDS_EVIDENCE'
    assert any(issue['kind'] == 'boundary_conflict' for issue in result['outcome']['issues'])


def test_physical_failure_remains_blocked_despite_positive_text(setup):
    result = asyncio.run(setup.discussion.run(setup.sid, 'Heat audit', [source('verdict', validation_checks={'heat_flux': False})]))
    assert result['status'] == 'NEEDS_EVIDENCE'
    assert result['outcome']['issues'][0]['kind'] == 'physical_failure'
    assert setup.store.latest_run(setup.sid) is None


@pytest.mark.parametrize('damage', ['claim', 'request', 'authority'])
def test_forged_refs_or_approvals_never_enter_checkpoint(setup, damage):
    def corrupt(opinion):
        if damage == 'claim': opinion['claims'][0]['evidence_refs'] = ['fabricated']
        elif damage == 'request': opinion['requests'] = [{'capability': 'recovery_diagnostics', 'question': 'inspect', 'evidence_refs': ['fabricated']}]
        else: opinion['approved_run'] = True
    setup.damage = corrupt
    result = asyncio.run(setup.discussion.run(setup.sid, 'heat research'))
    assert result['status'] == 'FAILED' and result['outcome']['accepted_opinions'] == []
    assert len(setup.requests) == 1 and result['calls'][0]['status'] == 'FAILED'
    assert setup.store.latest_run(setup.sid) is None


def test_budget_and_unknown_usage_do_not_become_fake_success(setup):
    result = asyncio.run(setup.discussion.run(setup.sid, 'heat research', limits=ResearchLimits(model_calls=1)))
    assert result['status'] == 'BUDGET_EXHAUSTED' and len(setup.requests) == 1


def test_provider_outage_is_explicit_degradation(setup):
    async def unavailable(*a, **k):
        return None, {'requests': 0, 'usage_known': False, 'input_tokens': None, 'output_tokens': None, 'latency_ms': 0}
    setup.provider.complete_metered = unavailable
    result = asyncio.run(setup.discussion.run(setup.sid, 'heat research'))
    assert result['status'] == 'DEGRADED' and result['outcome']['accepted_opinions'] == []


def test_cancelled_request_retains_budget_and_cannot_replay(setup):
    setup.delay = 10
    async def execute():
        task = asyncio.create_task(setup.discussion.run(setup.sid, 'heat research', request_key='cancel'))
        while not setup.requests: await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        return await setup.discussion.run(setup.sid, 'heat research', request_key='cancel')
    result = asyncio.run(execute())
    assert result['status'] == 'CANCELED' and len(setup.requests) == 1
    assert result['calls'][0]['status'] == 'INTERRUPTED'


def test_malicious_log_is_redacted_before_model_request(setup):
    result = asyncio.run(setup.discussion.run(setup.sid, 'heat research', [source('log', text='api_key=secret123 {"password":"jsonsecret"} contact hidden@example.test /home/private/data')]))
    assert result['status'] == 'READY_FOR_PLAN'
    assert all('secret123' not in json.dumps(item) and 'jsonsecret' not in json.dumps(item) and 'hidden@example.test' not in json.dumps(item) for item in setup.requests)


def test_reproducible_delivery_has_manifest_and_rejects_modified_report(setup, tmp_path):
    from app.orchestration.research_delivery import ResearchDelivery
    from pathlib import Path
    result = asyncio.run(setup.discussion.run(setup.sid, 'heat research'))
    export = ResearchDelivery(setup.store, tmp_path)
    manifest = export.export(result['case_id'])
    assert manifest['scientific_status'] == 'NOT_RUN' and 'report.md' in manifest['files']
    assert len(manifest['evidence']) > 2
    assert export.export(result['case_id'])['manifest_sha256'] == manifest['manifest_sha256']
    (Path(manifest['directory'])/'report.md').write_text('fake claim of a real solve', encoding='utf-8')
    with pytest.raises(ValueError, match='delivery_file_changed'): export.export(result['case_id'])


@pytest.mark.parametrize('strategy', ['single_agent', 'parallel_no_collaboration', 'full_rework', 'sky'])
def test_ablation_strategies_keep_identical_physical_gates(setup, strategy):
    result = asyncio.run(setup.discussion.run(setup.sid, 'Heat audit', [source('physical', validation_checks={'heat_flux': False})],
        limits=ResearchLimits(model_calls=16, token_limit=500000), strategy=strategy))
    assert result['status'] == 'NEEDS_EVIDENCE' and result['outcome']['solver_runs'] == 0
    assert any(issue['kind'] == 'physical_failure' for issue in result['outcome']['issues'])
    if strategy == 'full_rework': assert result['outcome']['full_rework_tasks'] > 0
