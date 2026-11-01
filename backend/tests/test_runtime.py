import asyncio
from contextlib import suppress

from app.agent.planner import extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.models import AgentMode, AgentState
from app.session.store import SessionStore
from app.scientific.schemas import ScientificPlan
from app.scientific.plan_completion import attach_completion, accept_completion


def test_run_response_publishes_precheck_state_before_background_execution(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session = store.create("workbench")
    prompt = "建立二维稳态热传导模型，矩形 10 mm × 5 mm，左边 373 K，右边 293 K，材料为铝，上下绝热。"
    plan = extract_heat_transfer_plan(prompt)
    assert plan is not None
    reviewed = attach_completion(ScientificPlan.model_validate(plan))
    reviewed = accept_completion(reviewed,reviewed.completion.proposal_id,{})
    reviewed.runnable = True
    store.commit_plan_revision(session['id'],reviewed.model_dump(mode='json'),0)
    store.set_state(session["id"], AgentState.WAITING_FOR_EXECUTION)
    runtime = AgentRuntime(sessions=store)

    async def fake_execute(_session_id, _plan) -> None:
        await asyncio.Event().wait()

    runtime._execute_run = fake_execute  # type: ignore[method-assign]

    async def exercise() -> None:
        response = await runtime.handle_message(session["id"], AgentMode.RUN, prompt)
        assert response["state"] == AgentState.PRECHECK
        task = runtime._tasks[session["id"]]
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
