"""Dual-teacher protocol primitives layered above the frozen Workbench runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any
import urllib.error
import urllib.request

from .artifacts import canonical_json_bytes
from .cases import FrozenCase


FORBIDDEN_EVALUATOR_KEYS = frozenset({
    "expected_checks", "error_tags", "injected_variant", "gold", "gold_labels",
    "oracle", "oracle_labels", "reward", "expected_evaluator_labels", "teacher_b_output",
    "dsh_review", "dsh_passa", "dsh_passb",
})


class IdentityMismatch(RuntimeError):
    """Raised when the live provider identity differs from the frozen identity."""


class ProviderUnavailable(RuntimeError):
    """Raised when the fixed local provider cannot complete a real request."""


@dataclass(frozen=True, slots=True)
class FrozenModelIdentity:
    provider: str
    endpoint: str
    provider_version: str
    name: str
    family: str
    parameter_size: str
    parameter_count: int
    quantization: str
    metadata_context_length: int
    runtime_context_length: int
    revision: str
    digest: str
    weight_blob_sha256: str
    research_class: str = "local-medium"
    fallback_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def assert_matches(self, actual: dict[str, Any]) -> None:
        expected = self.as_dict()
        required = (
            "provider", "endpoint", "provider_version", "name", "family",
            "parameter_size", "parameter_count", "quantization",
            "metadata_context_length", "runtime_context_length", "revision",
            "digest", "weight_blob_sha256", "research_class", "fallback_allowed",
        )
        mismatches = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in required if actual.get(key) != expected[key]
        }
        if mismatches:
            raise IdentityMismatch(f"FROZEN_MODEL_IDENTITY_MISMATCH: {json.dumps(mismatches, sort_keys=True)}")


def neutral_case_input(case: FrozenCase) -> dict[str, Any]:
    """Project a frozen case to the producer/teacher information boundary."""
    value = {
        "schema_version": "neutral-case-v1",
        "case_id": case.case_id,
        "title": case.title,
        "physics": list(case.physics),
        "step_type": case.step_type,
        "raw_request": case.exact_prompt,
        "capability_contract": {
            "workflow_id": case.workflow_id,
            "inside_declared_workbench_capability": case.workflow_id is not None,
            "new_physics_allowed": False,
        },
    }
    assert_no_evaluator_leakage(value)
    return value


def assert_no_evaluator_leakage(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_EVALUATOR_KEYS:
                raise ValueError(f"evaluator-only field at {path}.{key}")
            assert_no_evaluator_leakage(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_evaluator_leakage(item, f"{path}[{index}]")


def build_scientific_spec(case_input: dict[str, Any], plan: dict[str, Any], *, run_id: str,
                          attempt_id: str) -> dict[str, Any]:
    """Build a non-authoritative spec without evaluator metadata."""
    assert_no_evaluator_leakage(case_input)
    raw_request = str(case_input["raw_request"])
    capability = dict(case_input["capability_contract"])
    boundaries = [
        item if isinstance(item, dict) else {"description": str(item), "source": "deterministic_plan"}
        for item in (plan.get("boundary_conditions") or [])
    ]
    spec = {
        "schema_version": "scientific-spec-v1.1-dual-teacher",
        "spec_id": f"batch_001r:{case_input['case_id']}",
        "case_id": case_input["case_id"],
        "source_request": {
            "text": raw_request,
            "sha256": hashlib.sha256(raw_request.encode("utf-8")).hexdigest(),
            "preservation": "verbatim",
        },
        "core": {
            "domain": {"geometry": plan.get("geometry") or [], "dimension": plan.get("dimension")},
            "governing_model": {
                "workflow_id": capability.get("workflow_id"),
                "physics": list(case_input.get("physics") or []),
                "supported": bool(capability.get("inside_declared_workbench_capability")),
            },
            "boundary_conditions": boundaries,
            "initial_conditions": plan.get("initial_conditions"),
            "observables": [{"source": "raw_request", "requirement": raw_request}],
            "tolerances": [{"status": "not_declared", "rule": "must be supplied before certification"}],
        },
        "domain_profile": {
            "supported": bool(capability.get("inside_declared_workbench_capability")),
            "selection_sensitive": False,
            "invariants": [],
            "boundary_risk": "UNKNOWN",
            "boundary_risk_reasons": ["No execution/readback evidence is present in producer decision batch"],
        },
        "deterministic_plan_snapshot": plan,
        "run_instance": {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "execution_policy": "direct_closed_loop",
            "model_policy": "ollama:qwen3:4b@frozen-digest-no-fallback",
        },
        "provenance": {
            "raw_request_preserved": True,
            "deterministic_plan_is_authoritative_execution": False,
            "evaluator_metadata_used": False,
            "generated_by_llm": False,
        },
    }
    assert_no_evaluator_leakage(spec)
    return spec


def spec_fidelity_check(raw_request: str, spec: dict[str, Any]) -> dict[str, Any]:
    source = spec.get("source_request") if isinstance(spec.get("source_request"), dict) else {}
    preserved = source.get("text") == raw_request
    digest = hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
    return {
        "schema_version": "spec-fidelity-v1",
        "raw_request_sha256": digest,
        "spec_request_sha256": source.get("sha256"),
        "raw_request_preserved_verbatim": preserved and source.get("sha256") == digest,
        "loss": [] if preserved else ["raw_request_not_preserved_verbatim"],
        "invention": [],
        "semantic_distortion": "UNVERIFIED_DETERMINISTIC_PROJECTION_REQUIRES_REVIEW",
        "normalization": {
            "plan_snapshot_sha256": hashlib.sha256(
                canonical_json_bytes(spec.get("deterministic_plan_snapshot") or {})
            ).hexdigest(),
            "plan_snapshot_role": "non_authoritative_projection",
        },
        "evaluator_metadata_used": False,
    }


def canonical_information_boundary_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


TEACHER_REVIEW_REQUIRED_FIELDS = (
    "case_id", "teacher_id", "teacher_model", "teacher_revision",
    "information_boundary_hash", "request_interpretation", "diagnosis",
    "required_checks", "potential_failure_modes", "recommended_action",
    "scientific_verdict", "execution_assessment", "numerical_assessment",
    "physical_assessment", "spf_risk", "governance_risk",
    "capability_assessment", "confidence", "evidence_refs", "uncertainties",
    "do_not_claim",
)


def validate_teacher_review(review: dict[str, Any]) -> None:
    missing = [name for name in TEACHER_REVIEW_REQUIRED_FIELDS if name not in review]
    if missing:
        raise ValueError("missing teacher review fields: " + ", ".join(missing))
    if not isinstance(review["confidence"], (int, float)) or not 0 <= float(review["confidence"]) <= 1:
        raise ValueError("confidence must be in [0, 1]")
    for field in ("required_checks", "potential_failure_modes", "evidence_refs", "uncertainties", "do_not_claim"):
        if not isinstance(review[field], list):
            raise ValueError(f"{field} must be a list")


def copy_identical_packet(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copyfile(source, destination)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"source_sha256": source_hash, "destination_sha256": destination_hash,
            "equal": source_hash == destination_hash}


class OllamaProducer:
    """Exact-identity, no-fallback Ollama producer for Batch 001-R."""

    response_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "request_interpretation", "diagnosis", "required_checks",
            "potential_failure_modes", "recommended_action",
            "completion_conditions", "uncertainties", "do_not_claim",
        ],
        "properties": {
            "request_interpretation": {"type": "string"},
            "diagnosis": {"type": "string"},
            "required_checks": {"type": "array", "items": {"type": "string"}},
            "potential_failure_modes": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {"type": "string", "enum": ["PLAN", "VERIFY", "REFUSE_UNSUPPORTED", "ESCALATE"]},
            "completion_conditions": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
            "do_not_claim": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, identity: FrozenModelIdentity, *, timeout_seconds: float = 180.0) -> None:
        self.identity = identity
        self.timeout_seconds = timeout_seconds

    def _api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = canonical_json_bytes(payload) if payload is not None else None
        request = urllib.request.Request(
            f"{self.identity.endpoint}{path}", data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ProviderUnavailable(f"OLLAMA_PROVIDER_UNAVAILABLE: {exc}") from exc

    def current_identity(self) -> dict[str, Any]:
        version = str(self._api("/api/version").get("version") or "")
        tags = self._api("/api/tags").get("models") or []
        target = next((item for item in tags if item.get("name") == self.identity.name), None)
        if target is None:
            raise ProviderUnavailable(f"fixed model absent: {self.identity.name}")
        show = self._api("/api/show", {"model": self.identity.name, "verbose": False})
        info = show.get("model_info") or {}
        details = target.get("details") or {}
        return {
            "provider": "ollama", "endpoint": self.identity.endpoint,
            "provider_version": version, "name": target.get("name"),
            "family": details.get("family"), "parameter_size": details.get("parameter_size"),
            "parameter_count": int(info.get("general.parameter_count") or 0),
            "quantization": details.get("quantization_level"),
            "metadata_context_length": int(details.get("context_length") or info.get("qwen3.context_length") or 0),
            "runtime_context_length": self.identity.runtime_context_length,
            "revision": str(info.get("general.version") or ""),
            "digest": target.get("digest"),
            "weight_blob_sha256": self.identity.weight_blob_sha256,
            "research_class": "local-medium", "fallback_allowed": False,
        }

    def assert_identity(self) -> dict[str, Any]:
        actual = self.current_identity()
        self.identity.assert_matches(actual)
        return actual

    def generate(self, case_input: dict[str, Any], *, seed: int) -> dict[str, Any]:
        assert_no_evaluator_leakage(case_input)
        identity_before = self.assert_identity()
        prompt = (
            "You are the fixed local producer in a scientific reliability study. "
            "Use only the supplied case. Do not claim that COMSOL ran, converged, or passed validation. "
            "Do not invent evidence. Return the required JSON object.\n\nCASE:\n"
            + json.dumps(case_input, ensure_ascii=False, sort_keys=True)
        )
        request_payload = {
            "model": self.identity.name,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "format": self.response_schema,
            "options": {
                "temperature": 0, "seed": seed,
                "num_ctx": self.identity.runtime_context_length, "num_predict": 1024,
            },
        }
        started = time.perf_counter()
        response = self._api("/api/generate", request_payload)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        if response.get("model") != self.identity.name:
            raise IdentityMismatch(
                f"requested {self.identity.name}, actual {response.get('model')}"
            )
        identity_after = self.assert_identity()
        final_text = str(response.get("response") or "")
        parsed: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            candidate = json.loads(final_text)
            if not isinstance(candidate, dict):
                raise ValueError("producer result is not an object")
            assert_no_evaluator_leakage(candidate)
            parsed = candidate
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
        raw_response = {
            key: response.get(key) for key in (
                "model", "created_at", "response", "thinking", "done", "done_reason",
                "total_duration", "load_duration", "prompt_eval_count",
                "prompt_eval_duration", "eval_count", "eval_duration",
            )
        }
        return {
            "model_call_attempted": True,
            "request": request_payload,
            "raw_response": raw_response,
            "parsed_output": parsed,
            "parse_error": parse_error,
            "latency_ms": latency_ms,
            "requested_model": self.identity.name,
            "actual_model": response.get("model"),
            "identity_before": identity_before,
            "identity_after": identity_after,
            "no_fallback": response.get("model") == self.identity.name,
            "completed": bool(response.get("done")),
            "done_reason": response.get("done_reason"),
        }
