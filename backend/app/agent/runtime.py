"""Scientific Agent orchestration with typed plans and validation gates."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import ValidationError

from app.agent.context_builder import context_builder
from app.agent.intent_compiler import compile_intent
from app.validators.execution_verdict import ExecutionVerdict, evaluate_evidence_contract, execution_verdict
from app.agent.model_card import generate_model_card
from app.agent.planner import PlanGenerationFailure, ScientificPlanner, scientific_planner
from app.agent.state import InvalidStateTransition, assert_transition
from app.agent.tool_registry import ToolRegistry, tool_registry
from app.comsol.bridge_process import java_bridge_process_manager
from app.comsol.java_bridge import java_bridge_client
from app.config import WORKSPACE_ROOT, settings_store
from app.knowledge.retrieval import BasicRetriever, retriever
from app.models import AgentMode, AgentState
from app.providers.manager import ProviderManager, provider_manager
from app.scientific.schemas import ScientificPlan
from app.scientific.heat_recertification import corrected_heat_execution, normalize_heat_core
from app.scientific.resource_guard import ResourceMonitor, assess_risk, hardware_profile
from app.session.store import SessionStore, session_store, utc_now
from app.session.state_grounding import ModelStateLedger, Provenance, rebuild_context_snapshot
from app.session.run_journal import RunCanceled, StaleAttempt
from app.session.assumptions import AssumptionLedger
from app.memory.store import MemoryAdmissionPolicy, MemoryCandidate, MemoryStore, MemoryType
from app.recovery.controller import ControllerInput, RecoverabilityActionController
from app.execution.graph import ExecutionGraph, NodeState, TypedCorrection
from app.execution.live_interrupt import LiveInterruptAdapter, comsol_parameter_name
from app.validators import plan_quality_validator, validator_registry
from app.validators.base_validator import ValidationReport


class ScientificValidationError(RuntimeError):
    pass


class AgentRuntime:
    def __init__(
        self,
        sessions: SessionStore = session_store,
        providers: ProviderManager = provider_manager,
        knowledge: BasicRetriever = retriever,
        planner: ScientificPlanner = scientific_planner,
        tools: ToolRegistry = tool_registry,
    ) -> None:
        self.sessions = sessions
        self.providers = providers
        self.knowledge = knowledge
        self.planner = planner
        self.tools = tools
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_runs: dict[str, str] = {}
        self._pending_boundary_reviews: dict[str, dict[str, Any]] = {}
        self.recovery = RecoverabilityActionController()
        self.live_interrupt = LiveInterruptAdapter(sessions, java_bridge_client)
        if isinstance(self.tools, ToolRegistry): self.tools.sessions = sessions

    async def cancel_run(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        intent = self.sessions.request_cancel(session_id, force=force)
        if not intent.get('cancel_requested'): return intent
        result = await self.tools.cancel(session_id, force=force)
        result = {**intent, **result}
        self.sessions.append_event(session_id, 'run.cancel', result)
        return result

    def _active_interrupt_identity(self, session_id: str) -> tuple[str, str]:
        run = self.sessions.latest_run(session_id)
        if not run or run.get("outcome") is not None or not run.get("attempt_id"):
            raise RuntimeError("no active interruptible run")
        self.live_interrupt.graph(run["run_id"])
        return str(run["run_id"]), str(run["attempt_id"])

    async def pause_live_run(self, session_id: str) -> dict[str, Any]:
        run_id, _ = self._active_interrupt_identity(session_id)
        self.live_interrupt.request_pause(run_id)
        return {"status": "PAUSE_REQUESTED", **self.live_interrupt.status(run_id)}

    async def pause_capability(self, session_id: str) -> str:
        """Requestable only with this process's task, graph and owned live Bridge."""
        run = self.sessions.latest_run(session_id)
        task = self._tasks.get(session_id)
        if not run or run.get('outcome') is not None or run.get('cancel_requested') \
                or not task or task.done() or not run.get('attempt_id'):
            return 'unavailable'
        try:
            graph = self.live_interrupt.graph(str(run['run_id']))
        except KeyError:
            return 'unavailable'
        if graph.attempt_id != run['attempt_id'] or graph.workflow_id not in {
            'heat_transfer_2d_stationary', 'tds_2d_stationary_diffusion',
        }:
            return 'unavailable'
        if not any(node.state in {NodeState.RUNNING, NodeState.PAUSE_REQUESTED}
                   for node in graph.nodes.values()):
            return 'unavailable'
        bridge_status = java_bridge_process_manager.status()
        if bridge_status.get('running') is not True or bridge_status.get('owner_id') != run['run_id']:
            return 'unavailable'
        health = await java_bridge_client.health()
        return 'available' if health.get('real_comsol') is True \
            and health.get('model_connected') is True else 'unavailable'

    async def request_live_parameter_review(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a typed review only from the current attached COMSOL readback."""
        from app.adapter.rc42_contract import object_id
        run_id, attempt_id = self._active_interrupt_identity(session_id)
        if AgentState(self.sessions.get(session_id)['state']) is not AgentState.PAUSED:
            raise RuntimeError('review requires a verified PAUSED boundary')
        graph = self.live_interrupt.graph(run_id)
        path = str(payload['path'])
        plan_name = path.removeprefix('parameters.')
        if not path.startswith('parameters.'):
            raise ValueError('typed parameter path required')
        health = await java_bridge_client.health()
        if not health.get('real_comsol') or not health.get('model_connected'):
            raise RuntimeError('real COMSOL model attachment is not verified')
        readback = await java_bridge_client.parameters()
        tag = comsol_parameter_name(graph.workflow_id, plan_name)
        current = (readback.get('parameters') or {}).get(tag)
        if readback.get('model_state_verified') is not True or not isinstance(current, str):
            raise RuntimeError('current parameter readback is unavailable')
        return self.sessions.create_review(session_id, {
            'reviewId': str(uuid.uuid4()), 'runId': run_id, 'attemptId': attempt_id,
            'affectedObjectId': object_id(session_id, 'parameter', plan_name),
            'currentValue': current, 'proposedValue': payload['proposed_value'],
            'unit': payload['unit'], 'reason': payload['reason'],
            'downstreamImpact': 'The changed model requires COMSOL readback and a new scientific validation.',
            'allowedActions': ['approve', 'modify', 'reject', 'defer'],
        })

    async def queue_live_boundary_review(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Queue a request; create the actual review only after real Bridge readback."""
        from app.adapter.rc42_contract import object_id
        from app.execution.boundary_feature import kelvin_expression
        run_id, attempt_id = self._active_interrupt_identity(session_id)
        self.sessions.assert_current(run_id, attempt_id)
        graph = self.live_interrupt.graph(run_id)
        if graph.workflow_id != 'heat_transfer_2d_stationary':
            raise ValueError('literal Heat boundary correction only')
        if graph.attempt_id != attempt_id or graph.pause_requested:
            raise RuntimeError('boundary review must precede pause on the current attempt')
        boundary_id = str(payload['boundary_object_id'])
        if boundary_id not in {object_id(session_id, 'boundary', side) for side in ('left', 'right')}:
            raise ValueError('boundary object is not part of this Heat model')
        if payload['unit'] != 'K':
            raise ValueError('Heat boundary correction must use K')
        kelvin_expression(payload['proposed_value'])
        if run_id in self._pending_boundary_reviews:
            raise RuntimeError('a boundary review is already queued for this run')
        queued = {'run_id': run_id, 'attempt_id': attempt_id, **payload}
        self._pending_boundary_reviews[run_id] = queued
        self.sessions.append_event(session_id, 'boundary.review_queued', {
            'run_id': run_id, 'attempt_id': attempt_id, 'boundary_object_id': boundary_id,
            'status': 'awaiting_real_comsol_readback',
        })
        return {'status': 'QUEUED_AWAITING_READBACK', 'runId': run_id, 'attemptId': attempt_id,
                'boundaryObjectId': boundary_id, 'reviewRequest': None}

    async def correct_live_boundary(self, session_id: str, review_id: str) -> dict[str, Any]:
        run_id, attempt_id = self._active_interrupt_identity(session_id)
        self.sessions.assert_current(run_id, attempt_id)
        if AgentState(self.sessions.get(session_id)['state']) is not AgentState.PAUSED:
            raise RuntimeError('boundary mutation requires PAUSED')
        owned = java_bridge_process_manager.status()
        if owned.get('running') is not True or owned.get('owner_id') != run_id:
            raise RuntimeError('Workbench-owned Bridge for the current run is unavailable')
        return await self.live_interrupt.apply_boundary_correction(run_id, review_id)

    async def correct_live_run(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id, _ = self._active_interrupt_identity(session_id)
        if AgentState(self.sessions.get(session_id)["state"]) is not AgentState.PAUSED:
            raise RuntimeError("typed correction requires a verified PAUSED boundary")
        correction = TypedCorrection(
            path=str(payload["path"]), value=float(payload["value"]),
            unit=str(payload["unit"]), reason=str(payload["reason"]),
        )
        return await self.live_interrupt.apply_parameter_correction(
            run_id, correction, review_id=payload.get('review_id'))

    async def resume_live_run(self, session_id: str) -> dict[str, Any]:
        run_id, attempt_id = self._active_interrupt_identity(session_id)
        if AgentState(self.sessions.get(session_id)["state"]) is not AgentState.PAUSED:
            raise RuntimeError("resume requires a verified PAUSED boundary")
        result = await self.live_interrupt.resume(run_id)
        self.sessions.update_run(run_id, attempt_id, phase="BUILDING")
        await self._transition(session_id, AgentState.BUILDING)
        return result

    async def shutdown(self) -> None:
        for sid, task in list(self._tasks.items()):
            if task.done(): continue
            try:
                result = await self.tools.cancel(sid, force=True)
                self.sessions.append_event(sid, 'run.shutdown_cleanup', result)
            finally:
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self.reconcile()

    async def reconcile(self):
        results = await asyncio.to_thread(self.sessions.reconcile_active_runs)
        for lifecycle in results:
            run = self.sessions.get_run(lifecycle['run_id'])
            sid = run['session_id']
            plan = self.sessions.get(sid).get('plan') or {}
            verdict = ExecutionVerdict(run['run_id'], str(plan.get('workflow_id', '')), False, False, False, ('execution_interrupted',))
            artifacts = {}
            try:
                card = generate_model_card(run_id=run['run_id'], session_id=sid, plan=plan,
                    execution={'run_lifecycle': lifecycle, 'attempt_id': run['attempt_id']},
                    validation_report={}, repair_history=[], verdict=verdict, recovery=True)
                artifacts['model_card'] = str(card)
            except (OSError, ValueError) as exc:
                self.sessions.append_event(sid, 'runtime.warning', {'message': f'Recovery card unavailable: {exc}'})
            self.sessions.add_message(sid, 'assistant', 'RUN ' + lifecycle['outcome'] + ': completion was not established; previous attempt is revoked.',
                                      AgentMode.RUN, run_lifecycle=lifecycle, artifacts=artifacts, **verdict.fields())
        return results

    async def handle_message(self, session_id: str, mode: AgentMode, content: str) -> dict[str, Any]:
        self._begin_turn(session_id, mode, content)
        if mode == AgentMode.ASK:
            await self._ask(session_id, content)
        elif mode == AgentMode.PLAN:
            await self._plan(session_id, content)
        else:
            await self._start_run(session_id)
        return self.sessions.get(session_id)

    async def handle_message_stream(
        self, session_id: str, mode: AgentMode, content: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream visible answer text; hidden model reasoning is never exposed."""

        self._begin_turn(session_id, mode, content)
        if mode != AgentMode.ASK:
            if mode == AgentMode.PLAN:
                await self._plan(session_id, content)
            else:
                await self._start_run(session_id)
            yield {"type": "session", "session": self.sessions.get(session_id)}
            return
        async for event in self._ask_stream(session_id, content):
            yield event
        yield {"type": "session", "session": self.sessions.get(session_id)}

    async def update_plan(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.sessions.exists(session_id):
            raise KeyError(session_id)
        plan = ScientificPlan.model_validate(payload)
        plan.updated_at = utc_now()
        report = self._validate_plan(plan)
        quality = plan_quality_validator.apply(plan, enforce_prompt_fidelity=False)
        plan.precheck_summary = report.model_dump(mode="json") if report else None
        plan.runnable = bool(plan.workflow_id and report and report.passed and quality.status != "INVALID")
        self.sessions.set_plan(session_id, plan.model_dump(mode="json"))
        self.sessions.append_event(
            session_id,
            "plan.validated",
            {"plan_id": plan.plan_id, "valid": plan.runnable, "report": plan.precheck_summary},
        )
        await self._transition(session_id, AgentState.PLAN_READY)
        return self.sessions.get(session_id)

    def _begin_turn(self, session_id: str, mode: AgentMode, content: str) -> None:
        if not self.sessions.exists(session_id):
            raise KeyError(session_id)
        self.sessions.set_mode(session_id, mode)
        self.sessions.set_title_from_prompt(session_id, content)
        self.sessions.add_message(session_id, "user", content, mode)

    def _retrieve(self, session_id: str, prompt: str, limit: int) -> list[dict[str, Any]]:
        knowledge_status = self.knowledge.status()
        timeline_id = self._timeline_start(
            session_id,
            category="RAG",
            phase="Research",
            label="Retrieving traceable scientific evidence",
            tool="knowledge.hybrid_search",
            input_summary={"query": prompt[:400], "limit": limit, "mode": knowledge_status["active_mode"]},
        )
        started = time.perf_counter()
        if hasattr(self.knowledge, "search_detailed"):
            detailed = self.knowledge.search_detailed(prompt, limit=limit)
            citations = detailed["results"]
            trace = detailed.get("trace", {})
        else:
            citations = self.knowledge.search(prompt, limit=limit)
            trace = {"mode": knowledge_status["active_mode"]}
        self._timeline_finish(
            session_id,
            timeline_id,
            status="success",
            output_summary=f"Found {len(citations)} citable evidence unit(s); path={trace.get('mode')}; dense={trace.get('dense_backend', 'n/a')}; reranker={trace.get('reranker_status', 'n/a')}",
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return citations

    async def _ask(self, session_id: str, prompt: str) -> None:
        citations = self._retrieve(session_id, prompt, 5)
        bundle = context_builder.build(
            session=self.sessions.get(session_id),
            context_snapshot=rebuild_context_snapshot(self.sessions, session_id).as_dict(),
            mode="ASK",
            prompt=prompt,
            citations=citations,
            system_prompt="Answer as a cautious COMSOL scientific assistant. ASK must not execute tools. Cite only supplied Citation objects. Never invent parameter sources or expose hidden chain-of-thought. For numeric properties, distinguish AI knowledge, RAG source, benchmark assumption and verified source; say Unverified when the evidence does not verify the value.",
        )
        timeline_id = self._timeline_start(session_id, category="LLM", phase="Generating", label="Generating a source-grounded answer", tool="provider.chat", input_summary=bundle.summary())
        started = time.perf_counter()
        response = await self.providers.complete(bundle.messages)
        if response is None:
            answer = "No local AI provider is ready. Use Settings → AI Providers → Set up free local AI for Ollama or LM Studio. Retrieval remains available and no COMSOL action was executed."
            provider, model, status = "none", "none", "warning"
        else:
            answer, provider, model, status = response.text, response.provider_label, response.model, "success"
        self._timeline_finish(session_id, timeline_id, status=status, output_summary=f"Provider={provider}; model={model}", duration_ms=round((time.perf_counter() - started) * 1000))
        self.sessions.add_message(
            session_id,
            "assistant",
            answer,
            AgentMode.ASK,
            provider=provider,
            model=model,
            citations=[item["citation"] for item in citations],
            visible_reasoning_summary=f"Retrieved {len(citations)} local source chunk(s); no tools were executed.",
        )

    async def _ask_stream(self, session_id: str, prompt: str) -> AsyncIterator[dict[str, Any]]:
        citations = self._retrieve(session_id, prompt, 5)
        bundle = context_builder.build(
            session=self.sessions.get(session_id),
            context_snapshot=rebuild_context_snapshot(self.sessions, session_id).as_dict(),
            mode="ASK",
            prompt=prompt,
            citations=citations,
            system_prompt="Answer as a cautious COMSOL scientific assistant. ASK cannot execute tools. Cite only supplied Citation objects. Never invent evidence or reveal hidden chain-of-thought. For numeric properties, distinguish AI knowledge, RAG source, benchmark assumption and verified source; say Unverified when the evidence does not verify the value.",
        )
        timeline_id = self._timeline_start(session_id, category="LLM", phase="Generating", label="Streaming a source-grounded answer", tool="provider.chat.stream", input_summary=bundle.summary())
        started = time.perf_counter()
        text_parts: list[str] = []
        provider = model = "none"
        try:
            async for event in self.providers.stream_completion(bundle.messages):
                if event["type"] == "meta":
                    provider = str(event["provider_label"])
                    model = str(event["model"])
                    yield event
                elif event["type"] == "delta":
                    token = str(event["text"])
                    text_parts.append(token)
                    yield event
                elif event["type"] == "unavailable":
                    fallback = "No local AI provider is ready. Set up Ollama or LM Studio in Settings; no COMSOL action was executed."
                    text_parts.append(fallback)
                    yield {"type": "delta", "text": fallback}
        except (Exception,) as exc:
            failure = f"Provider streaming failed safely: {type(exc).__name__}. No COMSOL action was executed."
            text_parts.append(failure)
            yield {"type": "delta", "text": failure}
        answer = "".join(text_parts).strip()
        status = "success" if provider != "none" and answer else "warning"
        self._timeline_finish(session_id, timeline_id, status=status, output_summary=f"Provider={provider}; model={model}", duration_ms=round((time.perf_counter() - started) * 1000))
        self.sessions.add_message(session_id, "assistant", answer, AgentMode.ASK, provider=provider, model=model, citations=[item["citation"] for item in citations], visible_reasoning_summary=f"Retrieved {len(citations)} local source chunk(s); no tools were executed.")

    async def _plan(self, session_id: str, prompt: str) -> ScientificPlan | None:
        ledger = ModelStateLedger(self.sessions)
        ledger.append(session_id, 'request.current', prompt, Provenance.USER_REQUEST)
        intent = compile_intent(prompt)
        if intent.unknowns or intent.validation_issues:
            missing = ", ".join([*intent.unknowns, *intent.validation_issues])
            self.sessions.add_message(session_id, "assistant", f"The scientific intent is not ready for planning. Resolve before continuing: {missing}. No COMSOL action was executed.", AgentMode.PLAN, provider="deterministic-intent-compiler", model="none")
            return None
        canonical_prompt = intent.canonical_query
        await self._transition(session_id, AgentState.RESEARCHING)
        citations = self._retrieve(session_id, canonical_prompt, 6)
        await self._transition(session_id, AgentState.PLANNING)
        bundle = context_builder.build(
            session=self.sessions.get(session_id),
            context_snapshot=rebuild_context_snapshot(self.sessions, session_id).as_dict(),
            mode="PLAN",
            prompt=canonical_prompt,
            citations=citations,
            system_prompt="Create a cautious typed ScientificPlan. PLAN must not execute COMSOL.",
            external_context_tokens=(len(json.dumps(ScientificPlan.model_json_schema(), ensure_ascii=False)) + 3) // 4,
        )
        timeline_id = self._timeline_start(session_id, category="LLM", phase="Planning", label="Generating a typed ScientificPlan", tool="agent.scientific_planner", input_summary=bundle.summary())
        started = time.perf_counter()
        try:
            payload, provider, model = await self.planner.plan(canonical_prompt, citations, bundle.supplemental_context, original_prompt=prompt)
        except PlanGenerationFailure as exc:
            self._timeline_finish(
                session_id,
                timeline_id,
                status="error",
                output_summary=str(exc),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            await self._transition_to_failed(session_id, str(exc))
            self.sessions.add_message(
                session_id,
                "assistant",
                f"{exc}. The invalid provider output was not converted into a plan and no COMSOL action was executed.",
                AgentMode.PLAN,
                provider="failed",
                model="unknown",
                citations=[item["citation"] for item in citations],
            )
            return None
        try:
            plan = ScientificPlan.model_validate(payload)
        except ValidationError as exc:
            raise ScientificValidationError(f"ScientificPlan schema validation failed: {exc}") from exc
        report = self._validate_plan(plan)
        quality = plan_quality_validator.apply(plan)
        plan.precheck_summary = report.model_dump(mode="json") if report else None
        plan.runnable = bool(plan.workflow_id and report and report.passed and quality.status != "INVALID")
        self.sessions.set_plan(session_id, plan.model_dump(mode="json"))
        ledger.append(session_id, 'plan.workflow_id', plan.workflow_id, Provenance.PLAN)
        ledger.append(session_id, 'plan.parameters', plan.model_dump(mode='json')['parameters'], Provenance.PLAN)
        ledger.append(session_id, 'plan.boundary_conditions', plan.boundary_conditions, Provenance.PLAN)
        self.sessions.append_event(session_id, "plan.validated", {"plan_id": plan.plan_id, "valid": plan.runnable, "report": plan.precheck_summary})
        self._timeline_finish(session_id, timeline_id, status="success", output_summary=f"ScientificPlan {plan.plan_id[:8]} generated by {provider}/{model}; quality={plan.quality_status}; ready={plan.runnable}", duration_ms=round((time.perf_counter() - started) * 1000))
        await self._transition(session_id, AgentState.PLAN_READY)
        self.sessions.add_message(session_id, "assistant", format_plan(plan), AgentMode.PLAN, provider=provider, model=model, citations=[item["citation"] for item in citations], structured=plan.model_dump(mode="json"))
        return plan

    async def _start_run(self, session_id: str) -> None:
        current = self.sessions.get(session_id)
        payload = current.get("plan")
        if not payload:
            self.sessions.add_message(session_id, "assistant", "RUN requires a saved ScientificPlan. Create and review PLAN first; the original prompt was not reinterpreted.", AgentMode.RUN)
            return
        try:
            plan = ScientificPlan.model_validate(payload)
        except ValidationError as exc:
            self.sessions.add_message(session_id, "assistant", f"The saved plan does not satisfy ScientificPlan 2.0 and was not executed: {exc}", AgentMode.RUN)
            return
        supported = {
            "heat_transfer_2d_stationary", "tds_2d_stationary_diffusion",
            "solid_mechanics_uniaxial_bar", "electrochem_primary_current",
            "lithium_ion_battery_baseline", "heat_tds_temperature_coupling",
        }
        if not plan.runnable or plan.workflow_id not in supported:
            self.sessions.add_message(session_id, "assistant", "The saved ScientificPlan is not ready for a verified real-COMSOL executor. Nothing was changed in COMSOL.", AgentMode.RUN, plan_id=plan.plan_id)
            return
        active = self._tasks.get(session_id)
        if active and not active.done():
            self.sessions.add_message(session_id, "assistant", "A RUN is already active for this session.", AgentMode.RUN, plan_id=plan.plan_id)
            return
        process_ids = {"scientific_runtime": os.getpid(), **self.tools.process_ids(session_id)}
        bridge_pid = java_bridge_process_manager.status().get("pid")
        if isinstance(bridge_pid, int):
            process_ids["java_bridge"] = bridge_pid
        profile = await asyncio.to_thread(hardware_profile, WORKSPACE_ROOT, process_ids)
        resource_assessment = assess_risk(plan, profile, stage="prebuild")
        self.sessions.append_event(session_id, "resource.precheck", resource_assessment)
        if not resource_assessment["allowed"]:
            level = resource_assessment["risk_level"]
            reasons = "; ".join(resource_assessment["reasons"])
            self.sessions.add_message(
                session_id,
                "assistant",
                f"RUN is blocked by the {level} resource precheck: {reasons}. Review the deterministic suggestions and explicitly enable the matching resource-policy override to continue. COMSOL was not started.",
                AgentMode.RUN,
                plan_id=plan.plan_id,
                resource_assessment=resource_assessment,
            )
            return
        pending_run_id = str(uuid.uuid4())
        self.sessions.begin_run(session_id, pending_run_id, plan.model_dump(mode='json'))
        await self._transition(session_id, AgentState.PRECHECK)
        self._pending_runs[session_id] = pending_run_id
        task = asyncio.create_task(self._execute_run(session_id, plan), name=f"comsol-run-{session_id}")
        self._tasks[session_id] = task

    async def _execute_run(self, session_id: str, plan: ScientificPlan) -> None:
        run_id = self._pending_runs.pop(session_id, None)
        if run_id is None:
            run_id = str(uuid.uuid4())
            self.sessions.begin_run(session_id, run_id, plan.model_dump(mode='json'))
        attempt_id = None
        verdict: ExecutionVerdict | None = None
        execution: dict[str, Any] | None = None
        solution_report: ValidationReport | None = None
        run_started = time.perf_counter()
        repair_history: list[dict[str, Any]] = []
        outer_timeline = self._timeline_start(session_id, category="SYSTEM", phase="Execution", label="Executing saved ScientificPlan", tool="runtime.execution_protocol", input_summary={"run_id": run_id, "plan_id": plan.plan_id, "workflow_id": plan.workflow_id})
        resource_samples: list[dict[str, Any]] = []

        async def record_resource_sample(sample: dict[str, Any]) -> None:
            resource_samples.append(sample)
            self.sessions.append_resource_sample(session_id, run_id, sample)
            thresholds = sample.get("thresholds", {})
            if thresholds.get("events"):
                self.sessions.append_event(session_id, "resource.warning", sample)

        monitor = ResourceMonitor(
            WORKSPACE_ROOT,
            plan,
            record_resource_sample,
            process_ids=lambda: {"scientific_runtime": os.getpid(), **self.tools.process_ids(session_id)},
        )
        try:
            self.sessions.assert_current(run_id)
            validator = validator_registry.get(plan.workflow_id)
            if validator is None:
                raise ScientificValidationError(f"No validator registered for {plan.workflow_id}")
            precheck_id = self._timeline_start(session_id, category="VALIDATION", phase="Precheck", label="Validating plan, parameters and boundary definitions", tool=f"validators.{plan.workflow_id}.precheck", input_summary={"plan_id": plan.plan_id})
            precheck_started = time.perf_counter()
            precheck = validator.precheck(plan)
            self.sessions.append_event(session_id, "validation.precheck", precheck.model_dump(mode="json"))
            precheck_ms = round((time.perf_counter() - precheck_started) * 1000, 3)
            self._timeline_finish(session_id, precheck_id, status="success" if precheck.passed else "error", output_summary=summarize_report(precheck), duration_ms=precheck_ms)
            if not precheck.passed:
                self.sessions.finish_run(run_id, None, 'PrecheckRejected')
                await self._transition(session_id, AgentState.VALIDATION_FAILED)
                self.sessions.add_message(session_id, "assistant", "RUN stopped at PRECHECK. Scientific validation failed before COMSOL was started.", AgentMode.RUN, plan_id=plan.plan_id, validation=precheck.model_dump(mode="json"))
                return

            if java_bridge_process_manager.status().get("running"):
                # Avoid two concurrent COMSOL JVMs while the isolated worker owns
                # the build/solve; the solved MPH is reloaded into the Bridge below.
                await java_bridge_process_manager.stop()
                self.sessions.append_event(session_id, "comsol.command", {"event_type": "command", "phase": "bridge", "status": "success", "command_success": True, "model_state_verified": False, "scientific_validation_passed": False, "detail": "Stopped the Workbench-owned Bridge before isolated COMSOL solve", "created_at": utc_now()})

            max_repairs = max(0, min(int(settings_store.load().get("execution", {}).get("max_repairs", 1)), 3))
            execution: dict[str, Any] | None = None
            solution_report: ValidationReport | None = None
            for attempt in range(max_repairs + 1):
                self.sessions.assert_current(run_id)
                attempt_id = self.sessions.begin_attempt(run_id)
                self._record_plan_assumptions(session_id, run_id, attempt_id, plan)
                if plan.workflow_id in {"heat_transfer_2d_stationary", "tds_2d_stationary_diffusion"}:
                    graph = ExecutionGraph.small_supported(run_id, attempt_id, str(plan.workflow_id))
                    self.live_interrupt.register(session_id, graph, replace=True)
                    self.live_interrupt.start(run_id, "build")
                if attempt == 0:
                    await self._transition(session_id, AgentState.BUILDING)
                else:
                    current_state = AgentState(self.sessions.get(session_id)["state"])
                    if current_state != AgentState.REPAIRING:
                        await self._transition(session_id, AgentState.REPAIRING)
                    await self._transition(session_id, AgentState.REVALIDATING)
                    await self._transition(session_id, AgentState.BUILDING)
                try:
                    monitor.start()
                    tool_name = {
                        "heat_transfer_2d_stationary": "comsol.heat_transfer_2d",
                        "tds_2d_stationary_diffusion": "comsol.tds_2d_stationary_diffusion",
                        "solid_mechanics_uniaxial_bar": "comsol.solid_mechanics_uniaxial_bar",
                        "electrochem_primary_current": "comsol.electrochem_primary_current",
                        "lithium_ion_battery_baseline": "comsol.lithium_ion_battery_baseline",
                        "heat_tds_temperature_coupling": "comsol.heat_tds_temperature_coupling",
                    }[str(plan.workflow_id)]
                    execution = await self.tools.execute(
                        tool_name,
                        {"run_id": run_id, "attempt_id": attempt_id, "session_id": session_id, "workspace": str(WORKSPACE_ROOT), "plan": plan.execution_payload(), "attempt": attempt + 1},
                        lambda event, aid=attempt_id: self._handle_attempt_event(session_id, run_id, aid, event),
                        retries=0,
                    )
                except Exception as exc:
                    self.sessions.assert_current(run_id, attempt_id)
                    if isinstance(exc, StaleAttempt): raise
                    repair_history.append({"run_id": run_id, "attempt_id": attempt_id, "attempt": attempt + 1, "reason": str(exc), "outcome": "retry" if attempt < max_repairs else "failed", "time": utc_now()})
                    if attempt < max_repairs:
                        self.sessions.end_attempt(run_id, attempt_id, 'RetryableFailure')
                        current_state = AgentState(self.sessions.get(session_id)["state"])
                        if current_state != AgentState.REPAIRING:
                            await self._transition(session_id, AgentState.REPAIRING)
                        continue
                    raise
                finally:
                    await monitor.stop()
                self.sessions.assert_current(run_id, attempt_id)
                if execution.get('attempt_id', attempt_id) != attempt_id:
                    raise StaleAttempt('worker result belongs to a stale attempt')
                execution['attempt_id'] = attempt_id
                self.sessions.update_run(run_id, attempt_id, artifacts=execution.get('artifacts', {}))
                solution_report = validator.validate_execution(plan, execution)
                derived_evidence = await asyncio.to_thread(
                    evaluate_evidence_contract, execution, run_id, attempt_id,
                )
                verdict = execution_verdict(
                    str(plan.workflow_id), run_id, execution, solution_report,
                    expected_attempt_id=attempt_id, derived_evidence=derived_evidence,
                )
                self.sessions.append_event(session_id, "validation.solution", verdict.validation_report(solution_report))
                if verdict.completed:
                    break
                await self._transition(session_id, AgentState.VALIDATION_FAILED)
                repair_history.append({"run_id": run_id, "attempt_id": attempt_id, "attempt": attempt + 1, "reason": "; ".join(verdict.issues), "outcome": "retry" if attempt < max_repairs else "failed", "time": utc_now()})
                if (verdict.command_success and verdict.model_state_verified
                        and verdict.scientific_validation_success
                        and not verdict.evidence_contract_passed):
                    repair_history[-1]["outcome"] = "failed"
                    decision = self.recovery.decide(ControllerInput(
                        plan_node_id="validate", missing_required_evidence=True,
                        evidence_acquisition_available=False,
                    ))
                    self.sessions.append_event(session_id, "recovery.decision", {
                        "run_id": run_id, "attempt_id": attempt_id,
                        "action": decision.action.value, "priority": decision.priority,
                        "reason": decision.reason,
                    })
                    raise ScientificValidationError(
                        "Execution evidence is incomplete; automatic rerun is forbidden because a second solve cannot repair missing provenance"
                    )
                if attempt >= max_repairs:
                    raise ScientificValidationError("; ".join(verdict.issues))
                self.sessions.end_attempt(run_id, attempt_id, 'RetryableFailure')

            if execution is None or solution_report is None or verdict is None or not verdict.completed:
                raise ScientificValidationError("Execution did not produce a validated result")
            certification = self.recovery.decide(ControllerInput(
                plan_node_id="validate", all_certification_gates_passed=True,
            ))
            self.sessions.append_event(session_id, "recovery.decision", {
                "run_id": run_id, "attempt_id": attempt_id,
                "action": certification.action.value, "priority": certification.priority,
                "reason": certification.reason,
            })
            verdict_report = verdict.validation_report(solution_report)
            execution["repair_history"] = repair_history
            final_profile = await asyncio.to_thread(hardware_profile, WORKSPACE_ROOT, {"scientific_runtime": os.getpid(), **self.tools.process_ids(session_id)})
            validation_metrics = execution.get("validation", {})
            stage_b = assess_risk(
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
                        field = await java_bridge_client.heat_field()
                        resumed = next((item for item in reversed(self.sessions.get(session_id)['events'])
                                        if item.get('type') == 'execution.resumed'
                                        and (item.get('payload') or {}).get('run_id') == run_id
                                        and (item.get('payload') or {}).get('attempt_id') == attempt_id), None)
                        study_date = int(((resumed or {}).get('payload') or {}).get('study', {}).get('last_computation_date') or 0)
                        self.sessions.assert_current(run_id, attempt_id)
                        exported = await java_bridge_client.export_corrected_heat(run_id, attempt_id)
                        attempt_workspace = WORKSPACE_ROOT / 'models' / 'attempts' / run_id / attempt_id
                        corrected_plan, corrected_execution = corrected_heat_execution(
                            plan=plan, original=execution, correction=graph.corrections[0],
                            left_readback=left, right_readback=right, field=field,
                            exported=exported, study_date=study_date,
                            run_id=run_id, attempt_id=attempt_id,
                            attempt_workspace=attempt_workspace, model_tree=tree,
                        )
                        self.sessions.assert_current(run_id, attempt_id)
                        self.sessions.set_plan(session_id, corrected_plan.model_dump(mode='json'))
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
            lifecycle = self.sessions.finish_run(run_id, attempt_id, 'Completed')
            self.sessions.append_event(session_id, "run.completed", {"run_id": run_id, 'attempt_id': attempt_id, 'run_lifecycle': lifecycle, "plan_id": plan.plan_id, "artifacts": execution["artifacts"], "evidence_contract": execution.get("evidence_contract", {}), **verdict.fields(), "validation": execution["validation"], "build_readback": execution.get("build_readback"), "boundary_map": execution.get("boundary_map"), "validation_report": verdict_report, "repair_history": repair_history, "tool": execution["tool"], "duration_ms": execution["duration_ms"], "performance": execution["performance"], "java_bridge": execution.get("java_bridge"), "resource": execution.get("resource")})
            await self._transition(session_id, AgentState.COMPLETED)
            self._timeline_finish(session_id, outer_timeline, status="success", output_summary=verdict.summary(), duration_ms=execution["duration_ms"], fallback=bool(repair_history), raw_output=execution.get("raw_log", ""), verdict=verdict)
            primary_field = execution["artifacts"].get("concentration_image") or execution["artifacts"].get("temperature_image") or execution["artifacts"].get("stress_image") or execution["artifacts"].get("potential_image")
            self.sessions.add_message(session_id, "assistant", f"RUN completed from ScientificPlan `{plan.plan_id}`. {verdict.summary()}\n\n- Model: `{execution['artifacts']['model']}`\n- Primary field: `{primary_field}`\n- Model Card 2.0: `{card_path}`", AgentMode.RUN, plan_id=plan.plan_id, run_id=run_id, attempt_id=attempt_id, run_lifecycle=lifecycle, artifacts=execution["artifacts"], evidence_contract=execution.get("evidence_contract", {}), **verdict.fields(), validation=execution["validation"], validation_report=verdict_report, repair_history=repair_history)
        except (Exception, asyncio.CancelledError) as exc:
            self._pending_boundary_reviews.pop(run_id, None)
            await monitor.stop()
            run = self.sessions.get_run(run_id)
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

    async def _handle_attempt_event(self, session_id, run_id, attempt_id, event):
        run = self.sessions.get_run(run_id)
        if run['cancel_requested']:
            # Safe cancellation is unavailable: drain the still-owned worker without
            # publishing progress/success or killing it merely for emitting an event.
            return
        self.sessions.assert_current(run_id, attempt_id)
        if event.get('run_id', run_id) != run_id or event.get('attempt_id', attempt_id) != attempt_id:
            raise StaleAttempt('worker event belongs to stale attempt')
        phase = {'build': 'BUILDING', 'solve': 'SOLVING', 'export': 'EXPORTING',
                 'build validation': 'BUILD_VALIDATION', 'solution validation': 'SOLUTION_VALIDATION'}.get(str(event.get('phase', '')).lower())
        if phase: self.sessions.update_run(run_id, attempt_id, phase=phase)
        await self._handle_worker_event(session_id, {**event, 'run_id': run_id, 'attempt_id': attempt_id})

    async def _handle_worker_event(self, session_id: str, event: dict[str, Any]) -> None:
        phase = str(event.get("phase", "Build"))
        normalized = phase.lower().replace("_", " ")
        target = {
            "build": AgentState.BUILDING,
            "build validation": AgentState.BUILD_VALIDATION,
            "solve": AgentState.SOLVING,
            "validate": AgentState.SOLUTION_VALIDATION,
            "solution validation": AgentState.SOLUTION_VALIDATION,
            "repair": AgentState.REPAIRING,
            "revalidate": AgentState.REVALIDATING,
            "export": AgentState.EXPORTING,
        }.get(normalized)
        if target is not None:
            current = AgentState(self.sessions.get(session_id)["state"])
            if current != target:
                try:
                    await self._transition(session_id, target)
                except InvalidStateTransition as exc:
                    self.sessions.append_event(session_id, "runtime.warning", {"message": "Worker phase arrived after state advancement", "phase": phase, "current_state": current, "target_state": target, "detail": str(exc)})
        status = str(event.get("status", "info"))
        if status == "running":
            status = "info"
        category = "VALIDATION" if "validation" in normalized or normalized == "validate" else "COMSOL" if normalized in {"build", "solve", "export"} else "SYSTEM"
        event_type = str(event.get("event_type") or ("progress" if normalized in {"solve", "export"} else "model_change" if "validation" in normalized else "command"))
        self.sessions.append_event(session_id, f"comsol.{event_type}", {"event_type": event_type, "phase": phase, "status": status, "evidence_status": "provisional", "worker_claims": {key: event.get(key) for key in ("command_success", "model_state_verified", "scientific_validation_passed")}, "detail": event.get("output_summary") or event.get("label"), "created_at": utc_now()})
        self.sessions.append_event(session_id, "timeline.worker", {"id": str(uuid.uuid4()), "category": category, "phase": phase, "label": str(event.get("label", phase)), "status": status, "tool": "comsol.worker", "event_type": event_type, "input_summary": event.get("input_summary"), "output_summary": event.get("output_summary"), "duration_ms": event.get("duration_ms"), "fallback": bool(event.get("fallback", False)), "raw_output": event.get("raw_output"), "created_at": utc_now()})

    async def _verify_java_bridge(self, session_id: str, execution: dict[str, Any]) -> None:
        """Load the solved MPH in the owned Bridge and verify the supported tree."""

        model_path = str(execution.get("artifacts", {}).get("model", ""))
        bridge_started = time.perf_counter()
        run = self.sessions.latest_run(session_id)
        workflow_id = str((self.sessions.get(session_id).get('plan') or {}).get('workflow_id') or '')
        heat = workflow_id == 'heat_transfer_2d_stationary'
        timeline_id = self._timeline_start(
            session_id,
            category="COMSOL",
            phase="Build Validation",
            label="Loading solved Heat model in the real Java Bridge" if heat else "Loading solved TDS model in the real Java Bridge",
            tool="java_bridge.real_comsol",
            input_summary={"model_path": model_path},
        )
        await java_bridge_process_manager.start(model_path, owner_id=run['run_id'] if run else None)
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            run = self.sessions.latest_run(session_id)
            if run: self.sessions.assert_current(run['run_id'], run['attempt_id'])
            status = java_bridge_process_manager.status()
            if status.get("state") == "ready":
                break
            if status.get("state") in {"failed", "crashed"}:
                raise ScientificValidationError(f"Java Bridge failed to load the solved model: {status.get('error')}")
            await asyncio.sleep(0.25)
        else:
            raise ScientificValidationError("Java Bridge timed out while loading the solved model")
        health, tree, parameters = await asyncio.gather(
            java_bridge_client.health(),
            java_bridge_client.model_tree(),
            java_bridge_client.parameters(),
        )
        parameter_values = parameters.get("parameters", {})
        checks = {
            "real_comsol": health.get("real_comsol") is True,
            "model_connected": health.get("model_connected") is True,
            "component": "comp1" in tree.get("components", []),
            "geometry": "comp1:geom1" in tree.get("geometry", []),
            "physics": ("comp1:ht" if heat else "comp1:tds") in tree.get("physics", []),
            "mesh": "comp1:mesh1" in tree.get("mesh", []),
            "study": "std1" in tree.get("studies", []),
            "parameters": True if heat else all(name in parameter_values for name in ("L", "H", "D", "c_left", "c_right")),
        }
        if heat:
            from app.execution.boundary_feature import HEAT_FEATURES, read_kelvin
            edge_map = execution.get('boundary_map') or {}
            boundary_readback = {}
            for side, feature in HEAT_FEATURES.items():
                current = await java_bridge_client.boundary_feature(
                    component_tag='comp1', physics_tag='ht', feature_tag=feature, property_key='T0')
                expected_edge = edge_map.get(side)
                expected_plan = (self.sessions.get(session_id).get('plan') or {}).get('parameters', {}).get(f'{side}_temperature', {})
                expected_temperature = expected_plan.get('normalized_value', expected_plan.get('value'))
                try:
                    observed_temperature = read_kelvin(str(current.get('expression', '')))
                    temperature_matches = abs(observed_temperature - float(expected_temperature)) <= 1e-9
                except (ValueError, TypeError):
                    temperature_matches = False
                checks[f'{side}_boundary'] = (
                    current.get('model_state_verified') is True
                    and current.get('feature_type') == 'TemperatureBoundary'
                    and current.get('selection') == [expected_edge]
                    and current.get('unit') == 'K' and temperature_matches
                )
                boundary_readback[side] = current
            execution['boundary_feature_readback'] = boundary_readback
            try:
                core = await java_bridge_client.heat_core()
                execution['build_readback'] = normalize_heat_core(
                    core, boundary_readback['left'], boundary_readback['right'])
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                execution['build_readback_issue'] = f'{type(exc).__name__}: {exc}'
        execution["java_bridge"] = {"health": health, "tree": tree, "parameters": parameter_values, "checks": checks}
        if not all(checks.values()):
            self._timeline_finish(session_id, timeline_id, status="error", output_summary=f"Java Bridge saved-model readback failed: {checks}", duration_ms=0)
            raise ScientificValidationError(f"Java Bridge saved-model readback failed: {checks}")
        if heat and isinstance(execution.get('build_readback'), dict) \
                and execution['build_readback'].get('source') == 'direct_comsol_readback' and run:
            self.sessions.append_event(session_id, 'model.core_readback', {
                'run_id': run['run_id'], 'attempt_id': run['attempt_id'],
                'verified_real_comsol': True, 'readback': execution['build_readback'],
            })
        self.sessions.append_event(
            session_id,
            "comsol.model_change",
            {
                "event_type": "model_change",
                "phase": "Build Validation",
                "status": "success",
                "command_success": True,
                "model_state_verified": True,
                "scientific_validation_passed": False,
                "detail": "Java Bridge loaded the solved MPH and verified model tree plus workflow-specific readback",
                "created_at": utc_now(),
            },
        )
        bridge_ms = round((time.perf_counter() - bridge_started) * 1000, 3)
        self._timeline_finish(session_id, timeline_id, status="success", output_summary=f"Real Java Bridge readback passed: {checks}", duration_ms=bridge_ms)
        execution.setdefault("performance", {})["java_bridge_readback_ms"] = bridge_ms

    def _validate_plan(self, plan: ScientificPlan) -> ValidationReport | None:
        validator = validator_registry.get(plan.workflow_id)
        return validator.precheck(plan) if validator else None

    def _record_plan_assumptions(self, session_id: str, run_id: str, attempt_id: str,
                                 plan: ScientificPlan) -> None:
        ledger = AssumptionLedger(self.sessions)
        affected = ["build", "solve", "validate", "export"]
        for name, parameter in plan.parameters.items():
            if not parameter.assumption:
                continue
            ledger.record(
                session_id=session_id, run_id=run_id, attempt_id=attempt_id,
                plan_node_id="build",
                choice={"parameter": name, "value": parameter.value, "unit": parameter.unit},
                reason=parameter.source or "Explicit ScientificPlan assumption",
                source="SCIENTIFIC_PLAN", confidence=float(parameter.confidence or 0.5),
                user_confirmed=False, affected_nodes=affected,
                safe_default=True, domain_sensitive=False,
            )
        for description in plan.assumptions:
            ledger.record(
                session_id=session_id, run_id=run_id, attempt_id=attempt_id,
                plan_node_id="build", choice={"assumption": description},
                reason="Explicit assumption carried by the frozen ScientificPlan",
                source="SCIENTIFIC_PLAN", confidence=0.5, user_confirmed=False,
                affected_nodes=affected, safe_default=True, domain_sensitive=False,
            )

    def _admit_completed_run_memory(self, session_id: str, run_id: str, attempt_id: str,
                                    plan: ScientificPlan, verdict: ExecutionVerdict,
                                    execution: dict[str, Any]) -> None:
        record = MemoryStore(self.sessions).admit(
            MemoryCandidate(
                memory_type=MemoryType.EPISODIC_RUN,
                content={
                    "workflow_id": str(plan.workflow_id),
                    "verdict": verdict.as_dict(),
                    "required_checks": list(execution.get("validation", {}).keys()),
                },
                provenance={
                    "session_id": session_id, "run_id": run_id,
                    "attempt_id": attempt_id, "authority": "DETERMINISTIC_EXECUTION_VERDICT",
                },
                version_scope="1.1",
                applicability={"workflow_id": str(plan.workflow_id)},
                source_outcome="Completed",
                evidence_contract_passed=verdict.evidence_contract_passed,
                scientific_validation_passed=verdict.scientific_validation_success,
                false_pass=False,
            ),
            MemoryAdmissionPolicy(),
        )
        self.sessions.append_event(session_id, "memory.admission", {
            "run_id": run_id, "attempt_id": attempt_id,
            "memory_id": record["memory_id"], "type": record["type"].value,
            "validation_status": record["validation_status"].value,
            "validation_reason": record["validation_reason"],
        })

    def _admit_failure_memory(self, *, session_id: str, run_id: str, attempt_id: str,
                              plan: ScientificPlan, outcome: str, failure_class: str,
                              failed_phase: str, attempted_action: str) -> None:
        record = MemoryStore(self.sessions).admit_failure(
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt_id,
            workflow_id=str(plan.workflow_id),
            failure_class=failure_class,
            failed_phase=failed_phase,
            attempted_action=attempted_action,
            outcome=outcome,
            version_scope="1.1",
        )
        self.sessions.append_event(session_id, "memory.admission", {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "memory_id": record["memory_id"],
            "type": record["type"].value,
            "validation_status": record["validation_status"].value,
            "validation_reason": record["validation_reason"],
            "may_certify": False,
        })

    async def _transition_to_failed(self, session_id: str, error: str) -> None:
        current = AgentState(self.sessions.get(session_id)["state"])
        if current != AgentState.FAILED:
            assert_transition(current, AgentState.FAILED)
            self.sessions.set_state(session_id, AgentState.FAILED, error=error)

    async def _transition(self, session_id: str, target: AgentState) -> None:
        current = AgentState(self.sessions.get(session_id)["state"])
        if current == target:
            return
        assert_transition(current, target)
        self.sessions.set_state(session_id, target)

    def _timeline_start(self, session_id: str, *, category: str, phase: str, label: str, tool: str, input_summary: dict[str, Any]) -> str:
        item_id = str(uuid.uuid4())
        self.sessions.append_event(session_id, "timeline.started", {"id": item_id, "category": category, "phase": phase, "label": label, "status": "running", "tool": tool, "input_summary": input_summary, "output_summary": None, "duration_ms": None, "fallback": False, "raw_output": None, "created_at": utc_now()})
        return item_id

    def _timeline_finish(self, session_id: str, item_id: str, *, status: str, output_summary: str, duration_ms: int, fallback: bool = False, raw_output: str | None = None, verdict: ExecutionVerdict | None = None) -> None:
        run = self.sessions.get_run(verdict.run_id) if verdict else None
        lifecycle = {key: run[key] for key in ('run_id', 'attempt_id', 'outcome', 'phase', 'cancel_requested')} if run else None
        self.sessions.append_event(session_id, "timeline.completed", {"id": item_id, "status": status, "output_summary": output_summary, "duration_ms": duration_ms, "fallback": fallback, "raw_output": raw_output[-12_000:] if raw_output else None, "completed_at": utc_now(), **({'run_lifecycle': lifecycle} if lifecycle else {}), **(verdict.fields() if verdict else {})})


def summarize_report(report: ValidationReport) -> str:
    failed = [check.message for check in report.checks if check.status == "failed"]
    warnings = [check.message for check in report.checks if check.status == "warning"]
    if failed:
        return f"{len(failed)} failed: " + "; ".join(failed[:4])
    return f"{len(report.checks)} checks passed; {len(warnings)} warning(s)"


def format_plan(plan: ScientificPlan) -> str:
    status = "Ready to run" if plan.runnable else "Needs revision"
    return f"ScientificPlan 2.0 created.\n\nPlan ID: `{plan.plan_id}`\nStatus: **{status}**\nQuality: **{plan.quality_status}**\nPhysics: {', '.join(plan.physics) or 'not specified'}\nParameters: {len(plan.parameters)}\nSources: {len(plan.sources)}\nMissing information: {len(plan.missing_information)}\n\nReview the structured Plan Card and parameter provenance before RUN."


agent_runtime = AgentRuntime()
