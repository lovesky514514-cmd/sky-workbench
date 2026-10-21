"""Cross-physics quality gate for ScientificPlan 2.0."""

from __future__ import annotations

import math
import re
from app.scientific.quantity import find_quantity, valid_geometry
from typing import Any

from app.scientific.schemas import (
    ParameterStatus,
    PlanQualityCheck,
    PlanQualityReport,
    PlanQualityStatus,
    ScientificParameter,
    ScientificPlan,
)


REQUIRED_HEAT_PARAMETERS = ("width", "height", "left_temperature", "right_temperature")
REQUIRED_EXECUTION_PARAMETERS = (*REQUIRED_HEAT_PARAMETERS, "thermal_conductivity", "density", "heat_capacity")
SOURCE_BACKED_STATUSES = {
    ParameterStatus.BENCHMARK_DEFINED,
    ParameterStatus.VERIFIED,
    ParameterStatus.LITERATURE,
    ParameterStatus.COMSOL_DEFAULT,
    ParameterStatus.USER_PROVIDED,
}


def _numeric(parameter: ScientificParameter | None) -> float | None:
    if parameter is None or isinstance(parameter.value, bool):
        return None
    try:
        return float(parameter.value)
    except (TypeError, ValueError):
        return None


def _check(
    category: str,
    name: str,
    ok: bool,
    success: str,
    failure: str,
    *,
    warning: bool = False,
    observed: Any = None,
    expected: Any = None,
) -> PlanQualityCheck:
    return PlanQualityCheck(
        category=category,
        name=name,
        status="passed" if ok else ("warning" if warning else "failed"),
        message=success if ok else failure,
        observed=observed,
        expected=expected,
    )


class ScientificPlanQualityValidator:
    """Evaluate completeness, fidelity, provenance and execution readiness."""

    def validate(self, plan: ScientificPlan, *, enforce_prompt_fidelity: bool = True) -> PlanQualityReport:
        checks: list[PlanQualityCheck] = []
        blocking_missing = [item for item in plan.missing_information if not _nonblocking_missing(item)]
        advisory_missing = [item for item in plan.missing_information if _nonblocking_missing(item)]
        checks.extend(
            [
                _check("Physics", "physics", bool(plan.physics), "Physics is defined", "Governing physics is missing"),
                _check("Geometry", "geometry", bool(plan.geometry), "Geometry is defined", "Geometry is missing"),
                _check("Boundary Conditions", "boundaries", bool(plan.boundary_conditions), "Boundary conditions are defined", "Boundary conditions are missing"),
                _check("Study", "study", bool(plan.study), "Study type is defined", "Study type is missing"),
                _check("Outputs", "outputs", bool(plan.expected_outputs), "Expected outputs are defined", "Expected outputs are missing"),
                _check(
                    "Completeness",
                    "missing_information",
                    not blocking_missing,
                    "No blocking information is missing",
                    f"Missing information: {', '.join(blocking_missing)}",
                    warning=False,
                    observed=blocking_missing,
                ),
                _check("Completeness", "advisory_information", not advisory_missing, "No advisory information is missing", f"Advisory information: {', '.join(advisory_missing)}", warning=True, observed=advisory_missing),
            ]
        )

        if _is_heat_plan(plan):
            checks.extend(self._heat_checks(plan, enforce_prompt_fidelity=enforce_prompt_fidelity))
        elif _is_tds_plan(plan):
            checks.extend(self._tds_checks(plan))

        assumed = [
            name
            for name, item in plan.parameters.items()
            if item.assumption or item.status in {ParameterStatus.ASSUMPTION, ParameterStatus.UNKNOWN}
        ]
        checks.append(
            _check(
                "Sources",
                "parameter_provenance",
                not assumed,
                "Every parameter has an explicit source status",
                f"Assumed or unverified parameters require review: {', '.join(assumed)}",
                warning=True,
                observed=assumed,
            )
        )

        errors = [item.message for item in checks if item.status == "failed"]
        warnings = [item.message for item in checks if item.status == "warning"]
        status = (
            PlanQualityStatus.INVALID
            if errors
            else PlanQualityStatus.VALID_WITH_WARNINGS
            if warnings
            else PlanQualityStatus.VALID
        )
        return PlanQualityReport(status=status, checks=checks, errors=errors, warnings=warnings)

    def apply(self, plan: ScientificPlan, *, enforce_prompt_fidelity: bool = True) -> PlanQualityReport:
        report = self.validate(plan, enforce_prompt_fidelity=enforce_prompt_fidelity)
        plan.quality_report = report
        plan.quality_status = report.status
        return report

    def _heat_checks(self, plan: ScientificPlan, *, enforce_prompt_fidelity: bool) -> list[PlanQualityCheck]:
        checks: list[PlanQualityCheck] = []
        checks.extend(
            [
                _check("Physics", "heat_transfer_physics", any("heat transfer" in item.lower() or "conduction" in item.lower() or "传热" in item for item in plan.physics), "Heat-transfer physics is explicit", "Heat-transfer physics is not explicit"),
                _check("Materials", "material", bool(plan.materials), "A material is defined", "Material definition is missing"),
                _check("Boundary Conditions", "thermal_insulation", any("insulat" in item.lower() or "绝热" in item for item in plan.boundary_conditions), "Thermal insulation behavior is explicit", "Thermal insulation behavior is missing"),
                _check("Study", "stationary", any("stationary" in item.lower() or "steady" in item.lower() or "稳态" in item for item in plan.study), "Stationary study is explicit", "A stationary study is required for this workflow"),
                _check("Outputs", "temperature_output", any("temperature" in item.lower() or "温度" in item for item in plan.expected_outputs), "Temperature output is requested", "Temperature output is not explicit"),
            ]
        )
        missing_parameters = [name for name in REQUIRED_EXECUTION_PARAMETERS if name not in plan.parameters]
        checks.append(
            _check(
                "Parameters",
                "required_parameters",
                not missing_parameters,
                "Required geometry, temperature and material parameters are present",
                f"Required parameters are missing: {', '.join(missing_parameters)}",
                observed=missing_parameters,
                expected=list(REQUIRED_EXECUTION_PARAMETERS),
            )
        )

        width = _numeric(plan.parameters.get("width"))
        height = _numeric(plan.parameters.get("height"))
        dimensions_ok = width is not None and height is not None and width > 0 and height > 0
        checks.append(
            _check(
                "Geometry",
                "positive_dimensions",
                dimensions_ok,
                "Geometry dimensions are positive",
                "Width and height must be positive numeric values",
                observed={"width": width, "height": height},
            )
        )

        left = _numeric(plan.parameters.get("left_temperature"))
        right = _numeric(plan.parameters.get("right_temperature"))
        temperature_ok = left is not None and right is not None and left > 0 and right > 0
        checks.append(
            _check(
                "Units",
                "absolute_temperature",
                temperature_ok,
                "Boundary temperatures are valid absolute values",
                "Both boundary temperatures must be present and greater than 0 K",
                observed={"left_temperature": left, "right_temperature": right},
                expected="Kelvin > 0",
            )
        )

        units_ok = all(
            item.unit.strip() and item.unit.lower() not in {"c", "°c", "℃"}
            for name, item in plan.parameters.items()
            if name in REQUIRED_HEAT_PARAMETERS
        )
        checks.append(
            _check(
                "Units",
                "normalized_units",
                units_ok,
                "Executable parameters use explicit normalized units",
                "Executable parameters contain missing or non-normalized units",
            )
        )

        requested = _extract_requested_facts(plan.original_request or plan.research_goal) if enforce_prompt_fidelity else {}
        for name, expected in requested.items():
            actual = _numeric(plan.parameters.get(name))
            unit = plan.parameters.get(name).unit if plan.parameters.get(name) else None
            actual_si = _to_si(name, actual, unit)
            checks.append(
                _check(
                    "Fidelity",
                    f"prompt_{name}",
                    actual_si is not None and math.isclose(actual_si, expected, rel_tol=1e-6, abs_tol=1e-6),
                    f"{name} matches the user request",
                    f"{name} does not match the value in the user request",
                    observed=actual_si,
                    expected=expected,
                )
            )
        if enforce_prompt_fidelity:
            property_labels = {
                "thermal_conductivity": r'(?<![A-Za-z0-9_])k\s*=|thermal conductivity|热导率',
                "density": r'density|(?<![A-Za-z0-9_])rho\s*=|密度',
                "heat_capacity": r'heat capacity|specific heat|(?<![A-Za-z0-9_])Cp\s*=|比热容',
            }
            for name, labels in property_labels.items():
                stated = find_quantity(plan.original_request or plan.research_goal, labels, name, gap=12)
                if stated is None:
                    continue
                item = plan.parameters.get(name)
                actual = _numeric(item)
                checks.append(_check(
                    "Fidelity", f"prompt_{name}",
                    stated.issue is None and item is not None and actual is not None
                    and item.unit.replace('^3', '³') == stated.si_unit
                    and not item.assumption
                    and math.isclose(actual, stated.normalized_value, rel_tol=1e-12, abs_tol=0.0),
                    f"{name} matches the user request",
                    f"{name} does not match the user-stated physical property",
                    observed=actual, expected=stated.normalized_value,
                ))
        if enforce_prompt_fidelity:
            for name in REQUIRED_HEAT_PARAMETERS:
                if name in requested or name not in plan.parameters:
                    continue
                parameter = plan.parameters[name]
                checks.append(
                    _check(
                        "Fidelity",
                        f"unsupported_{name}",
                        False,
                        "",
                        f"{name} is present in the plan but no numeric value was supplied by the user",
                        observed={"value": parameter.value, "unit": parameter.unit, "source": parameter.source},
                        expected="Explicit user value",
                    )
                )
        return checks

    def _tds_checks(self, plan: ScientificPlan) -> list[PlanQualityCheck]:
        required = ("length", "height", "diffusion_coefficient", "left_concentration", "right_concentration")
        missing = [name for name in required if name not in plan.parameters]
        diffusion = _numeric(plan.parameters.get("diffusion_coefficient"))
        length = _numeric(plan.parameters.get("length"))
        height = _numeric(plan.parameters.get("height"))
        concentration_units = {
            plan.parameters[name].unit for name in ("left_concentration", "right_concentration") if name in plan.parameters
        }
        return [
            _check("Physics", "tds_interface", any("diluted species" in item.lower() or "稀物质" in item for item in plan.physics), "Transport of Diluted Species is explicit", "Transport of Diluted Species is missing"),
            _check("Parameters", "tds_required_parameters", not missing, "All TDS execution parameters are present", f"Required TDS parameters are missing: {', '.join(missing)}", observed=missing, expected=list(required)),
            _check("Geometry", "tds_positive_dimensions", length is not None and height is not None and length > 0 and height > 0, "TDS geometry dimensions are positive", "Length and height must be positive", observed={"length": length, "height": height}),
            _check("Parameters", "tds_positive_diffusion", diffusion is not None and diffusion > 0, "Diffusion coefficient is positive", "Diffusion coefficient must be greater than zero", observed=diffusion, expected="> 0 m^2/s"),
            _check("Units", "tds_length_units", all(plan.parameters[name].unit == "m" for name in ("length", "height") if name in plan.parameters) and not any(name not in plan.parameters for name in ("length", "height")), "Geometry units are normalized to metres", "Geometry units must be normalized to metres"),
            _check("Units", "tds_diffusion_unit", plan.parameters.get("diffusion_coefficient") is not None and plan.parameters["diffusion_coefficient"].unit in {"m^2/s", "m²/s"}, "Diffusion unit is explicit", "Diffusion unit must be m^2/s"),
            _check("Units", "tds_concentration_units", bool(concentration_units) and concentration_units.issubset({"mol/m^3", "mol/m³"}) and len(concentration_units) == 1, "Concentration units are explicit and consistent", "Concentration units must be mol/m^3", observed=sorted(concentration_units)),
            _check("Boundary Conditions", "tds_concentration_boundaries", sum("concentration" in item.lower() or "浓度" in item for item in plan.boundary_conditions) >= 2, "Left and right concentration boundaries are explicit", "Two concentration boundaries are required", observed=plan.boundary_conditions),
            _check("Boundary Conditions", "tds_no_flux", any("no flux" in item.lower() or "无通量" in item for item in plan.boundary_conditions), "Top/bottom No Flux is explicit", "Top/bottom No Flux is required", observed=plan.boundary_conditions),
            _check("Study", "tds_stationary", any("stationary" in item.lower() or "steady" in item.lower() or "稳态" in item for item in plan.study), "Stationary study is explicit", "A Stationary study is required", observed=plan.study),
            _check("Outputs", "tds_concentration_output", any("concentration" in item.lower() or "浓度" in item for item in plan.expected_outputs), "Concentration output is requested", "Concentration output is missing"),
            _check("Scope", "tds_pure_diffusion", not any(term in " ".join(plan.physics + plan.assumptions).lower() for term in ("convection enabled", "electric field", "nernst", "reaction enabled")), "Plan stays within pure diffusion", "Unsupported transport mechanisms are present"),
            _check("Sources", "tds_parameter_status", all(item.status in SOURCE_BACKED_STATUSES for item in plan.parameters.values()), "TDS parameter provenance is explicit", "TDS parameters include unverified provenance", observed={name: item.status for name, item in plan.parameters.items()}),
        ]


def _to_si(name: str, value: float | None, unit: str | None) -> float | None:
    if value is None or unit is None:
        return None
    normalized = unit.strip().lower().replace("°", "")
    if "temperature" in name:
        if normalized in {"k", "kelvin"}:
            return value
        if normalized in {"c", "℃", "celsius"}:
            return value + 273.15
        return None
    factors = {"m": 1.0, "cm": 0.01, "mm": 0.001}
    factor = factors.get(normalized)
    return value * factor if factor is not None else None


def _extract_requested_facts(prompt: str) -> dict[str, float]:
    facts: dict[str, float] = {}
    dimensions = valid_geometry(prompt)
    if dimensions:
        facts["width"] = dimensions[0].normalized_value
        facts["height"] = dimensions[1].normalized_value

    for side, names in {
        "left_temperature": ("左侧", "左边界", "左边", "left"),
        "right_temperature": ("右侧", "右边界", "右边", "right"),
    }.items():
        labels = "|".join(re.escape(item) for item in names)
        match = re.search(rf"(?:{labels})[^\d,，;；.-]{{0,40}}(-?\d+(?:\.\d+)?)\s*(K|°?C|℃)", prompt, re.I)
        if match is None:
            match = re.search(
                rf"(-?\d+(?:\.\d+)?)\s*(K|°?C|℃)[^\d,，;；.]{{0,30}}(?:{labels})",
                prompt,
                re.I,
            )
        if match:
            value = _to_si(side, float(match.group(1)), match.group(2))
            if value is not None:
                facts[side] = value
    return facts


plan_quality_validator = ScientificPlanQualityValidator()


def _is_heat_plan(plan: ScientificPlan) -> bool:
    haystack = " ".join([plan.research_goal, *plan.physics]).lower()
    return plan.workflow_id == "heat_transfer_2d_stationary" or (plan.workflow_id is None and any(term in haystack for term in ("heat transfer", "heat conduction", "热传导", "温度分布", "温度场")))


def _is_tds_plan(plan: ScientificPlan) -> bool:
    haystack = " ".join([plan.research_goal, *plan.physics]).lower()
    return plan.workflow_id == "tds_2d_stationary_diffusion" or (plan.workflow_id is None and any(term in haystack for term in ("diluted species", "稀物质传递", "溶质扩散")))


def _nonblocking_missing(value: str) -> bool:
    lower = value.lower()
    return any(term in lower for term in ("mesh", "网格", "validation", "验证", "alloy", "合金", "热导率", "conductivity", "source", "文献"))
