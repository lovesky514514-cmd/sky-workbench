"""Deterministic shadow Judge overlay; never an execution authority."""

from __future__ import annotations

from typing import Any


def _gate(status: str, reason: str, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "evidence_refs": list(evidence_refs or [])}


class ShadowScientificJudge:
    version = "shadow-judge-v0.1"

    def evaluate(
        self,
        spec: dict[str, Any],
        *,
        raw_observation: dict[str, Any],
        authoritative_verdict: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        core = spec.get("core") if isinstance(spec.get("core"), dict) else {}
        profile = spec.get("domain_profile") if isinstance(spec.get("domain_profile"), dict) else {}
        run = spec.get("run_instance") if isinstance(spec.get("run_instance"), dict) else {}
        case_id = str(spec.get("case_id", ""))
        run_id = str(run.get("run_id", "unexecuted"))
        attempt_id = str(run.get("attempt_id", "unexecuted"))

        missing = [name for name in ("domain", "governing_model", "boundary_conditions", "observables", "tolerances") if not core.get(name)]
        j1 = _gate("PASS" if not missing else "FAIL", "Specification fields present" if not missing else "Missing: " + ", ".join(missing))
        boundaries = core.get("boundary_conditions") or []
        selection_risk = bool(profile.get("selection_sensitive"))
        selection_evidence = raw_observation.get("selections")
        if not boundaries:
            j2 = _gate("FAIL", "Required boundary conditions are absent")
        elif selection_risk and not selection_evidence:
            j2 = _gate("UNVERIFIABLE", "Selection-sensitive case has no current entity readback")
        else:
            j2 = _gate("PASS", "No incompatible selection evidence observed")

        solver = raw_observation.get("solver") if isinstance(raw_observation.get("solver"), dict) else {}
        if not solver:
            j3 = _gate("UNVERIFIABLE", "No numerical execution evidence")
        elif solver.get("state") == "converged":
            j3 = _gate("PASS", "Solver reports convergence; this does not imply physical success")
        elif solver.get("state") in {"not_run", "unavailable"}:
            j3 = _gate("UNVERIFIABLE", "Solver was not run")
        else:
            j3 = _gate("FAIL", "Solver did not establish numerical completion")

        supported = profile.get("supported") is True and core.get("governing_model", {}).get("supported") is not False
        j4 = _gate("PASS" if supported else "FAIL", "Workflow is inside declared capability" if supported else "Requested workflow is outside declared capability")
        resource = raw_observation.get("resource_guard")
        j5 = _gate("PASS", "Resource Guard evidence present") if isinstance(resource, dict) and resource.get("status") == "pass" else _gate("UNVERIFIABLE", "No execution-time Resource Guard evidence")

        post: list[dict[str, Any]] = []
        checks = raw_observation.get("checks") if isinstance(raw_observation.get("checks"), dict) else {}
        failed = sorted(name for name, value in checks.items() if value is False)
        if failed:
            post.append(_gate("FLAG", "Failed declared invariant(s): " + ", ".join(failed)))
        elif authoritative_verdict and authoritative_verdict.get("completed") is True:
            post.append(_gate("PASS", "Authoritative verdict completed with referenced deterministic evidence"))
        else:
            post.append(_gate("UNVERIFIABLE", "No authoritative completed verdict for post-execution certification"))

        risk = str(profile.get("boundary_risk", "UNKNOWN"))
        if risk not in {"LOW", "ELEVATED", "HIGH", "UNKNOWN"}:
            risk = "UNKNOWN"
        report = {
            "case_id": case_id, "run_id": run_id, "attempt_id": attempt_id,
            "judge_mode": "shadow", "judge_can_block": False,
            "pre_gates": {"J1_spec_integrity": j1, "J2_boundary_selection": j2,
                          "J3_numerical_admissibility": j3, "J4_capability": j4,
                          "J5_resource_feasibility": j5},
            "post_audit": post, "boundary_risk": risk,
            "boundary_risk_reasons": list(profile.get("boundary_risk_reasons") or []),
            "evidence_refs": [], "authoritative_verdict_unchanged": authoritative_verdict,
        }
        all_gates = [*report["pre_gates"].values(), *post]
        if any(item["status"] in {"FAIL", "FLAG"} for item in all_gates):
            certificate_status = "FLAGGED_FOR_REVIEW"
        elif authoritative_verdict and authoritative_verdict.get("completed") is True and all(item["status"] in {"PASS", "NOT_APPLICABLE"} for item in all_gates):
            certificate_status = "VERIFIED_WITHIN_DECLARED_CHECKS"
        else:
            certificate_status = "UNVERIFIABLE"
        certificate = {
            "case_id": case_id, "run_id": run_id, "attempt_id": attempt_id, "status": certificate_status,
            "declared_observables": list(core.get("observables") or []),
            "declared_tolerances": list(core.get("tolerances") or []),
            "evidence_refs": [],
            "limitations": ["Shadow Judge cannot overwrite ExecutionVerdict", "No computable certified error bound is implemented"],
        }
        return report, certificate
