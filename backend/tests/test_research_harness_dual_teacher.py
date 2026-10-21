from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from app.research.cases import batch_001_cases
from app.research.batch001r import Batch001RRunner
from app.research.dual_teacher import (
    FrozenModelIdentity,
    IdentityMismatch,
    assert_no_evaluator_leakage,
    build_scientific_spec,
    canonical_information_boundary_hash,
    copy_identical_packet,
    neutral_case_input,
    spec_fidelity_check,
    validate_teacher_review,
)
from app.research.freeze import build_source_freeze, verify_source_freeze


def _identity() -> FrozenModelIdentity:
    return FrozenModelIdentity(
        provider="ollama",
        endpoint="http://127.0.0.1:11434",
        provider_version="0.30.9",
        name="qwen3:4b",
        family="qwen3",
        parameter_size="4.0B",
        parameter_count=4_022_468_096,
        quantization="Q4_K_M",
        metadata_context_length=262_144,
        runtime_context_length=8_192,
        revision="2507",
        digest="359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
        weight_blob_sha256="3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f",
    )


def test_neutral_case_excludes_evaluator_only_fields() -> None:
    case = batch_001_cases()[3]
    public = neutral_case_input(case)
    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
    for forbidden in ("expected_checks", "error_tags", "injected_variant", "gold", "oracle", "reward"):
        assert forbidden not in encoded
    assert public["raw_request"] == case.exact_prompt
    assert_no_evaluator_leakage(public)


def test_scientific_spec_does_not_use_expected_checks() -> None:
    case = batch_001_cases()[5]
    public = neutral_case_input(case)
    plan = {"geometry": [{"name": "rectangle"}], "boundary_conditions": [{"side": "left"}]}
    spec = build_scientific_spec(public, plan, run_id="run", attempt_id="attempt")
    encoded = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    assert "frozen_case.expected_checks" not in encoded
    assert "expected_checks" not in encoded
    fidelity = spec_fidelity_check(public["raw_request"], spec)
    assert fidelity["raw_request_preserved_verbatim"] is True
    assert fidelity["loss"] == []
    assert fidelity["invention"] == []


def test_evaluator_leakage_guard_rejects_nested_field() -> None:
    with pytest.raises(ValueError, match="evaluator-only"):
        assert_no_evaluator_leakage({"safe": {"gold": "hidden"}})


def test_identity_lock_rejects_digest_change() -> None:
    identity = _identity()
    actual = identity.as_dict()
    actual["digest"] = "0" * 64
    with pytest.raises(IdentityMismatch):
        identity.assert_matches(actual)


def test_identity_lock_accepts_exact_actual() -> None:
    identity = _identity()
    identity.assert_matches(identity.as_dict())


def test_information_boundary_hash_is_canonical() -> None:
    left = {"cases": [{"b": 2, "a": 1}], "schema": "x"}
    right = {"schema": "x", "cases": [{"a": 1, "b": 2}]}
    assert canonical_information_boundary_hash(left) == canonical_information_boundary_hash(right)


def test_teacher_review_requires_declared_schema_fields() -> None:
    review = {
        "case_id": "case_001", "teacher_id": "teacher_a_codex", "teacher_model": "codex",
        "teacher_revision": "2026-09-20", "information_boundary_hash": "a" * 64,
        "request_interpretation": "Review the request and evidence.", "diagnosis": "Execution is unverified.",
        "required_checks": ["readback"], "potential_failure_modes": ["wrong selection"],
        "recommended_action": "VERIFY", "scientific_verdict": "UNVERIFIABLE",
        "execution_assessment": "UNVERIFIABLE", "numerical_assessment": "UNVERIFIABLE",
        "physical_assessment": "UNVERIFIABLE", "spf_risk": "ELEVATED",
        "governance_risk": "LOW", "capability_assessment": "WITHIN_DECLARED_CAPABILITY",
        "confidence": 0.8, "evidence_refs": ["cases/case_001.json"],
        "uncertainties": ["No solve evidence"], "do_not_claim": ["Scientific validation passed"],
    }
    validate_teacher_review(review)
    del review["do_not_claim"]
    with pytest.raises(ValueError, match="do_not_claim"):
        validate_teacher_review(review)


def test_identical_packet_copy_preserves_bytes(tmp_path: Path) -> None:
    source = tmp_path / "neutral.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("case.json", "{}\n")
    target = tmp_path / "teacher_b.zip"
    result = copy_identical_packet(source, target)
    assert source.read_bytes() == target.read_bytes()
    assert result["equal"] is True


def test_source_freeze_detects_mutation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    tracked = root / "module.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    freeze = build_source_freeze(root, ["module.py"], batch_id="batch_001r")
    assert verify_source_freeze(root, freeze)["valid"] is True
    tracked.write_text("value = 2\n", encoding="utf-8")
    result = verify_source_freeze(root, freeze)
    assert result["valid"] is False
    assert result["mismatches"][0]["path"] == "module.py"


def test_batch_case_count_remains_exactly_twenty() -> None:
    assert len(batch_001_cases()) == 20


def test_batch001r_fake_integration_builds_neutral_boundary(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    schema_dir = repository / "research" / "teachers"
    schema_dir.mkdir(parents=True)
    schema_dir.joinpath("teacher_review.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )

    class FakeProducer:
        identity = _identity()

        def assert_identity(self):
            return self.identity.as_dict()

        def generate(self, case_input, *, seed):
            parsed = {
                "request_interpretation": "Preserve the request.",
                "diagnosis": "Execution evidence is absent.",
                "required_checks": ["model readback"],
                "potential_failure_modes": ["wrong selection"],
                "recommended_action": "VERIFY",
                "completion_conditions": ["deterministic evidence exists"],
                "uncertainties": ["not executed"],
                "do_not_claim": ["scientific validation"],
            }
            return {
                "parsed_output": parsed, "parse_error": None, "completed": True,
                "requested_model": "qwen3:4b", "actual_model": "qwen3:4b",
                "identity_after": self.identity.as_dict(), "no_fallback": True,
                "latency_ms": 1.0,
                "raw_response": {
                    "model": "qwen3:4b", "response": json.dumps(parsed),
                    "thinking": "", "done": True, "done_reason": "stop",
                    "prompt_eval_count": 1, "eval_count": 1,
                },
            }

    config = {
        "batch_id": "batch_001r", "fallback_allowed": False,
        "seed_base": 10, "model_options": {"temperature": 0},
    }
    output = repository / "artifacts" / "batch_001r"
    summary = Batch001RRunner(
        repository, output, producer=FakeProducer(),
        source_freeze={"files": {}}, config=config,
    ).run()
    assert summary["case_count"] == 20
    assert summary["model_calls_completed"] == 20
    assert output.joinpath("FROZEN").is_file()
    assert output.joinpath("reviewer_packet_teacher_neutral.zip").is_file()
    packet = output / "reviewer_packet_teacher_neutral"
    assert len(list(packet.joinpath("cases").glob("case_*.json"))) == 20
    for path in packet.rglob("*.json"):
        assert_no_evaluator_leakage(json.loads(path.read_text(encoding="utf-8")))
