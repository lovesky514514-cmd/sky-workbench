from __future__ import annotations

import json
import sys
from pathlib import Path

from app.memory.store import MemoryAdmissionPolicy, MemoryCandidate, MemoryStore, MemoryType, ValidationStatus
from app.research.benchmark_framework import BenchmarkCase, BenchmarkStatus, validate_benchmark_case
from app.session.store import SessionStore


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import rc3_launcher  # noqa: E402
import rc3_setup  # noqa: E402
import run_phase2_model_benchmark as model_benchmark  # noqa: E402


def _store(tmp_path: Path) -> MemoryStore:
    sessions = SessionStore(tmp_path / "sessions.sqlite3")
    sessions.initialize()
    return MemoryStore(sessions)


def test_failure_memory_is_negative_only_and_retrievable(tmp_path: Path) -> None:
    memory = _store(tmp_path)
    record = memory.admit_failure(
        session_id="session-1",
        run_id="run-1",
        attempt_id="attempt-1",
        workflow_id="tds_2d_stationary_diffusion",
        failure_class="selection_readback_mismatch",
        failed_phase="BUILD_VALIDATION",
        attempted_action="RECALL_MEMORY",
        outcome="FatalFailure",
        version_scope="1.1",
    )

    assert record["type"] is MemoryType.NEGATIVE_REPAIR
    assert record["validation_status"] is ValidationStatus.TRUSTED_NEGATIVE
    recalled = memory.relevant("tds_2d_stationary_diffusion", version_scope="1.1")
    assert [item["memory_id"] for item in recalled] == [record["memory_id"]]
    assert recalled[0]["content"]["may_certify"] is False


def test_failure_memory_rejects_missing_identity(tmp_path: Path) -> None:
    memory = _store(tmp_path)
    candidate = MemoryCandidate(
        memory_type=MemoryType.NEGATIVE_REPAIR,
        content={"failure_class": "solver_failure"},
        provenance={"run_id": "", "attempt_id": "attempt-1"},
        version_scope="1.1",
        applicability={"workflow_id": "heat_transfer_2d_stationary"},
        source_outcome="FatalFailure",
        evidence_contract_passed=False,
        scientific_validation_passed=False,
    )
    record = memory.admit(candidate, MemoryAdmissionPolicy())
    assert record["validation_status"] is ValidationStatus.REJECTED


def test_framework_only_3d_case_cannot_claim_validation() -> None:
    case = BenchmarkCase(
        case_id="3d-block-001",
        family="3D_GEOMETRY",
        workflow_id="heat_transfer_3d_stationary",
        status=BenchmarkStatus.FRAMEWORK_ONLY_UNVERIFIED,
        geometry="block",
        evidence_required=("model_readback", "mesh_dof", "solver_convergence", "finite_results"),
    )
    result = validate_benchmark_case(case)
    assert result["scientific_validation_passed"] is False
    assert result["execution_allowed"] is False
    assert result["status"] == "FRAMEWORK_ONLY_UNVERIFIED"


def test_framework_schema_examples_are_non_certifying() -> None:
    path = ROOT / "phase2_completion" / "benchmark_framework" / "THREE_D_MULTIPHYSICS_BENCHMARK_SCHEMA.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["scientific_claims"] == "PROHIBITED_UNTIL_REAL_EVIDENCE"
    assert all(case["status"] == "FRAMEWORK_ONLY_UNVERIFIED" for case in payload["cases"])
    assert all(case["scientific_validation_passed"] is False for case in payload["cases"])


def test_benchmark_scorer_detects_silent_false_pass() -> None:
    expected = {
        "decision": "NONMODEL_ESCALATION",
        "workflow_id": "heat_transfer_2d_stationary",
        "parameters": {},
        "required_evidence": ["energy_balance"],
        "readback_consistent": None,
        "allow_scientific_pass": False,
    }
    parsed = {
        "decision": "CONTINUE",
        "workflow_id": "heat_transfer_2d_stationary",
        "normalized_parameters": {},
        "required_evidence": [],
        "checks": [{"name": "energy_balance", "status": "UNVERIFIED"}],
        "readback_consistent": None,
        "scientific_validation_passed": True,
        "final_status": "PASS",
        "rationale": "command succeeded",
    }
    result = model_benchmark.score_output(parsed, expected)
    assert result["false_pass"] is True
    assert result["decision_correct"] is False


def test_rc3_preflight_has_explicit_status(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "product"
    for relative in (
        "Workbench.exe",
        "scientific-runtime.exe",
        "runtime/ollama/ollama.exe",
        "resources/java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java",
        "resources/semantic_runtime/semantic_runtime_worker.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    monkeypatch.setattr(rc3_launcher, "MODEL_FILES", {
        "qwen3:4b": ("weight-4", 1), "qwen3:8b": ("weight-8", 1),
    })
    for model, (blob, _) in rc3_launcher.MODEL_FILES.items():
        tag = model.rsplit(":", 1)[1]
        manifest = root / "models/ollama/manifests/registry.ollama.ai/library/qwen3" / tag
        weight = root / "models/ollama/blobs" / blob
        manifest.parent.mkdir(parents=True, exist_ok=True)
        weight.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_bytes(b"x")
        weight.write_bytes(b"x")
    monkeypatch.setattr(rc3_launcher, "_ollama_version", lambda _: "ollama version is 0.30.9")
    monkeypatch.setattr(rc3_launcher.platform_probe, "_vram_mib", lambda: (8192, "test GPU"))
    monkeypatch.setattr(rc3_launcher.platform_probe, "_detect_comsol", lambda: {"passed": True, "version": "COMSOL 6.3", "root": str(tmp_path / "comsol")})
    monkeypatch.setattr(rc3_launcher.platform_probe, "_writable", lambda _: True)
    monkeypatch.setattr(rc3_launcher.platform_probe, "_loopback_available", lambda: True)
    result = rc3_launcher.preflight(root)
    assert result["overall_status"] == "READY"
    assert result["selected_model"] == "qwen3:8b"
    assert result["fallback_allowed"] is False


def test_rc3_setup_rejects_manifest_path_escape(tmp_path: Path) -> None:
    source = tmp_path / "payload"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    payload = {"entries": [{"path": "../outside.txt", "bytes": 1, "sha256": rc3_setup._sha256(outside)}]}
    try:
        rc3_setup._verify_payload(source, payload)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("path escape was accepted")
