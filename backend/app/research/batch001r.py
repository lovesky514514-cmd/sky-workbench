"""One-shot, exact-model Batch 001-R runner and neutral packet freezer."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import time
from typing import Any
import uuid
import zipfile

from app.agent.planner import (
    extract_battery_plan, extract_electrochem_plan, extract_heat_transfer_plan,
    extract_multiphysics_plan, extract_solid_plan, extract_tds_plan, generic_plan,
)

from .artifacts import ImmutableArtifactStore, canonical_json_bytes, sha256_file
from .cases import batch_001_cases
from .dual_teacher import (
    FrozenModelIdentity, OllamaProducer, assert_no_evaluator_leakage,
    build_scientific_spec, canonical_information_boundary_hash,
    neutral_case_input, spec_fidelity_check,
)
from .freeze import verify_source_freeze
from .judge import ShadowScientificJudge
from .normalizer import PhysicsObservationNormalizer
from .skills import SkillRegistry, SkillRouter, SkillTelemetry
from .trajectory import TrajectoryRecorder


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _offline_plan(prompt: str) -> dict[str, Any]:
    return (
        extract_multiphysics_plan(prompt) or extract_battery_plan(prompt)
        or extract_tds_plan(prompt) or extract_heat_transfer_plan(prompt)
        or extract_solid_plan(prompt) or extract_electrochem_plan(prompt)
        or generic_plan(prompt)
    )


def _manifest(root: Path, *, exclude: set[str] | None = None) -> dict[str, Any]:
    ignored = set(exclude or ())
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in ignored:
            continue
        files[relative] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    return {"algorithm": "SHA-256", "files": files}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


class Batch001RRunner:
    batch_id = "batch_001r"

    def __init__(self, repository: Path, output: Path, *, producer: OllamaProducer,
                 source_freeze: dict[str, Any], config: dict[str, Any]) -> None:
        self.repository = repository.resolve()
        self.output = output.resolve()
        self.producer = producer
        self.source_freeze = source_freeze
        self.config = config
        self.normalizer = PhysicsObservationNormalizer()
        self.judge = ShadowScientificJudge()
        self.skill_registry = SkillRegistry.with_reference_candidates()
        self.skill_router = SkillRouter()

    def run(self) -> dict[str, Any]:
        if self.output.exists():
            raise FileExistsError(f"Batch identity already exists: {self.output}")
        freeze_check = verify_source_freeze(self.repository, self.source_freeze)
        if not freeze_check["valid"]:
            raise RuntimeError(f"SOURCE_FREEZE_MISMATCH: {freeze_check['mismatches']}")
        current_identity = self.producer.assert_identity()
        if self.config.get("batch_id") != self.batch_id:
            raise RuntimeError("batch config identity mismatch")
        if self.config.get("fallback_allowed") is not False:
            raise RuntimeError("Batch 001-R forbids fallback")
        cases = batch_001_cases()
        if len(cases) != 20:
            raise RuntimeError("Batch 001-R requires exactly 20 cases")

        self.output.mkdir(parents=True, exist_ok=False)
        store = ImmutableArtifactStore(self.output)
        store.write_json("RUN_STARTED.json", {
            "batch_id": self.batch_id, "started_at": utc_now(), "case_count": 20,
            "formal_batch": True, "repair_during_batch_allowed": False,
        })
        store.write_json("FROZEN_MODEL_IDENTITY.json", current_identity)
        store.write_json("SOURCE_FREEZE.json", self.source_freeze)
        policy = self._policy(current_identity)
        store.write_json("FROZEN_POLICY.json", policy)
        store.write_json("skill_registry_snapshot.json", {
            "mode": "shadow_candidate_reference_only", "body_injection_allowed": False,
            "skills": [item.as_dict() for item in self.skill_registry.all()],
        })
        (self.output / "raw").mkdir(exist_ok=True)
        (self.output / "neutral_source" / "cases").mkdir(parents=True, exist_ok=True)
        recorder = TrajectoryRecorder(self.output / "trajectories.jsonl", store)
        skill_telemetry = SkillTelemetry(self.output / "skill_shadow_telemetry.jsonl")
        started_at = utc_now()
        results: list[dict[str, Any]] = []
        try:
            for index, case in enumerate(cases, start=1):
                results.append(self._run_case(index, case, store, recorder, skill_telemetry))
            self.producer.assert_identity()
        except Exception as exc:
            store.write_json("INVALIDATED_INFRASTRUCTURE.json", {
                "batch_id": self.batch_id, "invalidated_at": utc_now(),
                "reason": f"{type(exc).__name__}: {exc}",
                "completed_cases": len(results), "online_repair_permitted": False,
            })
            raise

        summary = self._summary(results, started_at)
        store.write_json("BATCH_001R_SUMMARY_MACHINE.json", summary)
        boundary = self._build_neutral_packet(current_identity, policy, summary)
        store.write_json("INFORMATION_BOUNDARY.json", boundary)
        packet_zip = self.output / "reviewer_packet_teacher_neutral.zip"
        self._zip_directory(self.output / "reviewer_packet_teacher_neutral", packet_zip)
        packet_sha = sha256_file(packet_zip)
        store.write_json("PACKET_FREEZE.json", {
            "information_boundary_hash": boundary["information_boundary_hash"],
            "packet_sha256": packet_sha,
            "packet_size_bytes": packet_zip.stat().st_size,
            "teacher_a_input": packet_zip.name,
            "teacher_b_input_must_be_byte_identical": True,
        })
        manifest = _manifest(self.output, exclude={"BATCH_001R_MANIFEST.json", "FROZEN"})
        manifest.update({"batch_id": self.batch_id, "case_count": 20, "frozen": True})
        manifest_ref = store.write_json("BATCH_001R_MANIFEST.json", manifest)
        store.write_json("FROZEN", {
            "marker": "BATCH_001R_FROZEN", "manifest_sha256": manifest_ref.sha256,
            "information_boundary_hash": boundary["information_boundary_hash"],
        })
        return {**summary, "information_boundary_hash": boundary["information_boundary_hash"],
                "neutral_packet_sha256": packet_sha}

    def _run_case(self, index: int, case: Any, store: ImmutableArtifactStore,
                  recorder: TrajectoryRecorder, telemetry_writer: SkillTelemetry) -> dict[str, Any]:
        started = time.perf_counter()
        run_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        public_case = neutral_case_input(case)
        plan = _offline_plan(public_case["raw_request"])
        spec = build_scientific_spec(public_case, plan, run_id=run_id, attempt_id=attempt_id)
        fidelity = spec_fidelity_check(public_case["raw_request"], spec)
        call = self.producer.generate(public_case, seed=int(self.config["seed_base"]) + index)
        parsed = call.get("parsed_output")
        if parsed is None:
            action = {"status": "PRODUCER_OUTPUT_INVALID", "reason": call.get("parse_error")}
        else:
            action = {
                "status": "PRODUCER_DECISION_RECORDED",
                "recommended_action": parsed.get("recommended_action"),
                "scientific_execution_performed": False,
            }
        checks = {
            "model_call_completed": bool(call.get("completed")),
            "requested_equals_actual": call.get("requested_model") == call.get("actual_model"),
            "no_fallback": call.get("no_fallback") is True,
            "spec_request_preserved": fidelity["raw_request_preserved_verbatim"] is True,
            "producer_output_parseable": parsed is not None,
        }
        raw = {
            "schema_version": "batch001r-raw-v1", "batch_id": self.batch_id,
            "case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id,
            "timestamp": utc_now(), "case_input": public_case,
            "execution_policy": "direct_closed_loop", "producer_call": call,
            "action": action, "checks": checks,
            "scientific_spec": spec, "spec_fidelity_check": fidelity,
            "authoritative_verdict": None,
            "solver": {"state": "not_run", "reason": "producer_decision_batch_no_scientific_execution"},
            "warnings": [
                "Producer output is not COMSOL execution evidence",
                "No scientific success is inferred from a model response or deterministic plan",
            ],
            "ptc_used": False, "skill_context_injected": False,
        }
        assert_no_evaluator_leakage(raw)
        raw_ref = store.write_json(f"raw/{case.case_id}.json", raw)
        normalized = self.normalizer.normalize(raw)
        judge_report, certificate = self.judge.evaluate(
            spec, raw_observation=raw, authoritative_verdict=None
        )
        shadow = self.skill_router.shadow_route({
            "case_id": case.case_id, "physics": list(case.physics),
            "step_type": case.step_type, "error_tags": [],
            "available_tools": ["model_inspect", "result_evaluate", "solver_status"],
            "risk": "medium", "wrong_skill_suspected": False,
        }, self.skill_registry)
        telemetry = {
            "event": "skill_shadow_route", "case_id": case.case_id,
            "run_id": run_id, "attempt_id": attempt_id, "timestamp": utc_now(),
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "candidates": shadow.candidates, "selected": shadow.selected,
            "router_confidence": shadow.router_confidence,
            "reason_tags": shadow.reason_tags, "skill_tokens": 0,
            "outcome": None, "router_version": self.skill_router.version,
        }
        telemetry_writer.record(telemetry)
        trajectory = {
            "schema_version": "batch001r-trajectory-v1", "batch_id": self.batch_id,
            "case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id,
            "step_id": "producer-decision-001", "timestamp": utc_now(),
            "step_type": case.step_type, "physics": list(case.physics),
            "workflow": case.workflow_id, "execution_policy": "direct_closed_loop",
            "requested_model": self.producer.identity.as_dict(),
            "actual_model": call["identity_after"], "fallback_chain": [], "fallback_reason": None,
            "action": action,
            "observation": {"raw_ref": raw_ref.ref, "raw_sha256": raw_ref.sha256,
                            "normalized": normalized, "summary_role": "display_only"},
            "verdict": {
                "execution": None, "numerical": None, "physical": None,
                "silent_physical_failure": False, "evidence_refs": [],
            },
            "metrics": {
                "latency_ms": call["latency_ms"],
                "input_tokens": call["raw_response"].get("prompt_eval_count"),
                "output_tokens": call["raw_response"].get("eval_count"),
                "api_cost_usd": 0,
            },
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "shadow_skill_selected": shadow.selected, "skill_tokens": 0,
            "ptc_used": False, "judge_mode": "shadow",
            "judge_certificate": certificate["status"],
        }
        recorder.append(trajectory)
        neutral = {
            "case_input": public_case,
            "producer_model_identity": call["identity_after"],
            "producer_output": parsed,
            "producer_raw_response": call["raw_response"],
            "producer_operational_checks": checks,
            "scientific_spec": spec,
            "spec_fidelity_check": fidelity,
            "normalized_observation": normalized,
            "shadow_judge_report": judge_report,
            "shadow_judge_certificate": certificate,
            "skill_shadow_telemetry": telemetry,
            "trajectory": trajectory,
            "evidence_limitations": [
                "No scientific solve was executed in this producer-decision batch",
                "UNVERIFIABLE is not PASS",
                "Teacher judgement cannot create an ExecutionVerdict",
            ],
        }
        assert_no_evaluator_leakage(neutral)
        store.write_json(f"neutral_source/cases/{case.case_id}.json", neutral)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "case_id": case.case_id, "run_id": run_id, "attempt_id": attempt_id,
            "conclusion": action["status"], "model_call_completed": checks["model_call_completed"],
            "producer_output_parseable": checks["producer_output_parseable"],
            "judge_certificate": certificate["status"], "elapsed_ms": elapsed_ms,
            "scientific_execution_performed": False,
        }

    def _policy(self, identity: dict[str, Any]) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id, "case_count": 20,
            "execution_policy": "direct_closed_loop",
            "batch_role": "producer_scientific_decision_collection",
            "scientific_execution_in_batch": False,
            "skill_condition": "S0_no_skill", "skill_context_injected": False,
            "judge_mode": "shadow", "judge_can_block": False,
            "ptc_scientific_execution": False, "fallback_allowed": False,
            "fixed_model_identity": identity,
            "model_options": self.config["model_options"],
            "repair_during_batch_allowed": False,
            "teacher_a_can_see_teacher_b": False,
            "teacher_b_can_see_teacher_a": False,
            "evaluator_metadata_in_information_boundary": False,
        }

    @staticmethod
    def _summary(results: list[dict[str, Any]], started_at: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in results:
            counts[item["conclusion"]] = counts.get(item["conclusion"], 0) + 1
        return {
            "batch_id": "batch_001r", "case_count": len(results),
            "started_at": started_at, "completed_at": utc_now(), "frozen": True,
            "results": results, "conclusion_counts": counts,
            "model_calls_attempted": len(results),
            "model_calls_completed": sum(bool(item["model_call_completed"]) for item in results),
            "producer_outputs_parseable": sum(bool(item["producer_output_parseable"]) for item in results),
            "scientific_success_claims": 0, "real_comsol_scientific_runs": 0,
            "skill_context_injections": 0, "ptc_scientific_runs": 0,
            "judge_mode": "shadow", "reward_assignment_performed": False,
        }

    def _build_neutral_packet(self, identity: dict[str, Any], policy: dict[str, Any],
                              summary: dict[str, Any]) -> dict[str, Any]:
        packet = self.output / "reviewer_packet_teacher_neutral"
        if packet.exists():
            raise FileExistsError(packet)
        shutil.copytree(self.output / "neutral_source", packet)
        schema_source = self.repository / "research" / "teachers" / "teacher_review.schema.json"
        shutil.copyfile(schema_source, packet / "teacher_review.schema.json")
        context = {
            "schema_version": "teacher-neutral-boundary-v1",
            "batch_id": self.batch_id, "case_count": 20,
            "producer_model_identity": identity,
            "policy": policy,
            "summary": {
                "model_calls_attempted": summary["model_calls_attempted"],
                "model_calls_completed": summary["model_calls_completed"],
                "producer_outputs_parseable": summary["producer_outputs_parseable"],
                "scientific_success_claims": 0,
            },
            "evidence_priority": [
                "deterministic runtime facts", "exact tool/readback evidence",
                "analytical/reference solution", "conservation/invariants", "physical bounds",
                "numerical convergence/mesh evidence", "capability contract", "teacher judgement",
            ],
            "forbidden_sources": [
                "other teacher output", "oracle", "evaluator labels", "reward", "old DSH review",
            ],
        }
        assert_no_evaluator_leakage(context)
        _write_json(packet / "batch_context.json", context)
        instructions = (
            "# Neutral Teacher Review Protocol\n\n"
            "Review only this packet. Do not infer hidden labels. Produce one schema-valid review per case. "
            "Treat producer text as a proposal, not execution evidence. UNVERIFIABLE is not PASS. "
            "Do not claim COMSOL execution, convergence, scientific validation, or certified error bounds "
            "unless exact evidence is present in the case file.\n"
        )
        (packet / "TEACHER_REVIEW_PROTOCOL.md").write_text(instructions, encoding="utf-8", newline="\n")
        manifest = _manifest(packet)
        boundary_hash = canonical_information_boundary_hash(manifest)
        boundary = {
            "algorithm": "SHA-256 canonical JSON",
            "information_boundary_hash": boundary_hash,
            "manifest": manifest,
            "teacher_a_boundary_hash": boundary_hash,
            "teacher_b_boundary_hash_required": boundary_hash,
        }
        _write_json(packet / "INFORMATION_BOUNDARY.json", boundary)
        return boundary

    @staticmethod
    def _zip_directory(source: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(2026, 9, 20, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

