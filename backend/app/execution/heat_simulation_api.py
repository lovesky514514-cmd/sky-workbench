"""桌面工作台的模拟 Heat 会话控制。"""

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.execution.expert_contracts import Contract, Text
from app.execution.heat_simulation_evidence import Scenario
from app.execution.revision_store import CheckpointConflict


class StartSimulation(Contract):
    scenario: Scenario


class SimulationCommand(Contract):
    version: int = Field(strict=True, ge=1)


class ApproveSimulation(SimulationCommand):
    proposal_id: Text


def heat_simulation_router(get_service, active_project) -> APIRouter:
    router = APIRouter(prefix='/api/sessions/{session_id}/heat-simulation')

    def call(session_id, operation, *args):
        service = get_service()
        try:
            session = service.store.get(session_id)
            if session['project_id'] != active_project():
                raise KeyError(session_id)
            return getattr(service, operation)(session, *args)
        except KeyError as exc:
            raise HTTPException(404, detail='simulation_session_not_found') from exc
        except CheckpointConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.get('')
    async def read(session_id: str):
        return call(session_id, 'view')

    @router.post('/start', status_code=202)
    async def start(session_id: str, request: StartSimulation):
        return call(session_id, 'start', request.scenario)

    @router.post('/cancel')
    async def cancel(session_id: str, request: SimulationCommand):
        return call(session_id, 'cancel', request.version)

    @router.post('/resume', status_code=202)
    async def resume(session_id: str, request: SimulationCommand):
        return call(session_id, 'resume', request.version)

    @router.post('/approve', status_code=202)
    async def approve(session_id: str, request: ApproveSimulation):
        return call(session_id, 'approve', request.version, request.proposal_id)

    return router
