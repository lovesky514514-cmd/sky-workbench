"""N02 授权的 Bridge 只读核查；不重启进程、不改模型、不调用求解。"""
from __future__ import annotations

from uuid import uuid4

from app.execution.expert_contracts import Contract, EvidenceRef, ExpertIntake, ExpertScope
from app.execution.expert_scheduler import ExpertScheduler, ExpertTool
from app.recovery.http_execution import HTTP_RECOVERY, HttpRetrySession
from app.recovery.ingest import digest
from app.recovery.schemas import FailureEvent, Identifier
from app.recovery.store import RecoveryConflict
from app.recovery.verifier import ConnectionVerifier


TOOL = 'recovery.probe_bridge_health'


class ProbeArgs(Contract):
    case_id: Identifier


async def probe_connection(store, workspace, case_id, project_id, session_id, bridge):
    record = store.recovery_record(case_id, project_id, session_id)
    if record['state'] not in {'PROPOSED','WAITING_APPROVAL','UNRESOLVED'}:
        raise RecoveryConflict('connection_probe_not_current')
    event = FailureEvent.model_validate(record['failure'])
    if event.origin.value not in {'JAVA_BRIDGE','COMSOL','API'}:
        raise RecoveryConflict('connection_probe_not_applicable')
    if not event.scope.session_id:
        event = event.model_copy(update={'scope': store.recovery_scope(session_id=session_id, project_id=project_id)})
    with store._recovery_write() as db:
        snapshot = store._http_guard(db, event.scope)
    operation_id = 'http.' + event.event_id
    operation = store.open_http_operation(operation_id, event.scope,
                                          digest(['GET','/health',bridge.configured_url(),None]), case_id=case_id)
    if operation['status'] == 'COMPLETED':
        # 重复 HTTP 请求只读取已完成观测，不创建第二个探测或新预算。
        return store.recovery_record(case_id, project_id, session_id)
    if operation['active'] or operation['status'] != 'READY':
        raise RecoveryConflict('connection_probe_in_flight_or_stopped')
    with store._recovery_write() as db:
        store._http_guard(db, event.scope)
        store._recovery_append(db, case_id, 'connection_snapshot', snapshot.model_dump(mode='json'))
    scope = ExpertScope(project_id=project_id, session_id=session_id, case_id=case_id,
                        plan_id=event.scope.plan_id or 'diagnostic-only', plan_revision=event.scope.plan_revision or 1,
                        run_id=case_id, attempt_id=uuid4().hex)
    task_id = uuid4().hex
    observed = None

    async def inspect(task, args):
        nonlocal observed
        if task.task_id != task_id or task.scope != scope or args.case_id != case_id:
            raise PermissionError('connection_probe_scope_mismatch')
        retry = HttpRetrySession(store, event.scope, operation_id, case_id=case_id)
        token = HTTP_RECOVERY.set(retry)
        try:
            payload = await bridge._request('GET', '/health')
        finally:
            HTTP_RECOVERY.reset(token)
        with store._recovery_write() as db:
            store._http_guard(db, event.scope)
        observed = payload
        return {'payload_digest': digest(payload), 'scientific_validation_passed': False}

    tools = {TOOL: ExpertTool(ProbeArgs, inspect)}
    scheduler = ExpertScheduler(capabilities={'recovery_diagnostics': tools}, tools=tools, max_concurrency=1)
    task = dict(schema_version=1, scope=scope.model_dump(), task_id=task_id, capability='recovery_diagnostics',
                objective='Observe the already connected Bridge without submitting work', requested_tools=(TOOL,),
                input_evidence_refs=(case_id, snapshot.snapshot_id), budget_estimate={'agent_calls':0,'solver_runs':0})
    evidence = [EvidenceRef(schema_version=1, evidence_id=ref, kind='reference', source_ref='session_recovery',
                            locator=f'recovery:{ref}', applicability='current connection observation only')
                for ref in task['input_evidence_refs']]

    async def execute(context):
        await context.call_tool(TOOL, {'case_id': case_id})
        return dict(schema_version=1, scope=scope.model_dump(), task_id=task_id, summary='Read-only connection observation',
                    claims=[{'claim_id':'connection', 'statement':'Connection observed; scientific result not certified',
                             'evidence_refs':task['input_evidence_refs']}])

    workspace.mkdir(parents=True, exist_ok=True)
    batch = await scheduler.run(ExpertIntake(scope=scope, tasks=[task], evidence=evidence), workspace_root=workspace, execute=execute)
    if task_id not in batch.results:
        store.stop_http_operation(operation_id, event.scope)
        raise RecoveryConflict('connection_probe_unverified')
    if observed is None:
        raise RecoveryConflict('connection_probe_unverified')
    # 判定在专家返回后进行，原子复核当前身份；不将连接通过转成故障已修复。
    with store._recovery_write() as db:
        after = store._http_guard(db, event.scope)
        observation = {'operation_id': operation_id, 'mode': 'observed_read_only',
                       'connection_verified': ConnectionVerifier().verify(observed, snapshot, after),
                       'scientific_validation_passed': False, 'solve_submissions': 0,
                       'evidence_ref': snapshot.snapshot_id, 'verification_ref': after.snapshot_id,
                       'payload_digest': digest(observed)}
        store._recovery_append(db, case_id, 'connection_snapshot', after.model_dump(mode='json'))
        store._recovery_append(db, case_id, 'connection_verification', observation)
    return store.recovery_record(case_id, project_id, session_id)
