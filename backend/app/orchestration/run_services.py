"""已验证运行底座的确定性服务；不拥有科研任务派发或自动返工权。"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from app.validators.execution_verdict import ExecutionVerdict, evaluate_evidence_contract, execution_verdict
from app.agent.model_card import generate_model_card
from app.comsol.bridge_process import java_bridge_process_manager
from app.comsol.java_bridge import java_bridge_client
from app.config import WORKSPACE_ROOT
from app.models import AgentMode, AgentState
from app.scientific.heat_recertification import corrected_heat_execution
from app.recovery.ingest import Signal, attached_signals, capture
from app.recovery.schemas import Cause, Origin
from app.session.state_grounding import ModelStateLedger, Provenance
from app.session.run_journal import StaleAttempt
from app.execution.graph import ExecutionGraph, NodeState

from types import SimpleNamespace
from app.scientific.errors import ScientificValidationError


class ExecutionServices:
    def __init__(self, runtime):
        self.runtime = runtime

    def frame(self, session_id, plan, run_id):
        from app.agent import runtime as env
        host = self.runtime
        frame = SimpleNamespace(session_id=session_id, plan=plan, run_id=run_id, attempt_id=None,
            execution=None, solution_report=None, verdict=None, repair_history=[],
            resource_samples=[], run_started=time.perf_counter(), precheck_ms=0, validator=None)
        frame.outer_timeline = host._timeline_start(session_id, category='SYSTEM', phase='Execution',
            label='Executing controller-dispatched ScientificPlan', tool='orchestrator.execution',
            input_summary={'run_id': run_id, 'plan_id': plan.plan_id, 'workflow_id': plan.workflow_id})

        async def record(sample):
            frame.resource_samples.append(sample)
            host.sessions.append_resource_sample(session_id, run_id, sample)
            if sample.get('thresholds', {}).get('events'):
                host.sessions.append_event(session_id, 'resource.warning', sample)

        frame.monitor = env.ResourceMonitor(WORKSPACE_ROOT, plan, record,
            process_ids=lambda: {'scientific_runtime': os.getpid(), **host.tools.process_ids(session_id)})
        return frame

    async def precheck(self, frame):
        from app.agent import runtime as env
        host, store = self.runtime, self.runtime.sessions
        store.assert_current(frame.run_id)
        frame.validator = env.validator_registry.get(frame.plan.workflow_id)
        if frame.validator is None:
            raise ScientificValidationError('No validator registered for this workflow')
        started = time.perf_counter()
        report = host._validate_plan(frame.plan)
        frame.precheck_ms = (time.perf_counter() - started) * 1000
        store.append_event(frame.session_id, 'validation.precheck', report.model_dump(mode='json'))
        if not report.passed:
            store.finish_run(frame.run_id, None, 'PrecheckRejected')
            await host._transition(frame.session_id, AgentState.VALIDATION_FAILED)
            store.add_message(frame.session_id, 'assistant', 'RUN stopped at PRECHECK.', AgentMode.RUN,
                              validation=report.model_dump(mode='json'))
            return False
        return True

    async def execute_once(self, frame):
        host, store = self.runtime, self.runtime.sessions
        store.assert_current(frame.run_id, frame.attempt_id)
        store.assert_plan_binding(frame.session_id, frame.plan.model_dump(mode='json'))
        host._record_plan_assumptions(frame.session_id, frame.run_id, frame.attempt_id, frame.plan)
        if java_bridge_process_manager.status().get('running'):
            await java_bridge_process_manager.stop()
        if frame.plan.workflow_id in {'heat_transfer_2d_stationary', 'tds_2d_stationary_diffusion'}:
            graph = ExecutionGraph.small_supported(frame.run_id, frame.attempt_id, str(frame.plan.workflow_id))
            host.live_interrupt.register(frame.session_id, graph, replace=True)
            host.live_interrupt.start(frame.run_id, 'build')
        await host._transition(frame.session_id, AgentState.BUILDING)
        tool = {
            'heat_transfer_2d_stationary': 'comsol.heat_transfer_2d',
            'tds_2d_stationary_diffusion': 'comsol.tds_2d_stationary_diffusion',
            'tds_2d_transient_closed': 'comsol.tds_2d_transient_closed',
            'heat_transfer_3d_cooling': 'comsol.heat_transfer_3d_cooling',
            'solid_mechanics_uniaxial_bar': 'comsol.solid_mechanics_uniaxial_bar',
            'electrochem_primary_current': 'comsol.electrochem_primary_current',
            'lithium_ion_battery_baseline': 'comsol.lithium_ion_battery_baseline',
            'heat_tds_temperature_coupling': 'comsol.heat_tds_temperature_coupling',
        }[str(frame.plan.workflow_id)]
        attempt_id = frame.attempt_id
        frame.monitor.start()
        try:
            frame.execution = await host.tools.execute(tool,
                {'run_id': frame.run_id, 'attempt_id': attempt_id, 'session_id': frame.session_id,
                 'workspace': str(WORKSPACE_ROOT), 'plan': frame.plan.execution_payload(), 'attempt': 1},
                lambda event: host._handle_attempt_event(frame.session_id, frame.run_id, attempt_id, event), retries=0)
        finally:
            await frame.monitor.stop()
        store.assert_current(frame.run_id, attempt_id)
        if frame.execution.get('attempt_id', attempt_id) != attempt_id:
            raise StaleAttempt('worker result belongs to a stale attempt')
        frame.execution['attempt_id'] = attempt_id
        store.update_run(frame.run_id, attempt_id, artifacts=frame.execution.get('artifacts', {}))

    async def validate(self, frame):
        frame.solution_report = frame.validator.validate_execution(frame.plan, frame.execution)
        derived = await asyncio.to_thread(evaluate_evidence_contract, frame.execution, frame.run_id, frame.attempt_id)
        frame.verdict = execution_verdict(str(frame.plan.workflow_id), frame.run_id, frame.execution,
            frame.solution_report, expected_attempt_id=frame.attempt_id, derived_evidence=derived)
        self.runtime.sessions.append_event(frame.session_id, 'validation.solution',
            frame.verdict.validation_report(frame.solution_report))
        return frame.verdict

    async def postprocess(self, frame):
        from app.agent import runtime as env
        self = self.runtime
        session_id, plan, run_id = frame.session_id, frame.plan, frame.run_id
        attempt_id, execution, solution_report = frame.attempt_id, frame.execution, frame.solution_report
        verdict, repair_history = frame.verdict, frame.repair_history
        monitor, resource_samples = frame.monitor, frame.resource_samples
        run_started, outer_timeline, precheck_ms = frame.run_started, frame.outer_timeline, frame.precheck_ms
        validator = frame.validator
        verdict_report = verdict.validation_report(solution_report)
        execution["repair_history"] = repair_history
        final_profile = await asyncio.to_thread(env.hardware_profile, WORKSPACE_ROOT, {"scientific_runtime": os.getpid(), **self.tools.process_ids(session_id)})
        validation_metrics = execution.get("validation", {})
        stage_b = env.assess_risk(
            plan,
            final_profile,
            stage="post_mesh",
            mesh_elements=validation_metrics.get("element_count"),
            dof=validation_metrics.get("dof"),
        )
        execution["resource"] = {
            "stage_b": stage_b,
            "sample_count": len(resource_samples),
            "peak_process_memory_gb": max(
                (
                    float(item.get("rss_gb") or 0)
                    for sample in resource_samples
                    for item in sample.get("profile", {}).get("process_memory", {}).values()
                ),
                default=0.0,
            ),
        }
        self.sessions.append_event(session_id, "resource.post_mesh", stage_b)
        execution.setdefault("performance", {})["precheck_ms"] = precheck_ms
        execution["artifacts"]["execution_log"] = execution.get("log_path", "")
        boundary_request = self._pending_boundary_reviews.pop(run_id, None)
        bridge_required = plan.workflow_id == 'tds_2d_stationary_diffusion' or (
            plan.workflow_id == 'heat_transfer_2d_stationary' and boundary_request is not None)
        if bridge_required:
            try:
                if boundary_request is not None:
                    if boundary_request['attempt_id'] != attempt_id:
                        raise StaleAttempt('boundary review queued for a stale attempt')
                    self.live_interrupt.register_heat_boundaries(run_id, execution.get('boundary_map') or {})
                await self._verify_java_bridge(session_id, execution)
                if boundary_request is not None:
                    await self.live_interrupt.request_boundary_review(
                        run_id, boundary_request['boundary_object_id'],
                        boundary_request['proposed_value'], boundary_request['unit'],
                        boundary_request['reason'])
                    self.live_interrupt.request_pause(run_id)
                boundary_state = self.live_interrupt.safe_boundary(run_id, "build")
                if boundary_state is NodeState.PAUSED:
                    self.sessions.update_run(run_id, attempt_id, phase="PAUSED")
                    await self._transition(session_id, AgentState.PAUSED)
                    await self.live_interrupt.wait_for_resume(run_id)
                graph = self.live_interrupt.graph(run_id)
                if graph.corrections:
                    invalidation_issue = "human_correction_invalidated_pre_correction_scientific_evidence"
                    verdict = verdict.invalidate_scientific_evidence(invalidation_issue)
                    evidence_contract = execution.get("evidence_contract")
                    if isinstance(evidence_contract, dict):
                        evidence_issues = list(evidence_contract.get("issues") or [])
                        if invalidation_issue not in evidence_issues:
                            evidence_issues.append(invalidation_issue)
                        execution["evidence_contract"] = {
                            **evidence_contract, "passed": False, "issues": evidence_issues,
                        }
                    self.sessions.append_event(session_id, "validation.invalidated_by_human_correction", {
                        "run_id": run_id, "attempt_id": attempt_id,
                        "invalidated_nodes": [name for name, node in graph.nodes.items() if node.state is NodeState.INVALIDATED],
                        "reason": "Pre-correction numerical and scientific evidence cannot certify the corrected model",
                    })
                    if (plan.workflow_id != 'heat_transfer_2d_stationary'
                            or len(graph.corrections) != 1
                            or graph.corrections[0].get('path') not in {
                                'boundary_conditions.left', 'boundary_conditions.right'}):
                        raise ScientificValidationError(
                            'This correction has no supported fresh scientific recertification path')
                    self.sessions.assert_current(run_id, attempt_id)
                    await self._transition(session_id, AgentState.BUILD_VALIDATION)
                    tree = await java_bridge_client.model_tree()
                    left = await java_bridge_client.boundary_feature(
                        component_tag='comp1', physics_tag='ht',
                        feature_tag='temp_left', property_key='T0')
                    right = await java_bridge_client.boundary_feature(
                        component_tag='comp1', physics_tag='ht',
                        feature_tag='temp_right', property_key='T0')
                    await self._transition(session_id, AgentState.SOLVING)
                    attempt_workspace = WORKSPACE_ROOT / 'models' / 'attempts' / run_id / attempt_id

                    def check_field_scope():
                        self.sessions.assert_current(run_id, attempt_id)
                        if java_bridge_process_manager.status().get('owner_id') != run_id:
                            raise PermissionError('bridge_field_owner_mismatch')

                    field = await java_bridge_client.heat_field(run_id, attempt_id, attempt_workspace, check_field_scope)
                    resumed = next((item for item in reversed(self.sessions.get(session_id)['events'])
                                    if item.get('type') == 'execution.resumed'
                                    and (item.get('payload') or {}).get('run_id') == run_id
                                    and (item.get('payload') or {}).get('attempt_id') == attempt_id), None)
                    study_date = int(((resumed or {}).get('payload') or {}).get('study', {}).get('last_computation_date') or 0)
                    self.sessions.assert_current(run_id, attempt_id)
                    exported = await java_bridge_client.export_corrected_heat(run_id, attempt_id)
                    corrected_plan, corrected_execution = corrected_heat_execution(
                        plan=plan, original=execution, correction=graph.corrections[0],
                        left_readback=left, right_readback=right, field=field,
                        exported=exported, study_date=study_date,
                        run_id=run_id, attempt_id=attempt_id,
                        attempt_workspace=attempt_workspace, model_tree=tree,
                    )
                    self.sessions.assert_current(run_id, attempt_id)
                    corrected_plan.revision = plan.revision+1
                    self.sessions.commit_plan_revision(session_id,corrected_plan.model_dump(mode='json'),plan.revision)
                    self.sessions.append_event(session_id,'run.reviewed_plan_rebound',{
                        'run_id':run_id,'attempt_id':attempt_id,'revision':corrected_plan.revision})
                    execution = corrected_execution
                    plan = corrected_plan
                    self.sessions.update_run(run_id, attempt_id, artifacts=execution['artifacts'])
                    image = Path(execution['artifacts']['temperature_image'])
                    image_digest = hashlib.sha256(image.read_bytes()).hexdigest()
                    self.sessions.append_event(session_id, 'result.available', {
                        'run_id': run_id, 'attempt_id': attempt_id,
                        'artifact_id': f'artifact-{run_id}-{attempt_id}-temperature_image',
                        'path': str(image), 'sha256': image_digest,
                        'variable': 'Temperature', 'unit': 'K',
                        'minimum': execution['validation']['temperature_min_K'],
                        'maximum': execution['validation']['temperature_max_K'],
                        'evidence_state': 'pending',
                    })
                    await self._transition(session_id, AgentState.SOLUTION_VALIDATION)
                    solution_report = validator.validate_execution(plan, execution)
                    derived_evidence = await asyncio.to_thread(
                        evaluate_evidence_contract, execution, run_id, attempt_id)
                    verdict = execution_verdict(
                        str(plan.workflow_id), run_id, execution, solution_report,
                        expected_attempt_id=attempt_id, derived_evidence=derived_evidence)
                    self.sessions.append_event(session_id, 'validation.solution', {
                        'run_id': run_id, 'attempt_id': attempt_id,
                        **verdict.validation_report(solution_report)})
                    self.sessions.append_event(session_id, 'evidence.contract', {
                        'run_id': run_id, 'attempt_id': attempt_id,
                        **execution['evidence_contract']})
                    if not verdict.completed:
                        raise ScientificValidationError(
                            'Corrected Heat numerical validation or evidence contract did not pass: '
                            + '; '.join(verdict.issues))
                for node_id in ("solve", "validate", "export"):
                    self.live_interrupt.start(run_id, node_id)
                    self.live_interrupt.safe_boundary(run_id, node_id)
            finally:
                if java_bridge_process_manager.status().get('running') or java_bridge_process_manager.status().get('state') == 'starting':
                    await java_bridge_process_manager.stop()
        else:
            self._pending_boundary_reviews.pop(run_id, None)
        frame.plan, frame.execution, frame.solution_report, frame.verdict = plan, execution, solution_report, verdict

    async def publish(self, frame):
        self = self.runtime
        session_id, plan, run_id = frame.session_id, frame.plan, frame.run_id
        attempt_id, execution, solution_report = frame.attempt_id, frame.execution, frame.solution_report
        verdict, repair_history = frame.verdict, frame.repair_history
        monitor, resource_samples = frame.monitor, frame.resource_samples
        run_started, outer_timeline, precheck_ms = frame.run_started, frame.outer_timeline, frame.precheck_ms
        validator = frame.validator
        verdict_report = verdict.validation_report(solution_report)
        self.sessions.assert_current(run_id, attempt_id)
        lifecycle = {'run_id': run_id, 'attempt_id': attempt_id, 'outcome': 'Completed', 'state': 'COMPLETED', 'cancel_requested': False}
        execution['run_lifecycle'] = lifecycle
        model_state = execution["model_state"]
        model_state['run_lifecycle'] = lifecycle
        model_state["validation"] = verdict_report
        model_state["execution_verdict"] = verdict.as_dict()
        model_state["evidence_contract"] = execution.get("evidence_contract", {})
        model_state["sources"] = [source.model_dump(mode="json") for source in plan.sources]
        self.sessions.set_model(session_id, model_state)
        ledger = ModelStateLedger(self.sessions)
        for field in ('geometry', 'materials', 'physics', 'parameters', 'selections', 'boundary_conditions', 'mesh', 'study', 'solver', 'results'):
            if field in model_state:
                ledger.append(session_id, f'model.{field}', model_state[field], Provenance.COMSOL_READBACK,
                              run_id=run_id, attempt_id=attempt_id)
        ledger.append(session_id, 'execution.verdict', verdict.as_dict(), Provenance.DETERMINISTIC_CHECK,
                      run_id=run_id, attempt_id=attempt_id)
        ledger.append(session_id, 'execution.validation', execution.get('validation', {}), Provenance.SOLVER_RESULT,
                      run_id=run_id, attempt_id=attempt_id)
        self._admit_completed_run_memory(session_id, run_id, attempt_id, plan, verdict, execution)
        current_state = AgentState(self.sessions.get(session_id)["state"])
        if current_state != AgentState.EXPORTING:
            await self._transition(session_id, AgentState.EXPORTING)
        await self._transition(session_id, AgentState.REPORTING)
        card_started = time.perf_counter()
        card_path = generate_model_card(
            run_id=run_id,
            session_id=session_id,
            plan=plan.model_dump(mode="json"),
            execution=execution,
            validation_report=verdict_report,
            verdict=verdict,
            repair_history=repair_history,
        )
        execution["performance"]["model_card_ms"] = round((time.perf_counter() - card_started) * 1000, 3)
        execution["performance"]["runtime_total_ms"] = round((time.perf_counter() - run_started) * 1000, 3)
        # Refresh the card once so its performance table includes reporting time.
        card_path = generate_model_card(
            run_id=run_id,
            session_id=session_id,
            plan=plan.model_dump(mode="json"),
            execution=execution,
            validation_report=verdict_report,
            verdict=verdict,
            repair_history=repair_history,
        )
        execution["artifacts"]["model_card"] = str(card_path)
        # 最终审核和报告生成后再次冻结全部实际文件，导出不能拿路径代替证据。
        evidence = await asyncio.to_thread(evaluate_evidence_contract, execution, run_id, attempt_id)
        if evidence['passed'] is not True:
            raise ScientificValidationError('final_delivery_evidence_invalid')
        self.sessions.assert_current(run_id, attempt_id)
        execution['evidence_contract'] = evidence
        model_state['evidence_contract'] = evidence
        self.sessions.set_model(session_id, model_state)
        self.sessions.update_run(run_id, attempt_id, artifacts=execution['artifacts'])
        lifecycle = self.sessions.finish_run(run_id, attempt_id, 'Completed')
        self.sessions.append_event(session_id, "run.completed", {"run_id": run_id, 'attempt_id': attempt_id, 'run_lifecycle': lifecycle, "plan_id": plan.plan_id, "artifacts": execution["artifacts"], "evidence_contract": execution.get("evidence_contract", {}), **verdict.fields(), "validation": execution["validation"], "build_readback": execution.get("build_readback"), "boundary_map": execution.get("boundary_map"), "validation_report": verdict_report, "repair_history": repair_history, "tool": execution["tool"], "duration_ms": execution["duration_ms"], "performance": execution["performance"], "java_bridge": execution.get("java_bridge"), "resource": execution.get("resource")})
        await self._transition(session_id, AgentState.COMPLETED)
        self._timeline_finish(session_id, outer_timeline, status="success", output_summary=verdict.summary(), duration_ms=execution["duration_ms"], fallback=bool(repair_history), raw_output=execution.get("raw_log", ""), verdict=verdict)
        primary_field = execution["artifacts"].get("concentration_image") or execution["artifacts"].get("temperature_image") or execution["artifacts"].get("stress_image") or execution["artifacts"].get("potential_image")
        self.sessions.add_message(session_id, "assistant", f"RUN completed from ScientificPlan `{plan.plan_id}`. {verdict.summary()}\n\n- Model: `{execution['artifacts']['model']}`\n- Primary field: `{primary_field}`\n- Model Card 2.0: `{card_path}`", AgentMode.RUN, plan_id=plan.plan_id, run_id=run_id, attempt_id=attempt_id, run_lifecycle=lifecycle, artifacts=execution["artifacts"], evidence_contract=execution.get("evidence_contract", {}), **verdict.fields(), validation=execution["validation"], validation_report=verdict_report, repair_history=repair_history)

    async def fail(self, frame, exc):
        self = self.runtime
        session_id, plan, run_id = frame.session_id, frame.plan, frame.run_id
        attempt_id, execution, solution_report = frame.attempt_id, frame.execution, frame.solution_report
        verdict, repair_history = frame.verdict, frame.repair_history
        monitor, resource_samples = frame.monitor, frame.resource_samples
        run_started, outer_timeline, precheck_ms = frame.run_started, frame.outer_timeline, frame.precheck_ms
        validator = frame.validator
        self._pending_boundary_reviews.pop(run_id, None)
        await monitor.stop()
        run = self.sessions.get_run(run_id)
        if not isinstance(exc, asyncio.CancelledError) and not run['cancel_requested'] and not getattr(exc, '_sky_controller_captured', False):
            origin = Origin.SCIENCE if isinstance(exc, ScientificValidationError) else Origin.RUNTIME
            code = Cause.EVIDENCE_INVALID if origin == Origin.SCIENCE else 'runtime_failed'
            signal = Signal(origin, 'scientific_run', code)
            scope = dict(session_id=session_id, plan_id=plan.plan_id, plan_revision=plan.revision,
                         run_id=run_id, attempt_id=attempt_id)
            capture(self.sessions, signal, **scope)
            for related in attached_signals(exc):
                capture(self.sessions, related, **scope, correlation=signal.event_id, parent=signal.event_id)
        if run['outcome'] is not None:
            self.sessions.append_event(session_id, 'runtime.warning', {'message': f'Post-terminal publication issue: {exc}'})
            return
        outcome = 'Canceled' if run['cancel_requested'] else 'Interrupted' if isinstance(exc, asyncio.CancelledError) else 'FatalFailure'
        lifecycle = self.sessions.finish_run(run_id, attempt_id, outcome)
        outcome = lifecycle['outcome']
        verdict = (verdict or ExecutionVerdict(run_id, str(plan.workflow_id), False, False, False, ())).reject(f"execution_failed:{exc}")
        if outcome == 'FatalFailure':
            await self._transition_to_failed(session_id, str(exc))
        else:
            self.sessions.set_state(session_id, AgentState(lifecycle['state']), error=str(exc))
        execution = {**(execution or {}), 'run_lifecycle': lifecycle, 'attempt_id': attempt_id}
        self.sessions.set_model(session_id, {**execution.get('model_state', {}), 'run_lifecycle': lifecycle, 'execution_verdict': verdict.as_dict(), 'validation': verdict.validation_report(solution_report) if solution_report else verdict.fields()})
        if attempt_id and outcome in {'FatalFailure', 'Interrupted'}:
            try:
                self._admit_failure_memory(
                    session_id=session_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    plan=plan,
                    outcome=outcome,
                    failure_class=type(exc).__name__,
                    failed_phase=str(lifecycle.get('phase') or run.get('phase') or 'UNKNOWN'),
                    attempted_action=(repair_history[-1].get('outcome', 'NONE') if repair_history else 'NONE'),
                )
            except (OSError, ValueError, TypeError) as memory_error:
                self.sessions.append_event(session_id, "runtime.warning", {
                    "message": f"Failure memory unavailable: {memory_error}",
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                })
        failure_artifacts: dict[str, str] = {}
        try:
            card_path = generate_model_card(run_id=run_id, session_id=session_id, plan=plan.model_dump(mode="json"),
                execution=execution or {}, validation_report=verdict.validation_report(solution_report) if solution_report else {},
                repair_history=repair_history, verdict=verdict)
            failure_artifacts["model_card"] = str(card_path)
        except (OSError, ValueError, TypeError) as card_error:
            self.sessions.append_event(session_id, "runtime.warning", {"message": f"Failure Model Card unavailable: {card_error}"})
        event_kind = 'run.canceled' if outcome == 'Canceled' else 'run.interrupted' if outcome == 'Interrupted' else 'run.failed'
        self.sessions.append_event(session_id, event_kind, {"run_id": run_id, "attempt_id": attempt_id, 'run_lifecycle': lifecycle, "artifacts": failure_artifacts, **verdict.fields()})
        self._timeline_finish(session_id, outer_timeline, status="error", output_summary=f"{verdict.summary()} {exc}", duration_ms=round((time.perf_counter() - run_started) * 1000), fallback=bool(repair_history), verdict=verdict)
        self.sessions.add_message(session_id, "assistant", f"RUN {outcome}. {verdict.summary()} {exc}", AgentMode.RUN, plan_id=plan.plan_id, run_id=run_id, attempt_id=attempt_id, run_lifecycle=lifecycle, artifacts=failure_artifacts, **verdict.fields(), validation_report=verdict.validation_report(solution_report) if solution_report else {"scope": "solution", "passed": False, "checks": [], **verdict.fields()}, repair_history=repair_history)
