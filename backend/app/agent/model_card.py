"""Model Card 2.0 with AI, plan, provenance, validation and reproduction facts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import WORKSPACE_ROOT
from app.validators.execution_verdict import ExecutionVerdict


def generate_model_card(
    *,
    run_id: str,
    session_id: str,
    plan: dict[str, Any],
    execution: dict[str, Any],
    validation_report: dict[str, Any],
    repair_history: list[dict[str, Any]],
    verdict: ExecutionVerdict | None = None,
    recovery: bool = False,
) -> Path:
    # Legacy callers without a verdict cannot certify an execution.
    verdict = verdict or ExecutionVerdict(run_id, str(plan.get('workflow_id', '')), False, False, False, ('execution_verdict_missing',))
    if verdict.run_id != run_id or verdict.workflow_id != str(plan.get('workflow_id', '')):
        raise ValueError('Model Card verdict identity mismatch')
    execution = {**execution, **verdict.fields()}
    validation_report = {**validation_report, **verdict.fields(), 'passed': verdict.completed}
    report_dir = WORKSPACE_ROOT / "reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / ("recovery_card.md" if recovery else "model_card.md")
    parameters = plan.get("parameters", {})
    parameter_rows = "\n".join(
        "| {name} | {symbol} | {value} | {unit} | {status} | {source} | {page} | {chunk} | {confidence} | {assumption} | {verified} |".format(
            name=name,
            symbol=value.get("symbol", ""),
            value=value.get("value"),
            unit=value.get("unit", ""),
            status=value.get("status", "Unknown"),
            source=str(value.get("source", "unspecified")).replace("|", "/"),
            page=value.get("source_page") or "—",
            chunk=value.get("source_chunk") or "—",
            confidence=value.get("confidence") if value.get("confidence") is not None else "—",
            assumption="yes" if value.get("assumption") else "no",
            verified="yes" if value.get("verified") else "no",
        )
        for name, value in parameters.items()
    ) or "| — | — | — | — | Unknown | — | — | — | — | — | — |"
    sources = "\n".join(format_source(source) for source in plan.get("sources", [])) or "- No citable source was attached."
    checks = validation_report.get("checks", [])
    check_rows = "\n".join(
        f"| {check.get('category', '')} | {check.get('name', '')} | {check.get('status', '')} | {str(check.get('observed', '')).replace('|', '/')} | {str(check.get('expected', '')).replace('|', '/')} | {check.get('message', '')} |"
        for check in checks
    ) or "| — | — | — | — | — | No checks recorded |"
    repairs = "\n".join(
        f"- Attempt {item.get('attempt')}: {item.get('outcome')} — {item.get('reason')} ({item.get('time')})"
        for item in repair_history
    ) or "- No automatic repair was required."
    model = execution.get("model_state", {})
    artifacts = execution.get("artifacts", {})
    validation = execution.get("validation", {})
    performance = execution.get("performance", {})
    bridge = execution.get("java_bridge", {})
    resource = execution.get("resource", {})
    stage_b = resource.get("stage_b", {})
    risk_level = str(stage_b.get("risk_level", "not recorded"))
    resource_precheck = "USER_OVERRIDE" if stage_b.get("override_present") else ("PASS" if stage_b.get("allowed") else risk_level)
    result_metrics = format_result_metrics(validation)
    performance_rows = "\n".join(f"| {name} | {value} |" for name, value in performance.items()) or "| — | — |"
    content = f"""# COMSOL Scientific Model Card 2.0

## Identity

| Field | Value |
|---|---|
| AI Provider | {plan.get('ai_provider', 'none')} |
| AI Model | {plan.get('ai_model', 'none')} |
| Plan ID | `{plan.get('plan_id', 'unknown')}` |
| Session ID | `{session_id}` |
| COMSOL Version | {execution.get('comsol_version', 'unknown')} |
| Run ID | `{run_id}` |
| Attempt ID | `{execution.get('attempt_id', 'not recorded')}` |
| Run Outcome | {execution.get('run_lifecycle', {}).get('outcome', 'not recorded')} |
| Cancellation Requested | {execution.get('run_lifecycle', {}).get('cancel_requested', False)} |
| Command Success | {str(bool(execution.get('command_success'))).lower()} |
| Model State Verified | {str(bool(execution.get('model_state_verified'))).lower()} |
| Scientific Validation Success | {str(bool(execution.get('scientific_validation_success'))).lower()} |
| Evidence Contract Passed | {str(bool(execution.get('evidence_contract_passed'))).lower()} |

## Execution Verdict

```json
{json.dumps(verdict.as_dict(), ensure_ascii=False, sort_keys=True)}
```

## Evidence Contract

```json
{json.dumps(execution.get('evidence_contract', {}), ensure_ascii=False, sort_keys=True)}
```

## Research Goal

{plan.get('research_goal', '')}

## Scientific Plan

- Physics: {', '.join(plan.get('physics', [])) or 'None recorded'}
- Geometry: {', '.join(plan.get('geometry', [])) or 'None recorded'}
- Materials: {', '.join(plan.get('materials', [])) or 'None recorded'}
- Boundary conditions: {', '.join(plan.get('boundary_conditions', [])) or 'None recorded'}
- Mesh strategy: {', '.join(plan.get('mesh_strategy', [])) or 'None recorded'}
- Study: {', '.join(plan.get('study', [])) or 'None recorded'}
- Solver: {', '.join(plan.get('solver', [])) or 'None recorded'}

## Assumptions and Missing Information

{bullet(plan.get('assumptions', []))}

Missing information:

{bullet(plan.get('missing_information', []))}

## Parameter Provenance

| Name | Symbol | Value | Unit | Status | Source | Page | Chunk | Confidence | Assumption | Verified |
|---|---|---:|---|---|---|---:|---|---:|---|---|
{parameter_rows}

## Sources

{sources}

## Model State Read Back by the Agent

- Geometry: {', '.join(model.get('geometry', [])) or 'None recorded'}
- Materials: {', '.join(model.get('materials', [])) or 'None recorded'}
- Physics: {', '.join(model.get('physics', [])) or 'None recorded'}
- Boundary conditions: {', '.join(model.get('boundary_conditions', [])) or 'None recorded'}
- Selections: {', '.join(model.get('selections', [])) or 'None recorded'}
- Mesh: {', '.join(model.get('mesh', [])) or 'None recorded'}
- Study: {', '.join(model.get('study', [])) or 'None recorded'}
- Solver: {', '.join(model.get('solver', [])) or 'None recorded'}
- Results: {', '.join(model.get('results', [])) or 'None recorded'}
- Coupling dependency: {model.get('coupling', 'Not requested for this workflow')}
- Battery definition: {plan.get('battery_cell', 'Not requested for this workflow')}

## Validation Checks

| Category | Check | Status | Observed | Expected | Message |
|---|---|---|---|---|---|
{check_rows}

## Validation Result

- Scope: {validation_report.get('scope', 'unknown')}
- Passed: {validation_report.get('passed', False)}
- Command success: {validation_report.get('command_success', False)}
- Model state verified: {validation_report.get('model_state_verified', False)}
- Scientific validation success: {validation_report.get('scientific_validation_success', False)}
- Evidence contract passed: {validation_report.get('evidence_contract_passed', False)}
{result_metrics}

## Java Bridge Saved-Model Verification

- Real COMSOL: {bridge.get('health', {}).get('real_comsol', 'not requested for this workflow')}
- Model connected: {bridge.get('health', {}).get('model_connected', 'not requested for this workflow')}
- Readback checks: {bridge.get('checks', 'not requested for this workflow')}

## Hardware Resource Guard

- Resource Precheck: {resource_precheck}
- Stage B risk: {risk_level}
- Resource samples: {resource.get('sample_count', 0)}
- Peak tracked worker memory: {resource.get('peak_process_memory_gb', 'unavailable')} GB
- Estimated DOF: {resource.get('stage_b', {}).get('estimate', {}).get('estimated_dof', 'unavailable')}
- Mesh elements: {resource.get('stage_b', {}).get('estimate', {}).get('mesh_elements', 'unavailable')}
- Risk reasons: {resource.get('stage_b', {}).get('reasons', [])}

## Performance

| Phase | Milliseconds |
|---|---:|
{performance_rows}

## Repair History

{repairs}

## Artifacts

{bullet([f'{name}: `{value}`' for name, value in artifacts.items()])}

## Reproduction Steps

1. Start the Workbench with `START_WORKBENCH.bat`.
2. Restore session `{session_id}` and review ScientificPlan `{plan.get('plan_id', 'unknown')}`.
3. Confirm assumption-marked parameters or replace them with traceable values.
4. Execute RUN; the runtime consumes the saved ScientificPlan rather than reparsing the chat prompt.
5. Start a fresh COMSOL/MPh process, resolve physical boundaries from coordinates, build and solve the stationary model.
6. Require every validation check above to pass before reporting completion.
7. Compare the raw log at `{execution.get('log_path', 'not recorded')}` with the durable Timeline in `workspace/sessions.db`.
"""
    if recovery:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def format_source(source: dict[str, Any]) -> str:
    page = source.get("page") or "n/a"
    section = source.get("section") or "n/a"
    doi = f"; DOI {source.get('doi')}" if source.get("doi") else ""
    return f"- **{source.get('title', source.get('file_name', 'Untitled'))}** — `{source.get('file', '')}`; page {page}; section {section}; chunk `{source.get('chunk_id', source.get('chunk', ''))}`{doi}"


def bullet(values: list[Any]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- None recorded."


def format_result_metrics(validation: dict[str, Any]) -> str:
    if "voltage_initial_V" in validation:
        return "\n".join(
            [
                f"- Node / element / DOF count: {validation.get('node_count', 'unknown')} / {validation.get('element_count', 'unknown')} / {validation.get('dof', 'unknown')}",
                f"- Cell voltage: {validation.get('voltage_initial_V')} → {validation.get('voltage_final_V')} V; change {validation.get('voltage_change_V')} V",
                f"- Electrolyte concentration: {validation.get('concentration_min_mol_per_m3')} to {validation.get('concentration_max_mol_per_m3')} mol/m^3",
                f"- Applied / observed current density: {validation.get('applied_current_density_A_per_m2')} / {validation.get('current_density_mean_A_per_m2')} A/m^2; relative error {validation.get('current_relative_error')}",
                f"- Negative / positive SOC change: {validation.get('negative_soc_change')} / {validation.get('positive_soc_change')}",
            ]
        )
    if "coupling_on_flux_mol_per_m2_s" in validation:
        return "\n".join(
            [
                f"- Node / element / DOF count: {validation.get('node_count', 'unknown')} / {validation.get('element_count', 'unknown')} / {validation.get('dof', 'unknown')}",
                f"- Temperature range / analytical RMSE: {validation.get('temperature_min_K')} to {validation.get('temperature_max_K')} K / {validation.get('temperature_analytical_rmse_K')} K",
                f"- Variable-D concentration analytical RMSE: {validation.get('variable_diffusion_analytical_rmse_mol_per_m3')} mol/m^3",
                f"- Coupling OFF / ON flux: {validation.get('coupling_off_flux_mol_per_m2_s')} / {validation.get('coupling_on_flux_mol_per_m2_s')} mol/(m^2*s)",
                f"- OFF / ON flux ratio: {validation.get('off_on_flux_ratio')}; ON relative error {validation.get('on_flux_relative_error')}",
                f"- Mass-balance relative error: {validation.get('mass_balance_relative_error')}",
            ]
        )
    if "right_displacement_m" in validation:
        return "\n".join(
            [
                f"- Node / element count: {validation.get('node_count', 'unknown')} / {validation.get('element_count', 'unknown')}",
                f"- Right displacement: {validation.get('right_displacement_m')} m; expected {validation.get('expected_displacement_m')} m; relative error {validation.get('displacement_relative_error')}",
                f"- Axial / von Mises stress: {validation.get('axial_stress_Pa')} / {validation.get('von_mises_stress_Pa')} Pa",
                f"- Internal / expected force: {validation.get('internal_force_N')} / {validation.get('expected_force_N')} N; equilibrium relative error {validation.get('force_equilibrium_relative_error')}",
            ]
        )
    if "potential_min_V" in validation:
        return "\n".join(
            [
                f"- Node / element count: {validation.get('node_count', 'unknown')} / {validation.get('element_count', 'unknown')}",
                f"- Potential range: {validation.get('potential_min_V')} to {validation.get('potential_max_V')} V",
                f"- Analytical potential RMSE: {validation.get('potential_rmse_V')} V",
                f"- Current density: {validation.get('current_density_mean_A_per_m2')} A/m^2; expected {validation.get('expected_current_density_A_per_m2')} A/m^2; relative error {validation.get('current_density_relative_error')}",
            ]
        )
    if "concentration_min_mol_per_m3" in validation:
        return "\n".join(
            [
                f"- Node count: {validation.get('node_count', 'unknown')}",
                f"- Element count: {validation.get('element_count', 'unknown')}",
                f"- Concentration range: {validation.get('concentration_min_mol_per_m3')} to {validation.get('concentration_max_mol_per_m3')} mol/m^3",
                f"- Left/right boundary medians: {validation.get('left_boundary_median_mol_per_m3')} / {validation.get('right_boundary_median_mol_per_m3')} mol/m^3",
                f"- Analytical RMSE / MAE / max error: {validation.get('analytical_rmse_mol_per_m3')} / {validation.get('analytical_mae_mol_per_m3')} / {validation.get('analytical_max_abs_error_mol_per_m3')} mol/m^3",
                f"- Flux: {validation.get('flux_mean_mol_per_m2_s')} mol/(m^2*s); expected {validation.get('flux_expected_mol_per_m2_s')}; relative error {validation.get('flux_relative_error')}",
                f"- Mass-balance relative error: {validation.get('mass_balance_relative_error')}",
                f"- Maximum cross-y deviation: {validation.get('maximum_y_spread_mol_per_m3')} mol/m^3",
            ]
        )
    return "\n".join(
        [
            f"- Node count: {validation.get('node_count', 'unknown')}",
            f"- Temperature range: {validation.get('temperature_min_K', 'unknown')} to {validation.get('temperature_max_K', 'unknown')} K",
            f"- Left boundary median: {validation.get('left_boundary_median_K', 'unknown')} K",
            f"- Right boundary median: {validation.get('right_boundary_median_K', 'unknown')} K",
            f"- Analytical RMSE: {validation.get('analytical_rmse_K', 'unknown')} K",
        ]
    )
