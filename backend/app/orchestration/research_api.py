"""科研讨论 API：复用总控及 N02，所有操作只读研究资料，不含求解入口。"""
import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.execution.expert_contracts import Contract
from app.orchestration.research_contracts import ResearchEvidence, ResearchLimits
from app.orchestration.research_store import ResearchStore


class ResearchRequest(Contract):
    goal: Annotated[str, Field(min_length=1, max_length=12000)]
    request_key: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')]
    evidence: tuple[ResearchEvidence, ...] = Field(default=(), max_length=16)
    limits: ResearchLimits = Field(default_factory=ResearchLimits)


class PreparePlan(Contract):
    case_id: Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


def research_router(runtime, active_project):
    router = APIRouter(prefix='/api/sessions/{session_id}/research')
    def scope(sid):
        host = runtime()
        try:
            session = host.sessions.get(sid)
        except KeyError as exc:
            raise HTTPException(404, 'research_session_not_found') from exc
        if session['project_id'] != active_project():
            raise HTTPException(404, 'research_session_not_found')
        return host
    def latest(host, sid):
        store = ResearchStore(host.sessions)
        project_id = host.sessions.get(sid)['project_id']
        with host.sessions._connect() as db:
            row = db.execute('SELECT case_id FROM research_studies WHERE session_id=? AND project_id=? ORDER BY rowid DESC LIMIT 1',
                             (sid, project_id)).fetchone()
        return store.read(row['case_id']) if row else {'status': 'NOT_STARTED', 'scientific_status': 'NOT_RUN'}
    @router.post('', status_code=202)
    async def start(session_id: str, request: ResearchRequest):
        host = scope(session_id)
        if (job := host.controller._research_tasks.get(session_id)) is not None and not job.done():
            current = latest(host, session_id)
            from app.orchestration.research import request_digest
            expected = request_digest(request.goal, request.evidence, {}, request.limits)
            if current.get('request_key') == request.request_key and current.get('goal', {}).get('content_sha256') == expected:
                return current
            raise HTTPException(409, 'research_already_active')
        task = host.controller.begin_research(session_id, request.goal,
            tuple(item.model_dump() for item in request.evidence), limits=request.limits, request_key=request.request_key)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if task.done():
            try: return await task
            except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        return latest(host, session_id)
    @router.get('')
    async def read(session_id: str):
        return latest(scope(session_id), session_id)
    @router.post('/cancel')
    async def cancel(session_id: str):
        host = scope(session_id)
        await host.controller.cancel_research(session_id)
        return latest(host, session_id)
    @router.get('/report')
    async def report(session_id: str):
        from fastapi.responses import Response
        from app.orchestration.research_delivery import ResearchDelivery
        from app.config import WORKSPACE_ROOT
        host = scope(session_id)
        current = latest(host, session_id)
        if 'case_id' not in current:
            raise HTTPException(404, 'research_report_not_available')
        try:
            manifest = ResearchDelivery(host.sessions, WORKSPACE_ROOT).export(current['case_id'])
        except (ValueError, OSError) as exc:
            raise HTTPException(409, 'research_report_not_ready_or_integrity_failed') from exc
        from pathlib import Path
        data = (Path(manifest['directory'])/'report.md').read_bytes()
        return Response(data, media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename=research-report.md',
                                 'X-Manifest-SHA256': manifest['manifest_sha256']})
    @router.post('/plan')
    async def prepare_plan(session_id: str, request: PreparePlan):
        host = scope(session_id)
        try:
            study = ResearchStore(host.sessions).read(request.case_id)
        except KeyError as exc:
            raise HTTPException(404, 'research_case_not_found') from exc
        if (study['session_id'] != session_id or study['project_id'] != host.sessions.get(session_id)['project_id']
                or study['status'] not in {'READY_FOR_PLAN', 'NEEDS_EVIDENCE'}):
            raise HTTPException(409, 'research_case_not_ready_for_plan')
        current = host.sessions.get(session_id).get('plan') or {}
        if study.get('proposal_case_id'):
            try:
                proposal = ResearchStore(host.sessions).read(study['proposal_case_id'])
            except KeyError as exc:
                # 领取后进程异常退出也不能重发已经可能提交的模型请求。
                raise HTTPException(409, 'research_plan_proposal_claim_interrupted') from exc
            outcome = proposal.get('outcome') or {}
            if (outcome.get('plan_id') == current.get('plan_id') and
                    current.get('revision') == outcome.get('expected_revision', -1) + 1):
                return {'plan': current, 'scientific_status': 'NOT_RUN', 'reused': True}
            raise HTTPException(409, 'research_plan_proposal_already_claimed')
        if study['goal']['session_plan_binding'] != [current.get('plan_id'), current.get('revision')]:
            raise HTTPException(409, 'research_plan_binding_changed')
        plan = await host.controller.prepare_plan(session_id, study['goal']['goal'], study, study['goal']['limits'])
        if plan is None:
            raise HTTPException(409, 'research_plan_proposal_unavailable')
        return {'plan': plan.model_dump(mode='json'), 'scientific_status': 'NOT_RUN'}
    @router.post('/rebind-draft')
    async def rebind(session_id: str):
        host = scope(session_id)
        try:
            return {'plan': host.controller.rebind_draft(session_id), 'scientific_status': 'NOT_RUN'}
        except ValueError as exc:
            raise HTTPException(409, 'draft_rebind_requires_unapproved_idle_plan') from exc
    return router
