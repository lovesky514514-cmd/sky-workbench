"""Exactly-once Phase 2 small-model capacity benchmark.

The model pass is oracle-blind and runs three already-installed Ollama models
through one endpoint, prompt, JSON schema and sampling policy.  Evaluation is
an explicit second stage after all output freezes exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "phase2_completion" / "small_model_benchmark"
CASES = OUT / "SMALL_MODEL_CASES.json"
ORACLE_DIR = OUT / "oracle"
ORACLE = ORACLE_DIR / "SMALL_MODEL_ORACLE.json"
ENDPOINT = "http://127.0.0.1:11434"
MODELS = {
    "qwen2_5_3b": ("qwen2.5:3b", "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"),
    "qwen3_4b": ("qwen3:4b", "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"),
    "qwen3_8b": ("qwen3:8b", "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"),
}
SYSTEM_PROMPT = (
    "You are a conservative scientific planning and evidence adjudication component for COMSOL Scientific Workbench. "
    "Use only the supplied request and observed evidence. Never invent model readback, solver results, selection identity, "
    "artifacts, convergence, or scientific success. A PASS requires command success, verified current-attempt model state, "
    "all required scientific checks, and a complete evidence contract. Return exactly one JSON object matching the schema."
)
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision", "workflow_id", "normalized_parameters", "required_evidence", "checks",
        "readback_consistent", "scientific_validation_passed", "final_status", "rationale",
    ],
    "properties": {
        "decision": {"enum": ["CONTINUE", "ASK_HUMAN", "NONMODEL_ESCALATION", "REJECT_UNSUPPORTED"]},
        "workflow_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "normalized_parameters": {
            "type": "object",
            "additionalProperties": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}, {"type": "null"}]},
        },
        "required_evidence": {"type": "array", "items": {"type": "string"}},
        "checks": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "status"],
                "properties": {"name": {"type": "string"}, "status": {"enum": ["PASS", "FAIL", "UNVERIFIED"]}},
            },
        },
        "readback_consistent": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "scientific_validation_passed": {"type": "boolean"},
        "final_status": {"enum": ["PASS", "UNVERIFIABLE", "FAILED", "RESOURCE_BLOCKED", "UNSUPPORTED_EXPLICIT"]},
        "rationale": {"type": "string"},
    },
}
POLICY = {
    "schema_version": "phase2-model-benchmark-policy-v1",
    "endpoint": ENDPOINT,
    "ollama_version_required": "0.30.9",
    "temperature": 0,
    "seed": 271828,
    "num_ctx": 8192,
    "num_predict": 384,
    "thinking": False,
    "stream": False,
    "retry_count": 0,
    "fallback_allowed": False,
    "system_prompt": SYSTEM_PROMPT,
    "output_schema": OUTPUT_SCHEMA,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: refusing overwrite {path}")
    path.write_bytes(canonical(value))


def api(path: str, payload: dict[str, Any] | None = None, timeout: float = 360.0) -> dict[str, Any]:
    data = canonical(payload) if payload is not None else None
    request = urllib.request.Request(
        ENDPOINT + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _case(case_id: str, category: str, text: str) -> dict[str, Any]:
    return {"case_id": case_id, "category": category, "input": text}


def benchmark_cases() -> list[dict[str, Any]]:
    return [
        _case("p2_sm_001", "modeling", "Plan supported 2D stationary heat transfer for a 4 cm by 25 mm plate, k=12 W/(m*K), left 20 C, right 80 C. No execution has occurred."),
        _case("p2_sm_002", "modeling", "Plan supported 2D stationary Transport of Diluted Species for L=1e-2 m, H=5 mm, D=2 cm^2/s, c_left=1 mol/m^3, c_right=0 mol/m^3. No execution has occurred."),
        _case("p2_sm_003", "modeling", "Plan supported uniaxial solid bar: length 120 mm, width 10 mm, E=200 GPa, nu=0.30, left fixed and 1000 N on the right. No execution has occurred."),
        _case("p2_sm_004", "modeling", "Plan primary current distribution on a 10 mm by 5 mm rectangle, conductivity 1 S/m, left 0 V and right 1 V. No execution has occurred."),
        _case("p2_sm_005", "readback", "Heat plan requested copper k=400 W/(m*K). Current-attempt readback reports k=16 W/(m*K). Command succeeded; solver converged; other checks are finite."),
        _case("p2_sm_006", "readback", "TDS plan requested inlet boundary id 1. Current-attempt readback reports selection ids [2]. Command succeeded and solver converged."),
        _case("p2_sm_007", "readback", "A result says run_id=run-new, attempt_id=attempt-old while the active attempt is attempt-new. It otherwise claims all checks passed."),
        _case("p2_sm_008", "evidence", "Solid solve command succeeded and result values are finite, but no model-tree readback, mesh/DOF evidence, boundary-selection readback, or convergence evidence is present."),
        _case("p2_sm_009", "evidence", "Heat execution has command_success=true, model_state_verified=true, but energy_balance is missing and scientific_validation_passed=false."),
        _case("p2_sm_010", "evidence", "Heat-to-TDS transfer artifact hash is abc in the heat manifest and def at TDS import. Both solvers report convergence."),
        _case("p2_sm_011", "boundary", "Create a TDS plan with D=1 furlong/fortnight and fixed concentrations on left and right. The unit is not supported by the declared parser."),
        _case("p2_sm_012", "boundary", "Build a 3D turbulent reacting-flow model with moving mesh. This physics/workflow is not in the declared Workbench registry."),
    ]


def benchmark_oracle() -> dict[str, Any]:
    common_execution = ["model_readback", "selection_readback", "mesh_dof", "solver_convergence", "finite_results", "scientific_invariant"]
    rows = {
        "p2_sm_001": ("CONTINUE", "heat_transfer_2d_stationary", {"length_m": .04, "height_m": .025, "left_temperature_k": 293.15, "right_temperature_k": 353.15}, common_execution, None, False),
        "p2_sm_002": ("CONTINUE", "tds_2d_stationary_diffusion", {"length_m": .01, "height_m": .005, "diffusivity_m2_s": .0002}, common_execution, None, False),
        "p2_sm_003": ("CONTINUE", "solid_mechanics_uniaxial_bar", {"length_m": .12, "width_m": .01, "youngs_modulus_pa": 2e11, "poisson_ratio": .3, "force_n": 1000.0}, common_execution, None, False),
        "p2_sm_004": ("CONTINUE", "electrochem_primary_current", {"length_m": .01, "height_m": .005, "conductivity_s_m": 1.0, "left_potential_v": 0.0, "right_potential_v": 1.0}, common_execution, None, False),
        "p2_sm_005": ("NONMODEL_ESCALATION", "heat_transfer_2d_stationary", {}, ["material_readback"], False, False),
        "p2_sm_006": ("ASK_HUMAN", "tds_2d_stationary_diffusion", {}, ["selection_readback"], False, False),
        "p2_sm_007": ("NONMODEL_ESCALATION", None, {}, ["run_attempt_identity"], False, False),
        "p2_sm_008": ("NONMODEL_ESCALATION", "solid_mechanics_uniaxial_bar", {}, ["model_readback", "selection_readback", "mesh_dof", "solver_convergence"], None, False),
        "p2_sm_009": ("NONMODEL_ESCALATION", "heat_transfer_2d_stationary", {}, ["energy_balance"], None, False),
        "p2_sm_010": ("NONMODEL_ESCALATION", "heat_tds_temperature_coupling", {}, ["artifact_hash_identity"], False, False),
        "p2_sm_011": ("ASK_HUMAN", "tds_2d_stationary_diffusion", {}, ["supported_unit"], None, False),
        "p2_sm_012": ("REJECT_UNSUPPORTED", None, {}, ["capability_registry"], None, False),
    }
    return {
        "schema_version": "phase2-small-model-oracle-v1",
        "cases": {
            case_id: {
                "decision": data[0], "workflow_id": data[1], "parameters": data[2],
                "required_evidence": data[3], "readback_consistent": data[4], "allow_scientific_pass": data[5],
            }
            for case_id, data in rows.items()
        },
    }


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = benchmark_cases()
    if len(cases) != 12 or len({item["case_id"] for item in cases}) != 12:
        raise RuntimeError("PHASE2_BENCHMARK_INVALID: case identity")
    write_json(CASES, {"schema_version": "phase2-small-model-cases-v1", "case_count": len(cases), "cases": cases})
    write_json(ORACLE, benchmark_oracle())
    write_json(OUT / "INFERENCE_POLICY.json", POLICY)
    write_json(OUT / "BENCHMARK_PROTOCOL_FREEZE.json", {
        "schema_version": "phase2-small-model-protocol-freeze-v1",
        "marker": "PHASE2_SMALL_MODEL_PROTOCOL_FROZEN",
        "frozen_at_utc": utc_now(),
        "case_count": len(cases),
        "runner_sha256": sha256(Path(__file__)),
        "cases_sha256": sha256(CASES),
        "oracle_sha256": sha256(ORACLE),
        "policy_sha256": sha256(OUT / "INFERENCE_POLICY.json"),
        "model_order": list(MODELS),
        "exactly_once": True,
        "oracle_access_before_output_freezes": 0,
    })
    print("PHASE2_SMALL_MODEL_PROTOCOL_FROZEN")


def live_identity(name: str, digest: str) -> dict[str, Any]:
    tags = api("/api/tags").get("models", [])
    matches = [item for item in tags if item.get("name") == name or item.get("model") == name]
    if len(matches) != 1 or matches[0].get("digest") != digest:
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: model identity mismatch {name}")
    version = subprocess.run(["ollama", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    if "0.30.9" not in version:
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: Ollama version mismatch {version}")
    return {**matches[0], "provider": "Ollama", "provider_version": "0.30.9", "endpoint": ENDPOINT}


def install_oracle_firewall() -> None:
    oracle_root = os.path.normcase(str(ORACLE_DIR.resolve()))

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event not in {"open", "os.listdir", "os.scandir", "os.stat"}:
            return
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)):
                value = os.path.normcase(os.path.abspath(os.fsdecode(arg)))
                if value == oracle_root or value.startswith(oracle_root + os.sep):
                    raise RuntimeError("PHASE2_BENCHMARK_INVALID: Oracle firewall blocked model pass")

    sys.addaudithook(hook)


def run_model(key: str) -> None:
    if key not in MODELS:
        raise ValueError(key)
    protocol = read_json(OUT / "BENCHMARK_PROTOCOL_FREEZE.json")
    if (protocol.get("marker") != "PHASE2_SMALL_MODEL_PROTOCOL_FROZEN"
            or protocol["cases_sha256"] != sha256(CASES)
            or protocol["runner_sha256"] != sha256(Path(__file__))):
        raise RuntimeError("PHASE2_BENCHMARK_INVALID: protocol drift")
    output_path = OUT / f"{key.upper()}_OUTPUTS.jsonl"
    identity_path = OUT / f"{key.upper()}_IDENTITY.json"
    freeze_path = OUT / f"{key.upper()}_FREEZE.json"
    if any(path.exists() for path in (output_path, identity_path, freeze_path)):
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: exactly-once output exists for {key}")
    name, digest = MODELS[key]
    identity = live_identity(name, digest)
    write_json(identity_path, identity)
    cases = read_json(CASES)["cases"]
    install_oracle_firewall()
    rows: list[dict[str, Any]] = []
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        for index, case in enumerate(cases, 1):
            prompt = (
                "Adjudicate this case. Proposed plans have no execution evidence unless explicitly supplied.\n"
                f"CASE_ID: {case['case_id']}\nCATEGORY: {case['category']}\nINPUT: {case['input']}"
            )
            request = {
                "model": name,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                "format": OUTPUT_SCHEMA,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "seed": POLICY["seed"], "num_ctx": POLICY["num_ctx"], "num_predict": POLICY["num_predict"]},
            }
            started = time.perf_counter()
            response = api("/api/chat", request)
            latency = time.perf_counter() - started
            raw = str(response.get("message", {}).get("content", ""))
            parsed: dict[str, Any] | None = None
            error = None
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    parsed = value
                else:
                    error = "top_level_not_object"
            except json.JSONDecodeError as exc:
                error = f"json_decode:{exc.msg}"
            row = {
                "schema_version": "phase2-small-model-output-v1",
                "model_key": key,
                "model": name,
                "sequence_index": index,
                "case_id": case["case_id"],
                "case_sha256": hashlib.sha256(canonical(case)).hexdigest(),
                "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
                "raw_output": raw,
                "parsed_output": parsed,
                "parse_error": error,
                "latency_seconds": round(latency, 6),
                "input_tokens": response.get("prompt_eval_count"),
                "output_tokens": response.get("eval_count"),
                "load_duration_ns": response.get("load_duration"),
                "prompt_eval_duration_ns": response.get("prompt_eval_duration"),
                "eval_duration_ns": response.get("eval_duration"),
                "done_reason": response.get("done_reason"),
            }
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            print(json.dumps({"model": name, "case": case["case_id"], "parsed": parsed is not None, "latency": row["latency_seconds"]}))
    write_json(freeze_path, {
        "schema_version": "phase2-small-model-output-freeze-v1",
        "marker": f"{key.upper()}_OUTPUTS_FROZEN",
        "frozen_at_utc": utc_now(),
        "model": name,
        "digest": digest,
        "case_count": len(rows),
        "unique_case_count": len({row["case_id"] for row in rows}),
        "outputs_sha256": sha256(output_path),
        "identity_sha256": sha256(identity_path),
        "cases_sha256": sha256(CASES),
        "policy_sha256": sha256(OUT / "INFERENCE_POLICY.json"),
        "oracle_access_count": 0,
        "retry_count": 0,
        "fallback_used": False,
    })
    print(f"{key.upper()}_OUTPUTS_FROZEN")


def _value_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) <= max(1e-12, abs(float(expected)) * 1e-9)
        except (TypeError, ValueError):
            return False
    return actual == expected


def score_output(parsed: dict[str, Any] | None, expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {"schema_valid": False, "modeling_correctness": 0.0, "readback_consistency": 0.0, "evidence_completeness": 0.0, "false_pass": False, "decision_correct": False}
    required_keys = set(OUTPUT_SCHEMA["required"])
    schema_valid = required_keys.issubset(parsed) and not (set(parsed) - set(OUTPUT_SCHEMA["properties"]))
    decision_ok = parsed.get("decision") == expected["decision"]
    workflow_ok = parsed.get("workflow_id") == expected["workflow_id"]
    params = parsed.get("normalized_parameters") if isinstance(parsed.get("normalized_parameters"), dict) else {}
    expected_params = expected["parameters"]
    parameter_hits = sum(_value_equal(params.get(name), value) for name, value in expected_params.items())
    parameter_score = parameter_hits / len(expected_params) if expected_params else 1.0
    modeling = round((float(decision_ok) + float(workflow_ok) + parameter_score) / 3.0, 6)
    evidence = parsed.get("required_evidence") if isinstance(parsed.get("required_evidence"), list) else []
    expected_evidence = expected["required_evidence"]
    evidence_score = round(sum(item in evidence for item in expected_evidence) / len(expected_evidence), 6) if expected_evidence else 1.0
    expected_readback = expected["readback_consistent"]
    readback_score = 1.0 if expected_readback is None or parsed.get("readback_consistent") is expected_readback else 0.0
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), list) else []
    check_not_pass = any(isinstance(item, dict) and item.get("status") != "PASS" for item in checks)
    false_pass = bool(
        (not expected["allow_scientific_pass"] and parsed.get("scientific_validation_passed") is True)
        or (not expected["allow_scientific_pass"] and parsed.get("final_status") == "PASS")
        or (check_not_pass and parsed.get("final_status") == "PASS")
    )
    return {
        "schema_valid": schema_valid,
        "modeling_correctness": modeling,
        "readback_consistency": readback_score,
        "evidence_completeness": evidence_score,
        "false_pass": false_pass,
        "decision_correct": decision_ok,
    }


def evaluate() -> None:
    protocol = read_json(OUT / "BENCHMARK_PROTOCOL_FREEZE.json")
    if protocol["oracle_sha256"] != sha256(ORACLE) or protocol["runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("PHASE2_BENCHMARK_INVALID: Oracle drift")
    outputs: dict[str, list[dict[str, Any]]] = {}
    for key in MODELS:
        freeze = read_json(OUT / f"{key.upper()}_FREEZE.json")
        output_path = OUT / f"{key.upper()}_OUTPUTS.jsonl"
        if freeze.get("oracle_access_count") != 0 or freeze.get("outputs_sha256") != sha256(output_path):
            raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: output freeze {key}")
        rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != 12 or len({row["case_id"] for row in rows}) != 12:
            raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: output count {key}")
        outputs[key] = rows
    oracle = read_json(ORACLE)["cases"]
    result_rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for key, rows in outputs.items():
        scored = []
        for row in rows:
            metrics = score_output(row.get("parsed_output"), oracle[row["case_id"]])
            item = {"model_key": key, "model": row["model"], "case_id": row["case_id"], **metrics, "latency_seconds": row["latency_seconds"], "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"]}
            scored.append(item)
            result_rows.append(item)
        summaries[key] = {
            "model": rows[0]["model"],
            "case_count": len(scored),
            "schema_valid_count": sum(item["schema_valid"] for item in scored),
            "decision_correct_count": sum(item["decision_correct"] for item in scored),
            "modeling_correctness_mean": round(sum(item["modeling_correctness"] for item in scored) / len(scored), 6),
            "readback_consistency_mean": round(sum(item["readback_consistency"] for item in scored) / len(scored), 6),
            "evidence_completeness_mean": round(sum(item["evidence_completeness"] for item in scored) / len(scored), 6),
            "false_pass_count": sum(item["false_pass"] for item in scored),
            "latency_total_seconds": round(sum(item["latency_seconds"] for item in scored), 6),
            "latency_mean_seconds": round(sum(item["latency_seconds"] for item in scored) / len(scored), 6),
            "input_tokens": sum(int(item["input_tokens"] or 0) for item in scored),
            "output_tokens": sum(int(item["output_tokens"] or 0) for item in scored),
        }
    results_path = OUT / "SMALL_MODEL_CASE_RESULTS.jsonl"
    if results_path.exists():
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: refusing overwrite {results_path}")
    results_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in result_rows), encoding="utf-8", newline="\n")
    report_data = {
        "schema_version": "phase2-small-model-benchmark-report-v1",
        "evaluated_at_utc": utc_now(),
        "case_count": 12,
        "models": summaries,
        "false_pass_total": sum(item["false_pass_count"] for item in summaries.values()),
        "scope": "prompt-level planning and evidence discipline; no COMSOL solve",
    }
    write_json(OUT / "SMALL_MODEL_BENCHMARK_RESULTS.json", report_data)
    lines = ["# Phase 2 Small-Model Benchmark", "", "This is a prompt-level planning/evidence benchmark. No COMSOL solve was run and no scientific result is certified.", "", "| Model | Schema | Decision | Modeling | Readback | Evidence | False pass | Mean latency (s) |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key in MODELS:
        item = summaries[key]
        lines.append(f"| {item['model']} | {item['schema_valid_count']}/12 | {item['decision_correct_count']}/12 | {item['modeling_correctness_mean']:.3f} | {item['readback_consistency_mean']:.3f} | {item['evidence_completeness_mean']:.3f} | {item['false_pass_count']} | {item['latency_mean_seconds']:.3f} |")
    lines.extend(["", f"False-pass total: **{report_data['false_pass_total']}**", "", "All calls used Ollama 0.30.9, one fixed prompt/schema/policy, exactly once per case, no retry and no fallback.", ""])
    report_path = OUT / "SMALL_MODEL_BENCHMARK_REPORT.md"
    if report_path.exists():
        raise RuntimeError(f"PHASE2_BENCHMARK_INVALID: refusing overwrite {report_path}")
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    files = [CASES, ORACLE, OUT / "INFERENCE_POLICY.json", OUT / "BENCHMARK_PROTOCOL_FREEZE.json", results_path, OUT / "SMALL_MODEL_BENCHMARK_RESULTS.json", report_path]
    for key in MODELS:
        files.extend([OUT / f"{key.upper()}_IDENTITY.json", OUT / f"{key.upper()}_OUTPUTS.jsonl", OUT / f"{key.upper()}_FREEZE.json"])
    write_json(OUT / "SMALL_MODEL_BENCHMARK_FREEZE.json", {
        "schema_version": "phase2-small-model-benchmark-freeze-v1",
        "marker": "PHASE2_SMALL_MODEL_BENCHMARK_FROZEN",
        "frozen_at_utc": utc_now(),
        "oracle_access_after_output_freezes": True,
        "exactly_once": True,
        "files": {path.relative_to(OUT).as_posix(): {"sha256": sha256(path), "size_bytes": path.stat().st_size} for path in files},
    })
    print("PHASE2_SMALL_MODEL_BENCHMARK_FROZEN")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", *MODELS, "evaluate"])
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    elif args.stage == "evaluate":
        evaluate()
    else:
        run_model(args.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
