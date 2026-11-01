"""Language coverage, factual invariants and real review transactions across model families."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.agent.planner import ScientificPlanner
from app.agent.runtime import AgentRuntime
from app.scientific.general_planning import build_general_plan
from app.scientific.plan_completion import accept_completion, sanitize_plan_edit
from app.scientific.request_analysis import analyze_request
from app.scientific.schemas import ScientificParameter, ScientificPlan
from app.session.assumptions import AssumptionLedger
from app.session.store import SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
VAGUE = json.loads((FIXTURES / "fuzzy_requests_50.json").read_text(encoding="utf-8"))
EXPLICIT = json.loads(
    (FIXTURES / "explicit_requests_50.json").read_text(encoding="utf-8")
)


class Offline:
    async def complete(self, *args, **kwargs):
        return None


def planned(prompt):
    payload, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(prompt, []))
    return ScientificPlan.model_validate(payload)


@pytest.mark.parametrize("prompt,domains", VAGUE)
def test_vague_requests_produce_reviewable_models_not_automatic_runs(prompt, domains):
    plan = planned(prompt)
    assert plan.physics and plan.geometry and plan.expected_outputs
    assert set(plan.modeling_brief.domains) & set(domains)
    assert plan.modeling_brief.equations and plan.completion
    assert plan.modeling_brief.symbolic_inputs
    assert not plan.runnable
    assert not any(
        item.user_confirmed or item.use_authorized for item in plan.parameters.values()
    )


@pytest.mark.parametrize("prompt", EXPLICIT)
def test_explicit_requests_preserve_original_input_and_normalized_facts(prompt):
    plan = planned(prompt)
    assert plan.original_request == prompt
    assert plan.physics and plan.geometry and plan.expected_outputs
    for fact in plan.modeling_brief.facts:
        assert prompt[slice(*fact.span)] == fact.original
        if fact.normalized:
            actual = plan.parameters[fact.key]
            assert actual.value == fact.value
            assert actual.original_unit == fact.quantity["original_unit"]
            assert not actual.assumption
    assert all(
        engine.execution_status == "NOT_ASSESSED"
        for engine in plan.modeling_brief.engine_candidates
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "3x3x20 mm 铁块在水中冷却，初温 600 °C，环境温度 25 °C",
        "3x3x20 mm 铁块持续加热，初温 600 °C，环境温度 25 °C",
        "比较 3x3x20 mm 铁块与铜块哪个冷却快",
        "3x3x20 mm 铁块初温 600 °C，与底座接触并冷却",
        "3x3x20 mm 铁块烧红后冷却，不考虑辐射",
        "二维铝板热膨胀，稳态传热与应力耦合，20x10 mm，左侧 350 K，右侧 300 K，上下绝热",
        "铝板传热，20x10 mm，左侧 350 K，右侧 300 K，上下绝热，瞬态变化",
        "稳态铝板 20x10 mm，不考虑传热，只研究振动",
    ],
)
def test_distinct_physics_cannot_silently_use_heat_benchmark(prompt):
    plan = planned(prompt)
    assert plan.workflow_id is None
    assert plan.completion and not plan.runnable


def test_explicit_temperature_is_not_replaced_by_red_hot_default():
    plan = planned("3x3x20 mm 铁块烧红后冷却，初温 600 °C，环境温度 20 °C，观察 120 s")
    assert plan.workflow_id == "heat_transfer_3d_cooling"
    assert plan.parameters["initial_temperature"].value == 873.15
    assert plan.parameters["ambient_temperature"].value == 293.15
    assert plan.parameters["duration"].value == 120
    assert "initial_temperature" not in plan.completion.parameters
    accepted = accept_completion(plan, plan.completion.proposal_id, {})
    assert accepted.parameters["initial_temperature"].value == 873.15


def test_conflicts_unknown_units_and_missing_referents_stay_visible():
    conflict = planned("铁块 3x3x20 mm 冷却，初温 600 °C，初温 700 °C")
    assert conflict.workflow_id is None and conflict.modeling_brief.conflicts
    assert "initial_temperature" not in conflict.parameters
    invalid = planned("铁块 3x3x20parsecs 冷却")
    assert invalid.workflow_id is None and any(
        f.issue for f in invalid.modeling_brief.facts
    )
    reference = planned("把这个方程画出来")
    assert reference.completion and not reference.parameters
    assert any(q.key == "mathematical_model" for q in reference.completion.questions)


def test_negations_and_english_material_boundaries():
    brief = analyze_request(
        "A copper block cooling in an environment. No radiation, without convection."
    )
    assert {f.value for f in brief.facts if f.key.startswith("material:")} == {"Copper"}
    assert {"radiation", "convection"} <= set(brief.exclusions)
    excluded = analyze_request("不是铁，是铜；不考虑传热，只研究应力")
    assert "thermal" not in excluded.concepts and "mechanics" in excluded.concepts
    assert {f.value for f in excluded.facts if f.key.startswith("material:")} == {
        "Copper"
    }


@pytest.mark.parametrize(
    "constraint",
    [
        "无反应无迁移无对流",
        "不考虑反应，不考虑对流，不考虑电场迁移",
        "no reaction, no migration, no convection",
        "without reaction and convection",
        "zero convection, migration or reaction",
    ],
)
def test_explicit_diffusion_negations_do_not_request_extra_physics(constraint):
    prompt = (
        "2D stationary TDS 20 mm x 10 mm left 2 mol/m^3 right 0 mol/m^3 no flux D=1e-9 m^2/s; "
        + constraint
    )
    plan = planned(prompt)
    assert plan.workflow_id == "tds_2d_stationary_diffusion"
    assert plan.runnable and plan.completion is None
    assert plan.parameters["left_concentration"].value == 2
    assert "left_temperature" not in plan.parameters


def test_actual_reaction_is_still_outside_pure_diffusion_scope():
    plan = planned(
        "2D stationary TDS 20 mm x 10 mm left 2 mol/m^3 right 0 mol/m^3 no flux D=1e-9 m^2/s; no convection but include reaction"
    )
    assert plan.workflow_id is None and not plan.runnable and plan.completion


def test_review_choices_are_bound_partial_and_not_forgeable_by_plan_json():
    plan = build_general_plan("研究液体中分子的扩散")
    digest = plan.completion.reviewed_digest
    tampered = plan.model_copy(deep=True)
    tampered.completion.questions[0].options[0].label = "changed"
    assert tampered.completion.reviewed_digest != digest
    with pytest.raises(ValueError, match="listed planning option"):
        accept_completion(
            plan,
            plan.completion.proposal_id,
            {},
            question_answers={"parameter_policy": "verified"},
        )
    accepted = accept_completion(
        plan,
        plan.completion.proposal_id,
        {},
        question_answers={"parameter_policy": "symbolic"},
    )
    assert accepted.modeling_brief.answers == {"parameter_policy": "symbolic"}
    assert (
        accepted.completion
        and accepted.completion.proposal_id != plan.completion.proposal_id
    )
    forged = plan.model_copy(deep=True)
    forged.modeling_brief.answers["model_scale"] = "quantum"
    sanitize_plan_edit(forged, plan)
    assert not forged.modeling_brief.answers
    assert forged.completion.questions


def test_semantic_provider_can_extend_vocabulary_without_granting_authority():
    # A stub proves the protocol; it is not evidence of live model comprehension.
    result = ScientificPlan(
        title="声传播方案",
        research_goal="房间回声",
        physics=["Acoustics"],
        geometry=["待选声学域"],
        expected_outputs=["声压场"],
        modeling_brief={
            "domains": ["continuum"],
            "equations": ["d2p/dt2=c^2*laplacian(p)"],
            "answers": {"parameter_policy": "provide"},
            "engine_candidates": [
                {
                    "engine": "COMSOL",
                    "role": "fake",
                    "execution_status": "AVAILABLE_FOR_PREFLIGHT",
                }
            ],
        },
    )
    result.parameters["duration"] = ScientificParameter(
        name="duration",
        value=2,
        unit="s",
        source="AI",
        verified=True,
        use_authorized=True,
    )

    class SemanticProvider:
        async def complete(self, *args, **kwargs):
            return SimpleNamespace(
                text=result.model_dump_json(),
                provider_label="semantic-fixture",
                model="stub",
            )

    payload, _, _ = asyncio.run(
        ScientificPlanner(SemanticProvider()).plan("研究房间里的回声", [])
    )
    plan = ScientificPlan.model_validate(payload)
    assert "Acoustics" in plan.physics
    assert "d2p/dt2=c^2*laplacian(p)" in plan.modeling_brief.equations
    assert not plan.modeling_brief.answers
    assert plan.workflow_id is None and not plan.runnable
    assert not plan.completion.parameters["duration"].verified
    assert not plan.completion.parameters["duration"].use_authorized


def test_semantic_suggestions_cannot_replace_explicit_geometry_or_material():
    prompt = "铜板 20x10 mm 瞬态传热，初温 400 K"
    candidate = build_general_plan("铝板 100x50 mm 传热")
    candidate.parameters["initial_temperature"] = ScientificParameter(
        name="initial_temperature", value=900, unit="K", source="AI"
    )

    class Provider:
        async def complete(self, *args, **kwargs):
            return SimpleNamespace(
                text=candidate.model_dump_json(), provider_label="test", model="stub"
            )

    payload, _, _ = asyncio.run(ScientificPlanner(Provider()).plan(prompt, []))
    plan = ScientificPlan.model_validate(payload)
    assert plan.materials == ["Copper"]
    assert plan.geometry == ["0.02 m × 0.01 m"]
    assert plan.parameters["initial_temperature"].value == 400
    assert "initial_temperature" not in plan.completion.parameters


@pytest.mark.parametrize(
    "prompt",
    [
        "Let a cantilever sag under an unknown end force and compare deflections.",
        "我有一片金属，想看看它挠下去再放开会怎样。",
        "研究房间中的回声，希望让我选择未知条件。",
        "Can the molecules reorganize while the container warms up?",
        "不要猜这个未上传的图，先问我想研究什么。",
    ],
)
def test_held_out_or_unresolved_language_keeps_a_draft_and_targeted_choice(prompt):
    plan = planned(prompt)
    assert plan.completion and plan.physics and plan.geometry and plan.expected_outputs
    assert not plan.runnable
    assert any(q.options for q in plan.completion.questions)


@pytest.mark.parametrize("online", [False, True])
def test_http_choices_delegate_and_record_only_reviewed_scope(
    tmp_path, monkeypatch, online
):
    from app import main

    class Provider:
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            if not online or self.calls == 1:
                return None
            candidate = build_general_plan("金属板传热")
            candidate.parameters["duration"] = ScientificParameter(
                name="duration", value=60, unit="s", source="AI"
            )
            return SimpleNamespace(
                text=candidate.model_dump_json(), provider_label="test", model="stub"
            )

    provider = Provider()
    store = SessionStore(tmp_path / "session.db")
    store.initialize()
    runtime = AgentRuntime(sessions=store, planner=ScientificPlanner(provider))
    monkeypatch.setattr(runtime, "_retrieve", lambda *args: [])
    monkeypatch.setattr(main, "session_store", store)
    monkeypatch.setattr(main, "agent_runtime", runtime)
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", "fixture")
    client = TestClient(main.app, headers={"X-Workbench-Token": "fixture"})
    session = client.post("/api/sessions", json={}).json()
    url = "/api/sessions/" + session["id"]
    draft = client.post(
        url + "/messages", json={"mode": "PLAN", "content": "金属板传热"}
    ).json()["plan"]
    proposal = draft["completion"]
    body = dict(
        proposal_id=proposal["proposal_id"],
        reviewed_digest=proposal["reviewed_digest"],
        expected_revision=draft["revision"],
        question_answers={"parameter_policy": "propose"},
    )
    response = client.post(url + "/plan/completion", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()["plan"]
    assert not saved["runnable"] and not runtime._tasks
    assert saved["modeling_brief"]["answers"]["parameter_policy"] == "propose"
    if online:
        assert saved["completion"]["parameters"]["duration"]["value"] == 60
        assert not saved["completion"]["parameters"]["duration"]["use_authorized"]
    else:
        assert (
            saved["modeling_brief"]["model_outline"]["refinement_status"]
            == "NEEDS_MODEL_SERVICE"
        )
    ledger = AssumptionLedger(store).active(session["id"])
    assert any(item["choice"].get("question") == "parameter_policy" for item in ledger)
    assert client.post(url + "/plan/completion", json=body).status_code == 409
