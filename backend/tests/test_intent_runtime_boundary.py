import asyncio

import httpx
import pytest

from app.agent.intent_compiler import compile_intent, confirmed_plan_input
from app.agent.planner import ScientificPlanner
from app.models import IntentConfirmationInput


class NoProvider:
    async def complete(self, *args, **kwargs):
        return None


def test_planner_uses_original_for_deterministic_values_but_canonical_for_model_input():
    original = "建立二维稳态热传导模型。矩形 10 mm × 5 mm，左侧 373 K，右侧 293 K，上下绝热。材料：铝。输出温度场。"
    canonical = compile_intent(original).canonical_query
    payload, provider, _ = asyncio.run(ScientificPlanner(providers=NoProvider()).plan(canonical, [], original_prompt=original))
    assert provider == "offline-scientific-planner"
    assert payload["runnable"] is False
    assert set(payload['completion']['parameters']) == {'thermal_conductivity'}
    assert payload["parameters"]["left_temperature"]["value"] == 373
    assert payload["original_request"] == original


def test_confirmed_study_and_explicit_conditions_feed_the_normal_plan():
    original = "建立二维热传导模型。"
    conditions = "矩形 2 m × 1 m，左侧 373.15 K，右侧 293.15 K，上下绝热，导热系数 20 W/(m*K)。"
    content, receipt = confirmed_plan_input(original, IntentConfirmationInput(study="stationary", details=conditions))
    intent = compile_intent(content)
    assert intent.study == ["Stationary"]
    assert intent.parameters["thermal_conductivity"].value == 20
    assert receipt == {"source": "user_intent_confirmation", "study": "stationary", "details": conditions}
    plan, _, _ = asyncio.run(ScientificPlanner(providers=NoProvider()).plan(intent.canonical_query, [], original_prompt=content))
    assert plan["original_request"] == content
    assert plan["parameters"]["left_temperature"]["value"] == 373.15


def test_undecided_study_preserves_unknown_and_explicit_study_cannot_be_replaced():
    original = "建立二维热传导模型。"
    content, _ = confirmed_plan_input(original, IntentConfirmationInput(study="undecided"))
    assert compile_intent(content).study == []
    with pytest.raises(ValueError, match="already specified"):
        confirmed_plan_input("建立二维稳态热传导模型。", IntentConfirmationInput(study="transient"))
    with pytest.raises(ValueError, match="conflict"):
        confirmed_plan_input(original, IntentConfirmationInput(study="stationary", details="研究类型为瞬态。"))


@pytest.mark.asyncio
async def test_confirmation_http_route_only_sends_plan_to_research_controller(tmp_path, monkeypatch):
    import app.main as main
    from app.agent.runtime import AgentRuntime
    from app.session.store import SessionStore

    store = SessionStore(tmp_path / 'sessions.db')
    store.initialize()
    session = store.create('project')['id']
    runtime = AgentRuntime(sessions=store)
    planned = []

    async def plan(session_id, content):
        planned.append((session_id, compile_intent(content).study))

    token = 'intent-http-test-token'
    monkeypatch.setenv('COMSOL_WORKBENCH_SESSION_TOKEN', token)
    monkeypatch.setattr(main, 'agent_runtime', runtime)
    monkeypatch.setattr(main, 'session_store', store)
    monkeypatch.setattr(runtime.controller, 'plan', plan)
    path = f'/api/sessions/{session}/messages'
    payload = {'content': '建立二维热传导模型。', 'intent_confirmation': {'study': 'stationary'}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url='http://desktop', headers={'X-Workbench-Token': token}) as client:
        for mode in ('ASK', 'RUN'):
            assert (await client.post(path, json={**payload, 'mode': mode})).status_code == 422
        assert (await client.post(path + '/stream', json={**payload, 'mode': 'PLAN'})).status_code == 422
        response = await client.post(path, json={**payload, 'mode': 'PLAN'})

    assert response.status_code == 200
    assert planned == [(session, ['Stationary'])]
    assert response.json()['messages'][-1]['intent_confirmation']['study'] == 'stationary'
    assert store.latest_run(session) is None
