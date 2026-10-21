from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.artifacts import ImmutableArtifactStore, sha256_bytes
from app.research.batch import BatchFreezer, BatchRunner
from app.research.cases import batch_001_cases
from app.research.judge import ShadowScientificJudge
from app.research.model_adapter import ModelAdapter, ModelRequest
from app.research.model_router import ModelRouter
from app.research.normalizer import PhysicsObservationNormalizer
from app.research.ptc import RestrictedPTC
from app.research.skills import SkillLoader, SkillManifest, SkillRegistry, SkillRouter, SkillTelemetry, SkillValidator
from app.research.trajectory import TrajectoryRecorder, is_silent_physical_failure


def _manifest(**updates):
    data = {
        "id": "boundary-selection",
        "version": "0.1.0",
        "description": "Select current geometry entities from current evidence.",
        "risk": "high",
        "invocation": "router-only",
        "status": "candidate",
        "entrypoint": "reference-only",
        "physics_tags": ["generic"],
        "step_tags": ["selection"],
        "error_tags": ["wrong-boundary"],
        "required_tools": ["model_inspect"],
        "tests": ["held-out-selection"],
        "completion_criteria": ["Selection is justified by current model evidence."],
        "provenance": {"source": "human", "source_refs": ["package:v0.3"]},
    }
    data.update(updates)
    return SkillManifest.from_dict(data)


def test_raw_artifact_is_immutable_and_hash_linked(tmp_path: Path):
    store = ImmutableArtifactStore(tmp_path)
    ref = store.write_json("raw/case_001.json", {"value": 7})
    assert ref.sha256 == sha256_bytes(ref.path.read_bytes())
    with pytest.raises(FileExistsError):
        store.write_json("raw/case_001.json", {"value": 8})


def test_trajectory_is_append_only_and_links_raw(tmp_path: Path):
    store = ImmutableArtifactStore(tmp_path)
    raw = store.write_json("raw/observation.json", {"temperature": {"value": 300, "unit": "K"}})
    recorder = TrajectoryRecorder(tmp_path / "trajectory.jsonl", store)
    record = {
        "case_id": "case_001",
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "step_id": "step-1",
        "observation": {"raw_ref": raw.ref, "raw_sha256": raw.sha256, "normalized": {}},
    }
    recorder.append(record)
    recorder.append({**record, "step_id": "step-2"})
    lines = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["observation"]["raw_sha256"] == raw.sha256
    with pytest.raises(ValueError, match="raw hash"):
        recorder.append({**record, "step_id": "step-3", "observation": {**record["observation"], "raw_sha256": "0" * 64}})


def test_normalizer_preserves_scientific_fields_and_display_summary_is_non_authoritative():
    raw = {
        "values": [{"name": "D", "value": 1e-9, "unit": "m^2/s"}],
        "selections": [{"name": "left", "ids": [1], "dimension": 1}],
        "geometry": {"dimension": 2, "topology_revision": "g7"},
        "solver": {"state": "converged", "warnings": ["conditioning"]},
        "readback": {"left_potential": {"value": 0, "unit": "V"}},
    }
    normalized = PhysicsObservationNormalizer().normalize(raw)
    assert normalized["values"] == raw["values"]
    assert normalized["selections"] == raw["selections"]
    assert normalized["geometry"] == raw["geometry"]
    assert normalized["solver"] == raw["solver"]
    assert normalized["readback"] == raw["readback"]
    assert normalized["summary_role"] == "display_only"
    assert "deterministic_evidence" not in normalized["display_summary"]


def test_model_adapter_records_unavailable_without_fallback_impersonation():
    adapter = ModelAdapter(catalog=[])
    identity = adapter.resolve(ModelRequest("local-medium", provider="preferred-local"))
    assert identity.requested["name"] == "local-medium"
    assert identity.actual["status"] == "UNAVAILABLE"
    assert identity.actual["name"] is None
    assert identity.fallback_chain == []
    assert identity.fallback_reason == "no_declared_target_available"


def test_model_adapter_makes_fallback_identity_explicit():
    adapter = ModelAdapter(catalog=[{"provider": "ollama", "name": "small", "status": "Ready", "size": "small"}])
    identity = adapter.resolve(ModelRequest("medium", provider="ollama", allow_fallback=True))
    assert identity.requested["name"] == "medium"
    assert identity.actual["name"] == "small"
    assert identity.fallback_chain[0]["name"] == "small"
    assert identity.fallback_reason == "declared_post_resolution_fallback"


def test_model_router_has_only_round0_nonlearned_policies():
    router = ModelRouter()
    fixed = router.route("fixed_model", {"requested_model": "local-medium"})
    escalated = router.route("explicit_escalation", {"requested_model": "local-small", "escalate_to": "frontier", "reason": "user_requested"})
    stub = router.route("rule_router_stub", {"requested_model": "local-small", "risk": "high"})
    assert fixed.selected_model == "local-medium"
    assert escalated.selected_model == "frontier"
    assert escalated.reason_tags == ("user_requested",)
    assert stub.selected_model == "local-small"
    assert stub.advisory == "escalation_suggested_not_applied"
    with pytest.raises(ValueError, match="learned"):
        router.route("learned_router", {})


@pytest.mark.parametrize("execution,numerical,physical,expected", [
    (False, False, False, False), (False, False, True, False),
    (False, True, False, False), (False, True, True, False),
    (True, False, False, False), (True, False, True, False),
    (True, True, False, True), (True, True, True, False),
])
def test_spf_truth_table(execution, numerical, physical, expected):
    assert is_silent_physical_failure(execution, numerical, physical) is expected


def test_skill_registry_validator_and_candidate_lifecycle():
    registry = SkillRegistry()
    manifest = _manifest()
    registry.register(manifest)
    assert registry.get("boundary-selection").status == "candidate"
    assert registry.get("boundary-selection").content_hash
    with pytest.raises(ValueError, match="production"):
        registry.promote("boundary-selection")
    assert SkillValidator({"model_inspect"}).validate(manifest).valid


def test_skill_loader_progressive_disclosure_is_disabled_for_batch001():
    registry = SkillRegistry.with_reference_candidates()
    loader = SkillLoader(registry)
    pointers = loader.pointers()
    assert pointers and all("content" not in item for item in pointers)
    with pytest.raises(PermissionError, match="S0_no_skill"):
        loader.load("boundary-selection", skill_condition="S0_no_skill")


def test_skill_telemetry_is_append_only(tmp_path: Path):
    telemetry = SkillTelemetry(tmp_path / "skills.jsonl")
    telemetry.record({"event": "skill_shadow_route", "case_id": "case_001"})
    telemetry.record({"event": "skill_shadow_route", "case_id": "case_002"})
    assert len((tmp_path / "skills.jsonl").read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize("updates,reason", [
    ({"completion_criteria": []}, "completion_criteria"),
    ({"required_tools": ["missing_tool"]}, "missing_required_tool"),
    ({"description": "Always use boundary id 7"}, "stale_entity_assumption"),
    ({"tests": []}, "tests"),
])
def test_skill_static_validator_rejects_unsafe_manifests(updates, reason):
    result = SkillValidator({"model_inspect"}).validate(_manifest(**updates))
    assert not result.valid
    assert any(reason in issue for issue in result.issues)


def test_rule_skill_router_is_explainable_and_bounded():
    registry = SkillRegistry.with_reference_candidates()
    route = SkillRouter().shadow_route({
        "case_id": "case_008", "physics": ["Transport of Diluted Species"],
        "step_type": "selection", "error_tags": ["mass-balance"],
        "available_tools": ["model_inspect"], "risk": "high",
    }, registry)
    assert 0 <= len(route.selected) <= 3
    assert route.candidates
    assert all(item["reason_tags"] for item in route.candidates)
    assert route.skill_context_injected is False
    assert route.skill_condition == "S0_no_skill"


def test_wrong_but_plausible_skill_can_be_recorded_but_not_injected():
    route = SkillRouter().shadow_route({
        "case_id": "case_010", "physics": ["Solid Mechanics"],
        "step_type": "boundary-condition", "error_tags": ["wrong-boundary"],
        "wrong_skill_suspected": True,
    }, SkillRegistry.with_reference_candidates())
    assert route.wrong_skill_suspected is True
    assert route.skill_context_injected is False
    assert route.skill_hashes == []


def test_shadow_routes_do_not_change_producer_payload_bytes():
    payload = {"messages": [{"role": "user", "content": "solve heat"}], "tools": ["plan"], "model": "local-medium"}
    before = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    SkillRouter().shadow_route({"physics": ["Heat Transfer"], "step_type": "plan"}, SkillRegistry.with_reference_candidates())
    after = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert before == after


@pytest.mark.parametrize("program,allowed", [
    ([{"op": "set_param", "args": {"name": "L", "value": "1[m]"}}], True),
    ([{"op": "shell", "args": {"command": "whoami"}}], False),
    ([{"op": "network", "args": {"url": "https://example.com"}}], False),
    ([{"op": "filesystem", "args": {"path": "x"}}], False),
    ([{"op": "java_reflection", "args": {"class": "x"}}], False),
])
def test_restricted_ptc_whitelist(program, allowed):
    report = RestrictedPTC().dry_run(program)
    assert report.completed is allowed
    assert bool(report.policy_violations) is (not allowed)


def test_restricted_ptc_requires_closed_loop_for_high_risk_steps():
    report = RestrictedPTC().dry_run([{"op": "assign_boundary", "args": {"selection": "left"}}])
    assert report.closed_loop_required is True
    assert report.completed is False
    assert "closed_loop_required" in report.policy_violations


def test_reward_ledger_reserves_final_writes_for_external_reviewer(tmp_path: Path):
    freezer = BatchFreezer(tmp_path)
    freezer.initialize_reward_ledger()
    with pytest.raises(PermissionError):
        freezer.append_reward({"case_id": "case_001", "reward": 3}, actor="producer")


def test_judge_gates_and_certificate_do_not_overwrite_verdict():
    judge = ShadowScientificJudge()
    spec = {
        "case_id": "case_x", "core": {"domain": {}, "governing_model": {"supported": False},
        "boundary_conditions": [], "initial_conditions": None, "observables": [], "tolerances": []},
        "domain_profile": {"supported": False}, "run_instance": {}, "provenance": {},
    }
    verdict = {"command_success": True, "model_state_verified": True, "scientific_validation_success": False, "completed": False}
    report, certificate = judge.evaluate(spec, raw_observation={}, authoritative_verdict=verdict)
    assert report["pre_gates"]["J1_spec_integrity"]["status"] != "PASS"
    assert report["pre_gates"]["J4_capability"]["status"] == "FAIL"
    assert certificate["status"] != "VERIFIED_WITHIN_DECLARED_CHECKS"
    assert verdict["completed"] is False
    assert report["judge_mode"] == "shadow"
    assert report["judge_can_block"] is False


def test_judge_converged_but_failed_invariant_is_flagged():
    spec = {
        "case_id": "case_y", "core": {"domain": {"dimension": 2}, "governing_model": {"supported": True},
        "boundary_conditions": [{"name": "left"}], "initial_conditions": None,
        "observables": [{"name": "mass_balance"}], "tolerances": [{"metric": "relative_error", "value": 1e-6}]},
        "domain_profile": {"supported": True}, "run_instance": {}, "provenance": {},
    }
    report, certificate = ShadowScientificJudge().evaluate(
        spec,
        raw_observation={"solver": {"state": "converged"}, "checks": {"mass_balance": False}},
        authoritative_verdict={"completed": False},
    )
    assert any(item["status"] == "FLAG" for item in report["post_audit"])
    assert certificate["status"] == "FLAGGED_FOR_REVIEW"


def test_shadow_judge_is_byte_noninterfering_for_producer_payload():
    producer = {"model": "local-medium", "action": {"tool": "plan", "arguments": {"x": 1}}, "retry": 0}
    before = json.dumps(producer, sort_keys=True, separators=(",", ":")).encode()
    spec = {
        "case_id": "case_z", "core": {"domain": {}, "governing_model": {"supported": False},
        "boundary_conditions": [], "initial_conditions": None, "observables": [], "tolerances": []},
        "domain_profile": {"supported": False}, "run_instance": {}, "provenance": {},
    }
    ShadowScientificJudge().evaluate(spec, raw_observation={}, authoritative_verdict=None)
    after = json.dumps(producer, sort_keys=True, separators=(",", ":")).encode()
    assert before == after


def test_batch_definitions_are_exactly_twenty_and_frozen_contracts():
    cases = batch_001_cases()
    assert [item.case_id for item in cases] == [f"case_{index:03d}" for index in range(1, 21)]
    assert all(item.execution_policy == "direct_closed_loop" for item in cases)
    assert all(item.skill_condition == "S0_no_skill" for item in cases)
    assert all(item.skill_context_injected is False for item in cases)
    assert all(item.exact_prompt for item in cases)


def test_batch_runner_freezes_complete_reviewer_packet_and_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "batch_001"
    runner = BatchRunner(output, model_adapter=ModelAdapter(catalog=[]), research_probe_mode=True)
    summary = runner.run()
    assert summary["review_units"] == 20
    assert summary["frozen"] is True
    assert len(list((output / "raw").glob("case_*.json"))) == 20
    assert len((output / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()) == 20
    assert len((output / "skill_shadow_telemetry.jsonl").read_text(encoding="utf-8").splitlines()) == 20
    telemetry = [json.loads(line) for line in (output / "skill_shadow_telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(item["wrong_skill_suspected"] is False for item in telemetry)
    assert all("error_signature_match" not in candidate["reason_tags"] for item in telemetry for candidate in item["candidates"])
    required = {
        "BATCH_001_MANIFEST.json", "BATCH_001_SUMMARY_MACHINE.json", "BATCH_001_RAW_INDEX.md",
        "reviewer_packet.zip", "FROZEN",
    }
    assert required <= {path.name for path in output.iterdir()}
    assert len(list((output / "reviewer_packet" / "cases").glob("case_*"))) == 20
    for case_dir in (output / "reviewer_packet" / "cases").glob("case_*"):
        spec = json.loads((case_dir / "scientific_spec.json").read_text(encoding="utf-8"))
        assert all(isinstance(item, dict) for item in spec["core"]["boundary_conditions"])
        post = json.loads((case_dir / "judge_post_report.json").read_text(encoding="utf-8"))
        assert post["judge_mode"] == "shadow"
        assert set(post["pre_gates"]) == {"J1_spec_integrity", "J2_boundary_selection", "J3_numerical_admissibility", "J4_capability", "J5_resource_feasibility"}
        assert post["post_audit"]
    with pytest.raises(FileExistsError):
        BatchRunner(output, model_adapter=ModelAdapter(catalog=[]), research_probe_mode=True).run()
