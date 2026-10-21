"""One-shot Batch 001 runner and freezer.

It never invokes PTC, never injects Skill text, and never manufactures a model
response. A missing declared model yields an explicit UNAVAILABLE observation.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time
import uuid
import zipfile
from typing import Any

from app.agent.planner import (
    extract_battery_plan, extract_electrochem_plan, extract_heat_transfer_plan,
    extract_multiphysics_plan, extract_solid_plan, extract_tds_plan, generic_plan,
)
from app.session.run_journal import RunCanceled, StaleAttempt
from app.session.store import SessionStore

from .artifacts import ImmutableArtifactStore, canonical_json_bytes, sha256_file
from .cases import FrozenCase, batch_001_cases
from .judge import ShadowScientificJudge
from .model_adapter import ModelAdapter, ModelRequest
from .normalizer import PhysicsObservationNormalizer
from .skills import SkillRegistry, SkillRouter, SkillTelemetry
from .trajectory import TrajectoryRecorder, is_silent_physical_failure


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _offline_plan(prompt: str) -> dict[str, Any]:
    return (extract_multiphysics_plan(prompt) or extract_battery_plan(prompt) or extract_tds_plan(prompt)
            or extract_heat_transfer_plan(prompt) or extract_solid_plan(prompt)
            or extract_electrochem_plan(prompt) or generic_plan(prompt))


class BatchFreezer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = ImmutableArtifactStore(root)

    def initialize_reward_ledger(self) -> dict[str, Any]:
        record = {
            "schema_version": "0.3", "batch_id": "batch_001", "status": "AWAITING_EXTERNAL_DSH",
            "producer_final_reward_write": False, "entries": [],
        }
        self.store.write_json("reward_ledger_initial.json", record)
        return record

    def append_reward(self, entry: dict[str, Any], *, actor: str) -> None:
        if actor != "external_dsh":
            raise PermissionError("producer has no final reward write authority")
        raise RuntimeError("frozen Batch 001 ledger accepts DSH output only as a separate review artifact")

    @staticmethod
    def directory_manifest(root: Path, *, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = set(exclude or ())
        files: dict[str, dict[str, Any]] = {}
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            files[relative] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        return {"algorithm": "SHA-256", "files": files}


class BatchRunner:
    batch_id = "batch_001"

    def __init__(self, output: Path, *, model_adapter: ModelAdapter, research_probe_mode: bool = False) -> None:
        self.output = output
        self.model_adapter = model_adapter
        self.research_probe_mode = research_probe_mode
        self.registry = SkillRegistry.with_reference_candidates()
        self.router = SkillRouter()
        self.normalizer = PhysicsObservationNormalizer()
        self.judge = ShadowScientificJudge()

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=False)
        with tempfile.TemporaryDirectory(
            prefix=f".{self.output.name}.working-",
            dir=self.output.parent,
            ignore_cleanup_errors=True,
        ) as working:
            self._working = Path(working)
            return self._run_once()

    def _run_once(self) -> dict[str, Any]:
        store = ImmutableArtifactStore(self.output)
        freezer = BatchFreezer(self.output)
        freezer.initialize_reward_ledger()
        (self.output / "raw").mkdir(exist_ok=True)
        (self.output / "reviewer_packet" / "cases").mkdir(parents=True, exist_ok=True)
        recorder = TrajectoryRecorder(self.output / "trajectories.jsonl", store)
        telemetry = SkillTelemetry(self.output / "skill_shadow_telemetry.jsonl")
        started_at = utc_now()
        results: list[dict[str, Any]] = []
        cases = batch_001_cases()
        if len(cases) != 20:
            raise RuntimeError("Batch 001 must contain exactly 20 review units")

        policy = self._policy_snapshot()
        store.write_json("FROZEN_POLICY.json", policy)
        store.write_json("skill_registry_snapshot.json", {
            "mode": "candidate_reference_only", "batch001_load_allowed": False,
            "skills": [item.as_dict() for item in self.registry.all()],
        })
        for case in cases:
            result = self._run_case(case, store, recorder, telemetry)
            results.append(result)

        summary = self._summary(results, started_at)
        store.write_json("BATCH_001_SUMMARY_MACHINE.json", summary)
        store.write_bytes("BATCH_001_RAW_INDEX.md", self._raw_index(results).encode("utf-8"))
        self._assemble_reviewer_packet(store, summary, policy)
        packet_manifest = freezer.directory_manifest(self.output / "reviewer_packet", exclude={"MANIFEST.json"})
        store.write_json("reviewer_packet/MANIFEST.json", packet_manifest)
        self._zip_reviewer_packet(self.output / "reviewer_packet.zip")
        manifest = freezer.directory_manifest(self.output, exclude={"BATCH_001_MANIFEST.json", "FROZEN"})
        manifest.update({"batch_id": self.batch_id, "review_units": 20, "frozen": True})
        manifest_ref = store.write_json("BATCH_001_MANIFEST.json", manifest)
        store.write_json("FROZEN", {"marker": "BATCH_001_FROZEN_FOR_DSH_REVIEW", "manifest_sha256": manifest_ref.sha256})
        return summary

    def _run_case(self, case: FrozenCase, store: ImmutableArtifactStore, recorder: TrajectoryRecorder,
                  telemetry_recorder: SkillTelemetry) -> dict[str, Any]:
        started = time.perf_counter()
        run_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        identity = self.model_adapter.resolve(ModelRequest(case.requested_model, case.requested_provider, case.requested_size))
        route = self.router.shadow_route({
            "case_id": case.case_id, "physics": list(case.physics), "step_type": case.step_type,
            "error_tags": [], "available_tools": ["model_inspect", "result_evaluate", "solver_status"],
            "risk": "high" if case.selection_sensitive else "medium",
            "wrong_skill_suspected": False,
        }, self.registry)
        plan = _offline_plan(case.exact_prompt)
        action, lifecycle, checks = self._producer_observation(case, identity.actual, run_id, attempt_id)
        raw = {
            "schema_version": "batch001-raw-v0.3", "case_id": case.case_id, "run_id": run_id,
            "attempt_id": attempt_id, "timestamp": utc_now(), "support_status": case.support_status,
            "exact_prompt": case.exact_prompt, "execution_policy": case.execution_policy,
            "model_identity": identity.as_dict(), "action": action, "lifecycle": lifecycle,
            "checks": checks, "offline_plan_snapshot": plan,
            "authoritative_verdict": None,
            "solver": {"state": "not_run", "reason": action.get("reason")},
            "warnings": ["No scientific success is inferred from planning or file existence"],
            "ptc_used": False, "skill_context_injected": False,
        }
        raw_ref = store.write_json(f"raw/{case.case_id}.json", raw)
        normalized = self.normalizer.normalize(raw)
        spec = self._scientific_spec(case, plan, run_id, attempt_id)
        judge_report, certificate = self.judge.evaluate(spec, raw_observation=raw, authoritative_verdict=None)
        case_dir = f"reviewer_packet/cases/{case.case_id}"
        refs = {
            "case_spec": store.write_json(f"{case_dir}/case_spec.json", case.as_dict()),
            "raw_ref": store.write_json(f"{case_dir}/raw_ref.json", raw_ref.as_dict()),
            "normalized": store.write_json(f"{case_dir}/normalized_observation.json", normalized),
            "spec": store.write_json(f"{case_dir}/scientific_spec.json", spec),
            "judge_pre": store.write_json(f"{case_dir}/judge_pre_report.json", {**judge_report, "post_audit": []}),
            "judge_post": store.write_json(f"{case_dir}/judge_post_report.json", judge_report),
            "certificate": store.write_json(f"{case_dir}/judge_certificate.json", certificate),
            "model_identity": store.write_json(f"{case_dir}/model_identity.json", identity.as_dict()),
        }
        telemetry = {
            "event": "skill_shadow_route", "case_id": case.case_id, "run_id": run_id,
            "step_id": "step-001", "timestamp": utc_now(), "skill_condition": "S0_no_skill",
            "skill_context_injected": False, "candidates": route.candidates, "selected": route.selected,
            "router_confidence": route.router_confidence, "reason_tags": route.reason_tags,
            "skill_tokens": 0, "outcome": None, "wrong_skill_suspected": route.wrong_skill_suspected,
            "router_version": self.router.version,
        }
        telemetry_recorder.record(telemetry)

        trajectory = {
            "case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id, "step_id": "step-001",
            "timestamp": utc_now(), "step_type": case.step_type, "physics": list(case.physics),
            "workflow": case.workflow_id, "execution_policy": "direct_closed_loop",
            **identity.as_dict(), "context_version": "batch001-no-skill-v0.3",
            "normalizer_version": self.normalizer.version, "tool_schema_version": "workbench-v1.1-frozen",
            "action": action,
            "observation": {"raw_ref": raw_ref.ref, "raw_sha256": raw_ref.sha256, "normalized": normalized,
                            "display_summary": normalized["display_summary"], "summary_role": "display_only"},
            "verdict": {"execution": None, "numerical": None, "physical": None,
                        "silent_physical_failure": is_silent_physical_failure(None, None, None), "evidence_refs": []},
            "metrics": {"latency_ms": round((time.perf_counter() - started) * 1000, 3), "input_tokens": None,
                        "output_tokens": None, "peak_ram_mb": None, "peak_vram_mb": None, "api_cost_usd": None},
            "artifacts": [value.as_dict() for value in refs.values()], "skill_candidate_refs": [],
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "skill_candidates": route.candidates, "skill_selected": [], "shadow_skill_selected": route.selected,
            "skill_router_confidence": route.router_confidence, "skill_router_reason_tags": route.reason_tags,
            "skill_tokens": 0, "skill_hashes": [], "wrong_skill_suspected": route.wrong_skill_suspected,
            "scientific_spec_ref": refs["spec"].ref, "scientific_spec_sha256": refs["spec"].sha256,
            "judge_mode": "shadow", "judge_pre_report_ref": refs["judge_pre"].ref,
            "judge_post_report_ref": refs["judge_post"].ref, "judge_certificate_ref": refs["certificate"].ref,
            "judge_boundary_risk": judge_report["boundary_risk"], "judge_disagreement": None,
            "judge_disagreement_reason": None,
        }
        recorder.append(trajectory)
        trajectory_ref = store.write_json(f"{case_dir}/trajectory.json", trajectory)
        telemetry_ref = store.write_json(f"{case_dir}/skill_shadow_telemetry.json", telemetry)
        artifact_manifest = {name: value.as_dict() for name, value in {
            **refs, "trajectory": trajectory_ref, "skill_shadow_telemetry": telemetry_ref,
        }.items()}
        store.write_json(f"{case_dir}/artifact_manifest.json", artifact_manifest)
        conclusion = action["status"]
        return {"case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id,
                "support_status": case.support_status, "conclusion": conclusion,
                "model_status": identity.actual["status"], "raw_ref": raw_ref.ref,
                "raw_sha256": raw_ref.sha256, "judge_certificate": certificate["status"],
                "ptc_used": False, "skill_context_injected": False}

    def _producer_observation(self, case: FrozenCase, actual: dict[str, Any], run_id: str,
                              attempt_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, bool]]:
        if case.case_id == "case_015":
            return self._probe_cancel_building(run_id, attempt_id)
        if case.case_id == "case_016":
            return ({"status": "NOT_RUN", "reason": "real_comsol_cancellation_excluded_by_frozen_phase2a_baseline"},
                    {"state": "NOT_RUN", "real_comsol": False, "safe_cancel_unavailable": True}, {})
        if case.case_id == "case_017":
            return self._probe_reconciliation(run_id, attempt_id)
        if case.case_id == "case_018":
            return self._probe_stale_attempt(run_id, attempt_id)
        if case.support_status.startswith("UNSUPPORTED"):
            return ({"status": "UNSUPPORTED_EXPLICIT", "reason": "outside_declared_workbench_capability"}, None,
                    {"unsupported_explicit": True})
        if actual.get("status") != "READY":
            return ({"status": "MODEL_UNAVAILABLE", "reason": "declared_target_model_unavailable_no_fallback"}, None,
                    {"model_unavailable": True})
        if self.research_probe_mode:
            return ({"status": "NOT_RUN", "reason": "research_probe_mode_disables_external_scientific_execution"}, None, {})
        raise RuntimeError("A ready target requires an explicit producer integration; refusing implicit or mock execution")

    def _scratch_store(self, case: str) -> tuple[SessionStore, Path, str]:
        scratch = self._working / case
        scratch.mkdir(parents=True, exist_ok=False)
        store = SessionStore(scratch / "journal.sqlite3")
        store.initialize()
        session = store.create("research-round0", case)
        return store, scratch, session["id"]

    def _probe_cancel_building(self, run_id: str, attempt_id: str):
        store, scratch, session_id = self._scratch_store("case_015")
        try:
            store.begin_run(session_id, run_id, {"case": "case_015"})
            current = store.begin_attempt(run_id)
            cancel = store.request_cancel(session_id, force=False)
            completion_rejected = False
            try:
                store.finish_run(run_id, current, "Completed")
            except RunCanceled:
                completion_rejected = True
            store.finish_run(run_id, current, "Canceled")
            journal = store.get_run(run_id)
            lifecycle = {"state": journal["phase"], "outcome": journal["outcome"], "cancel_requested": journal["cancel_requested"],
                         "new_attempts_after_cancel": 0, "controlled_public_safe": True}
            checks = {"persistent_cancel_intent": journal["cancel_requested"], "new_attempt_zero": len(journal["attempts"]) == 1,
                      "not_completed": journal["outcome"] != "Completed" and completion_rejected}
            return {"status": "CANCELED", "reason": "controlled_public_safe_journal_probe", "cancel": cancel}, lifecycle, checks
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _probe_reconciliation(self, run_id: str, attempt_id: str):
        store, scratch, session_id = self._scratch_store("case_017")
        try:
            store.begin_run(session_id, run_id, {"case": "case_017"})
            current = store.begin_attempt(run_id)
            store.update_run(run_id, current, phase="SOLVING")
            results = store.reconcile_active_runs()
            journal = store.get_run(run_id)
            lifecycle = {"state": journal["phase"], "outcome": journal["outcome"], "controlled_public_safe": True,
                         "reconciliation_records": len(results)}
            checks = {"restart_reconciliation": bool(results), "ghost_solving_zero": journal["phase"] != "SOLVING",
                      "false_completed_zero": journal["outcome"] != "Completed"}
            return {"status": "INTERRUPTED", "reason": "controlled_public_safe_reconciliation_probe"}, lifecycle, checks
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _probe_stale_attempt(self, run_id: str, attempt_id: str):
        store, scratch, session_id = self._scratch_store("case_018")
        try:
            store.begin_run(session_id, run_id, {"case": "case_018"})
            current = store.begin_attempt(run_id)
            stale_rejected = False
            try:
                store.assert_current(run_id, attempt_id if attempt_id != current else "wrong-attempt")
            except StaleAttempt:
                stale_rejected = True
            store.request_cancel(session_id, force=False)
            store.finish_run(run_id, current, "Canceled")
            journal = store.get_run(run_id)
            lifecycle = {"state": journal["phase"], "outcome": journal["outcome"], "controlled_public_safe": True}
            checks = {"stale_attempt_rejected": stale_rejected, "scientific_certification_zero": journal["outcome"] != "Completed"}
            return {"status": "STALE_ATTEMPT_REJECTED", "reason": "controlled_public_safe_identity_probe"}, lifecycle, checks
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    @staticmethod
    def _scientific_spec(case: FrozenCase, plan: dict[str, Any], run_id: str, attempt_id: str) -> dict[str, Any]:
        geometry = plan.get("geometry") or []
        boundaries = [item if isinstance(item, dict) else {"description": str(item), "source": "existing_plan"}
                      for item in (plan.get("boundary_conditions") or [])]
        observables = [{"name": name, "source": "frozen_case.expected_checks"} for name in case.expected_checks]
        tolerances = [{"metric": "deterministic_contract", "rule": "existing workflow threshold; unchanged"}] if observables else []
        supported = not case.support_status.startswith("UNSUPPORTED")
        return {
            "spec_id": f"batch001:{case.case_id}", "case_id": case.case_id,
            "core": {"domain": {"geometry": geometry, "dimension": "from_existing_plan" if geometry else None},
                     "governing_model": {"workflow_id": case.workflow_id, "physics": list(case.physics), "supported": supported},
                     "boundary_conditions": boundaries, "initial_conditions": None,
                     "observables": observables, "tolerances": tolerances},
            "domain_profile": {"supported": supported, "selection_sensitive": case.selection_sensitive,
                               "invariants": list(case.expected_checks), "boundary_risk": "UNKNOWN",
                               "boundary_risk_reasons": ["No execution-time perturbation evidence in Batch 001"]},
            "run_instance": {"case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id,
                             "execution_policy": "direct_closed_loop", "model_policy": "declared-target-no-fallback",
                             "resource_settings": "existing Workbench Resource Guard; not invoked without execution"},
            "provenance": {"prompt": "frozen_case.exact_prompt", "plan": "existing deterministic offline planner snapshot",
                           "expected_checks": "frozen evaluator metadata", "generated_by_llm": False},
        }

    def _policy_snapshot(self) -> dict[str, Any]:
        source_root = Path(__file__).resolve().parent
        files = {}
        for path in sorted(source_root.glob("*.py")):
            files[path.name] = sha256_file(path)
        runner = source_root.parents[2] / "scripts" / "run_research_batch001.py"
        if runner.is_file():
            files["scripts/run_research_batch001.py"] = sha256_file(runner)
        repository = source_root.parents[2]
        frozen_core = {}
        for relative in (
            "backend/app/validators/execution_verdict.py", "backend/app/scientific/quantity.py",
            "backend/app/session/run_journal.py", "backend/app/agent/runtime.py",
            "backend/app/agent/tool_registry.py", "backend/app/scientific/resource_guard.py",
            "backend/app/agent/model_card.py",
        ):
            path = repository / relative
            frozen_core[relative] = sha256_file(path)
        return {
            "batch_id": self.batch_id, "case_count": 20, "execution_policy": "direct_closed_loop",
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "ptc_scientific_execution": False, "judge_mode": "shadow", "judge_can_block": False,
            "model_policy": {"requested": "local-medium", "provider": "preferred-local", "fallback_allowed": False},
            "research_source_sha256": files, "frozen_core_sha256": frozen_core,
            "phase2a_core_modified_by_runner": False,
        }

    def _summary(self, results: list[dict[str, Any]], started_at: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in results:
            counts[item["conclusion"]] = counts.get(item["conclusion"], 0) + 1
        return {
            "batch_id": self.batch_id, "review_units": len(results), "started_at": started_at,
            "completed_at": utc_now(), "frozen": True, "results": results, "conclusion_counts": counts,
            "scientific_success_claims": 0, "real_comsol_scientific_runs": 0,
            "real_comsol_cancellation_tested": False, "desktop_crash_tested": False,
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "ptc_used_in_scientific_runs": False, "judge_mode": "shadow",
        }

    @staticmethod
    def _raw_index(results: list[dict[str, Any]]) -> str:
        lines = ["# Batch 001 Raw Index", "", "Raw files are immutable and identified by SHA-256.", "",
                 "| Case | Conclusion | Model | Raw ref | SHA-256 |", "|---|---|---|---|---|"]
        for item in results:
            lines.append(f"| {item['case_id']} | {item['conclusion']} | {item['model_status']} | `{item['raw_ref']}` | `{item['raw_sha256']}` |")
        return "\n".join(lines) + "\n"

    def _assemble_reviewer_packet(self, store: ImmutableArtifactStore, summary: dict[str, Any], policy: dict[str, Any]) -> None:
        packet = self.output / "reviewer_packet"
        shutil.copyfile(self.output / "trajectories.jsonl", packet / "trajectories.jsonl")
        shutil.copyfile(self.output / "skill_shadow_telemetry.jsonl", packet / "skill_shadow_telemetry.jsonl")
        shutil.copyfile(self.output / "reward_ledger_initial.json", packet / "reward_ledger_initial.json")
        shutil.copyfile(self.output / "skill_registry_snapshot.json", packet / "skill_registry_snapshot.json")
        (packet / "BATCH_001_SUMMARY_MACHINE.json").write_bytes(canonical_json_bytes(summary))
        (packet / "FROZEN_POLICY.json").write_bytes(canonical_json_bytes(policy))
        environment = {
            "os": platform.platform(), "python": platform.python_version(), "pid_recorded": os.getpid(),
            "evidence_boundary": "No API keys, private KB paths, COMSOL JARs, MPH files, or model weights included",
        }
        (packet / "environment.json").write_bytes(canonical_json_bytes(environment))

    def _zip_reviewer_packet(self, destination: Path) -> None:
        packet = self.output / "reviewer_packet"
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(item for item in packet.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(packet).as_posix())
