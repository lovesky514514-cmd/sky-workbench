import asyncio

import pytest

from app.agent.planner import ScientificPlanner, enforce_deterministic_contract, extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.scientific.plan_completion import accept_completion, attach_completion
from app.scientific.schemas import ScientificParameter, ScientificPlan
from app.session.assumptions import AssumptionLedger
from app.session.store import SessionStore
from app.validators import plan_quality_validator


class OfflineProvider:
    async def complete(self, *args, **kwargs):
        return None


def draft(prompt="建一个铝板传热模型"):
    return attach_completion(ScientificPlan.model_validate(extract_heat_transfer_plan(prompt)))


def test_incomplete_request_produces_draft_then_reviewed_executable_plan(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session = store.create("workbench")
    runtime = AgentRuntime(sessions=store, planner=ScientificPlanner(providers=OfflineProvider()))
    runtime._retrieve = lambda *args: []

    async def exercise():
        # The study is explicit; dimensions and boundaries remain reviewable suggestions.
        plan = await runtime._plan(session["id"], "建一个二维稳态铝板传热模型")
        assert plan is not None and plan.completion and not plan.runnable
        assert plan.planning_status == "DRAFT_NEEDS_INPUT"
        binding = {"reviewed_digest": plan.completion.reviewed_digest, "expected_revision": plan.revision}
        saved = await runtime.complete_plan(session["id"], plan.completion.proposal_id, {}, "auto", **binding)
        assert saved["plan"]["runnable"] is True
        assert saved["plan"]["planning_status"] == "READY"
        assert saved["plan"]["completion"] is None
        for item in saved["plan"]["parameters"].values():
            assert item["assumption"] and item["use_authorized"] and not item["user_confirmed"] and not item["verified"]
        ledger = AssumptionLedger(store).active(session["id"])
        assert ledger and all(not item["user_confirmed"] and item["executable"] for item in ledger)
        assert not runtime._tasks  # Planning and review never spawn a solver.
        with pytest.raises(ValueError, match="Proposal changed"):
            await runtime.complete_plan(session["id"], plan.completion.proposal_id, {}, "review", **binding)

    asyncio.run(exercise())


def test_known_inputs_survive_ai_suggestions_and_confirmation():
    plan = draft("二维铝板传热，矩形 20 mm × 8 mm，左侧 350 K")
    assert plan.completion is not None
    known = plan.parameters["left_temperature"].model_dump()
    assert "left_temperature" not in plan.completion.parameters
    accepted = accept_completion(plan, plan.completion.proposal_id, {"right_temperature": 300})
    assert accepted.parameters["left_temperature"].model_dump() == known
    assert accepted.parameters["right_temperature"].value == 300
    assert accepted.parameters["right_temperature"].verified is False
    assert plan.parameters.get("right_temperature") is None


def test_provider_candidate_survives_both_deterministic_passes():
    deterministic = ScientificPlan.model_validate(extract_heat_transfer_plan("铝板传热"))
    candidate = ScientificPlan.model_validate(extract_heat_transfer_plan(
        "2D steady aluminum heat transfer 0.2 m x 0.1 m left 340 K right 300 K insulated"))
    candidate.parameters["width"] = ScientificParameter(name="width", value=0.2, unit="m", source="AI", rationale="Exploratory dimensions")
    candidate.missing_information = list(deterministic.missing_information)
    once = enforce_deterministic_contract(candidate, deterministic)
    assert "width" not in once.parameters
    assert once.completion.parameters["width"].value == 0.2
    twice = enforce_deterministic_contract(once, deterministic)
    accepted = accept_completion(twice, twice.completion.proposal_id, {})
    assert accepted.parameters["width"].value == 0.2
    assert plan_quality_validator.validate(accepted).status == "VALID_WITH_WARNINGS"


def test_unsafe_or_out_of_scope_values_do_not_become_ready():
    plan = draft()
    with pytest.raises(ValueError):
        accept_completion(plan, "old-proposal", {})
    with pytest.raises(ValueError):
        accept_completion(plan, plan.completion.proposal_id, {"external": 1})
    with pytest.raises(ValueError):
        accept_completion(plan, plan.completion.proposal_id, {"width": float("nan")})
    accepted = accept_completion(plan, plan.completion.proposal_id, {"left_temperature": -1})
    assert plan_quality_validator.validate(accepted).status == "INVALID"
    assert draft("三维瞬态铝板传热").completion is None


def test_generated_json_cannot_claim_user_approval_or_bypass_unit_conversion():
    plan = draft()
    plan.parameters["forged"] = ScientificParameter(name="forged", value=1, unit="m", source="AI", user_confirmed=True)
    parsed = ScientificPlanner._parse_provider_plan(plan.model_dump_json(), [], "provider", "model", "铝板传热")
    assert parsed.completion is None
    assert parsed.parameters["forged"].user_confirmed is False
    plan.completion.parameters["width"].unit = "mm"
    accepted = accept_completion(plan, plan.completion.proposal_id, {"width": 100})
    assert accepted.parameters["width"].value == 0.1
    assert accepted.parameters["width"].unit == "m"
    assert accepted.parameters["width"].original_value == 100
