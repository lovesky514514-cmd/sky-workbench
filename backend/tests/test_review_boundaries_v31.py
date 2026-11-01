import asyncio
import copy

import pytest

from app.agent.runtime import AgentRuntime
from app.scientific.plan_completion import accept_completion
from app.scientific.schemas import PlanCompletion, ScientificParameter
from app.session.assumptions import AssumptionLedger
from app.session.store import PlanRevisionConflict, SessionStore
from test_plan_completion import draft
from test_plan_completion import OfflineProvider
from app.agent.planner import ScientificPlanner


def fixture(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session_id = store.create("workbench")["id"]
    plan = draft()
    store.commit_plan_revision(session_id, plan.model_dump(mode="json"),0)
    return store, session_id, plan, AgentRuntime(sessions=store)


def test_plan_save_cannot_inject_confirmation_or_proposal(tmp_path):
    store, sid, plan, runtime = fixture(tmp_path)
    payload = plan.model_dump(mode="json")
    payload["parameters"] = copy.deepcopy(payload["completion"]["parameters"])
    for item in payload["parameters"].values():
        item.update(user_confirmed=True, use_authorized=True, verified=True,
                    source="user", source_page=8, source_chunk="forged", authorization="reviewed")
    payload["completion"] = None
    saved = asyncio.run(runtime.update_plan(sid, payload))["plan"]
    assert not saved["runnable"]
    assert all(not p["user_confirmed"] and not p["use_authorized"] and not p["verified"]
               and p["source_chunk"] is None for p in saved["parameters"].values())
    assert not AssumptionLedger(store).active(sid)
    assert any(e["type"] == "plan.field_rejected" for e in store.get(sid)["events"])


def test_stale_plan_save_does_not_overwrite_new_revision(tmp_path):
    store, sid, plan, runtime = fixture(tmp_path)
    old = plan.model_dump(mode="json")
    asyncio.run(runtime.update_plan(sid, old))
    with pytest.raises(PlanRevisionConflict):
        asyncio.run(runtime.update_plan(sid, old))
    assert store.get(sid)["plan"]["revision"] == plan.revision + 1


def test_acceptance_binds_digest_and_commits_ledger_once(tmp_path):
    store, sid, plan, runtime = fixture(tmp_path)
    async def exercise():
        args = dict(reviewed_digest=plan.completion.reviewed_digest, expected_revision=plan.revision)
        with pytest.raises(PlanRevisionConflict):
            await runtime.complete_plan(sid, plan.completion.proposal_id, {}, "review",
                                        **{**args, "reviewed_digest": "0" * 64})
        assert not AssumptionLedger(store).active(sid)
        outcomes = await asyncio.gather(*[
            runtime.complete_plan(sid, plan.completion.proposal_id, {}, "review", **args) for _ in range(2)
        ], return_exceptions=True)
        assert sum(isinstance(r, dict) for r in outcomes) == 1
        assert sum(isinstance(r, PlanRevisionConflict) for r in outcomes) == 1
        saved = store.get(sid)["plan"]
        assert saved["runnable"]
        events = store.get(sid)["events"]
        assert sum(e["type"] == "plan.dependencies_invalidated" for e in events) == 1
        assert all(a["user_confirmed"] for a in AssumptionLedger(store).active(sid))
    asyncio.run(exercise())


def test_ledger_failure_rolls_back_plan_and_events(tmp_path, monkeypatch):
    store, sid, plan, runtime = fixture(tmp_path)
    before = store.get(sid)
    original = AssumptionLedger.record
    def fail_after_insert(self, **kwargs):
        original(self, **kwargs)
        raise RuntimeError("simulated durable write failure")
    monkeypatch.setattr(AssumptionLedger, "record", fail_after_insert)
    with pytest.raises(RuntimeError):
        asyncio.run(runtime.complete_plan(sid, plan.completion.proposal_id, {}, "review",
                                          reviewed_digest=plan.completion.reviewed_digest,
                                          expected_revision=plan.revision))
    assert store.get(sid)["plan"] == before["plan"]
    assert store.get(sid)["events"] == before["events"]
    assert not AssumptionLedger(store).active(sid)


def test_partial_acceptance_keeps_remaining_proposal():
    plan = draft()
    accepted = accept_completion(plan, plan.completion.proposal_id, {}, selected_parameters=["width"])
    assert "width" in accepted.parameters
    assert accepted.completion and "width" not in accepted.completion.parameters
    assert accepted.completion.proposal_id != plan.completion.proposal_id
    assert not accepted.runnable
    completed = accept_completion(accepted, accepted.completion.proposal_id, {})
    assert completed.completion is None


def test_remaining_decisions_can_be_completed_in_place():
    plan = draft()
    plan.completion.remaining_missing = ["Required decision: solver"]
    accepted = accept_completion(plan, plan.completion.proposal_id, {})
    assert accepted.completion and accepted.completion.remaining_missing
    done = accept_completion(accepted, accepted.completion.proposal_id, {},
                             decisions={"solver": ["Stationary direct solver"]})
    assert done.completion is None and done.solver == ["Stationary direct solver"]


def test_unknown_quantities_are_rejected_and_temperature_differences_do_not_shift():
    plan = draft()
    plan.completion = PlanCompletion(parameters={"film_coefficient": ScientificParameter(
        name="film_coefficient", value="abc", unit="unknown", source="AI", assumption=True)})
    with pytest.raises(ValueError, match="Unsupported quantity"):
        accept_completion(plan, plan.completion.proposal_id, {})
    plan.completion = PlanCompletion(parameters={"delta_temperature": ScientificParameter(
        name="delta_temperature", value=10, unit="°C", source="AI", assumption=True)})
    digest = plan.completion.reviewed_digest
    plan.completion.parameters["delta_temperature"].value = 20
    assert plan.completion.reviewed_digest != digest
    accepted = accept_completion(plan, plan.completion.proposal_id, {})
    assert accepted.parameters["delta_temperature"].value == 20
    assert accepted.parameters["delta_temperature"].unit == "K"


def test_replanning_increments_revision_and_rejects_an_old_editor(tmp_path):
    store, sid, plan, runtime = fixture(tmp_path)
    runtime.planner = ScientificPlanner(providers=OfflineProvider())
    runtime._retrieve = lambda *args: []
    old = plan.model_dump(mode="json")
    replanned = asyncio.run(runtime._plan(sid, "建一个铝板传热模型"))
    assert replanned.revision == plan.revision + 1
    with pytest.raises(PlanRevisionConflict):
        asyncio.run(runtime.update_plan(sid, old))
