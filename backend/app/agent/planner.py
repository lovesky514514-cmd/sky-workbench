"""Provider-assisted planning with explicit schema repair and no silent AI fallback."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from app.providers.manager import ProviderManager, provider_manager
from app.scientific.schemas import Citation, ParameterStatus, ScientificParameter, ScientificPlan
from app.validators.plan_quality_validator import plan_quality_validator
from app.scientific.quantity import find_quantity, valid_geometry


HEAT_TERMS = (
    "热传导", "传热", "温度场", "温度分布", "绝热", "heat conduction",
    "heat transfer", "heat-transfer", "temperature field", "temperature distribution", "thermal insulation",
)
TDS_TERMS = (
    "transport of diluted species", "diluted species", "tds", "稀物质传递",
    "稀物质扩散", "溶质扩散", "solute diffusion", "species diffusion", "pure diffusion model",
)
SOLID_TERMS = ("solid mechanics", "uniaxial", "tensile bar", "固体力学", "单轴拉伸", "拉伸杆")
ELECTROCHEM_TERMS = ("primary current distribution", "electrochemistry", "一次电流分布", "电化学")
BATTERY_TERMS = ("battery", "电池", "锂离子", "锌离子", "lithium-ion", "lithium ion", "zinc-ion", "zinc ion")
COUPLING_TERMS = ("heat to tds", "heat → tds", "temperature-dependent diffusion", "temperature dependent diffusion", "d(t)", "温度依赖扩散", "传热到稀物质", "多物理场耦合")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"


class PlanGenerationFailure(RuntimeError):
    """A provider answered, but did not produce a valid plan after one repair."""


def parameter(
    name: str,
    symbol: str,
    value: float,
    unit: str,
    source: str,
    *,
    assumption: bool = False,
    original_value: float | None = None,
    original_unit: str | None = None,
    status: ParameterStatus | None = None,
) -> ScientificParameter:
    resolved_status = status or (ParameterStatus.ASSUMPTION if assumption else ParameterStatus.USER_PROVIDED)
    return ScientificParameter(
        name=name,
        symbol=symbol,
        value=value,
        unit=unit,
        source=source,
        confidence=0.99 if not assumption else 0.55,
        assumption=assumption,
        verified=not assumption,
        status=resolved_status,
        original_value=original_value,
        original_unit=original_unit,
        normalized_value=value,
        normalized_unit=unit,
    )


def _geometry_parameter(name, symbol, quantity, *, native=False, source="user prompt", status=None):
    result = parameter(name, symbol,
                       quantity.original_value if native else quantity.normalized_value,
                       quantity.original_unit if native else quantity.si_unit, source,
                       original_value=quantity.original_value, original_unit=quantity.original_unit,
                       status=status)
    result.normalized_value = quantity.normalized_value
    result.normalized_unit = quantity.si_unit
    result.quantity = asdict(quantity)
    return result


def _length_to_m(value: float, unit: str) -> float:
    return value * {"m": 1.0, "cm": 0.01, "mm": 0.001}[unit.lower()]


def _temperature_to_kelvin(value: float, unit: str) -> float:
    return value + 273.15 if unit.lower().replace("°", "") in {"c", "℃"} else value


def _temperature_for_side(prompt: str, side: str) -> tuple[float, str] | None:
    labels = {
        "left": ("左侧", "左边界", "左边", "left boundary", "left"),
        "right": ("右侧", "右边界", "右边", "right boundary", "right"),
    }[side]
    pattern = "|".join(re.escape(item) for item in labels)
    quantity = find_quantity(prompt, pattern, 'temperature', gap=50, allow_before=True)
    if quantity is not None:
        return (quantity.normalized_value, 'K') if quantity.issue is None else None
    return None


def extract_heat_transfer_plan(prompt: str) -> dict[str, Any] | None:
    """Parse only facts present in the prompt; never synthesize missing dimensions or temperatures."""

    lower = prompt.lower()
    if not any(term in lower for term in HEAT_TERMS):
        return None

    dimensions = valid_geometry(prompt)
    left = _temperature_for_side(prompt, "left")
    right = _temperature_for_side(prompt, "right")
    aluminum = any(term in lower for term in ("铝", "aluminum", "aluminium"))
    insulated = any(term in lower for term in ("绝热", "不散热", "insulated", "insulate", "adiabatic", "no-flux", "no flux", "thermal insulation"))
    steady = any(term in lower for term in ("稳态", "steady", "stationary"))
    two_dimensional = any(term in lower for term in ("二维", "2d", "2-d")) or dimensions is not None or ("薄" in prompt and "板" in prompt)

    parameters: dict[str, ScientificParameter] = {}
    geometry: list[str] = []
    missing: list[str] = []
    if dimensions:
        native = dimensions[0].original_unit == dimensions[1].original_unit
        for name, symbol, quantity in zip(("width", "height"), ("W", "H"), dimensions):
            parameters[name] = _geometry_parameter(name, symbol, quantity, native=native)
        width, height = parameters["width"].value, parameters["height"].value
        unit = parameters["width"].unit
        geometry.append(f"2D rectangle: {width:g} {unit} × {height:g} {unit}")
    else:
        missing.extend(["Complete 2D geometry width", "Complete 2D geometry height"])

    boundary_conditions: list[str] = []
    for side, parsed in (("left", left), ("right", right)):
        parameter_name = f"{side}_temperature"
        if parsed:
            raw_value, raw_unit = parsed
            kelvin = _temperature_to_kelvin(raw_value, raw_unit)
            parameters[parameter_name] = parameter(
                parameter_name,
                "T_left" if side == "left" else "T_right",
                kelvin,
                "K",
                "user prompt",
                original_value=raw_value,
                original_unit=raw_unit,
            )
            labels = r'左侧|左边界|左边|\bleft\b' if side == 'left' else r'右侧|右边界|右边|\bright\b'
            quantity = find_quantity(prompt, labels, 'temperature', gap=50, allow_before=True)
            if quantity and not quantity.issue:
                parameters[parameter_name].original_value = quantity.original_value
                parameters[parameter_name].original_unit = quantity.original_unit
                parameters[parameter_name].quantity = asdict(quantity)
            boundary_conditions.append(f"{side.title()} boundary: prescribed temperature {kelvin:g} K")
        else:
            missing.append(f"Numeric {side} boundary temperature")
    if insulated:
        boundary_conditions.append("Top and bottom boundaries: thermal insulation")

    complete_benchmark = dimensions is not None and left is not None and right is not None
    assumptions: list[str] = []
    warnings: list[str] = []
    property_specs = {
        "thermal_conductivity": (r'(?<![A-Za-z0-9_])k\s*=|thermal conductivity|热导率', 'k', 'W/(m*K)', 238.0),
        "density": (r'density|(?<![A-Za-z0-9_])rho\s*=|密度', 'rho', 'kg/m^3', 2700.0),
        "heat_capacity": (r'heat capacity|specific heat|(?<![A-Za-z0-9_])Cp\s*=|比热容', 'Cp', 'J/(kg*K)', 900.0),
    }
    for name, (labels, symbol, unit, benchmark_value) in property_specs.items():
        explicit = find_quantity(prompt, labels, name, gap=12)
        if explicit is not None:
            if explicit.issue or explicit.normalized_value is None or explicit.normalized_value <= 0:
                missing.append(f"Valid {name} with supported unit")
                continue
            item = parameter(name, symbol, explicit.normalized_value, unit, "user prompt",
                             original_value=explicit.original_value, original_unit=explicit.original_unit)
            item.quantity = asdict(explicit)
            parameters[name] = item
        elif aluminum and complete_benchmark:
            parameters[name] = parameter(name, symbol, benchmark_value, unit,
                "Workbench aluminum benchmark assumption; alloy-specific source required", assumption=True)
            warnings.append(f"{name} uses an aluminum benchmark assumption; alloy-specific source required")
        else:
            missing.append(f"Material {name} with supported unit")
    materials = (["Aluminum with explicit property provenance"] if aluminum else
                 ["User-defined thermal material with explicit properties"] if all(name in parameters for name in property_specs) else [])
    if not materials:
        missing.append("Material definition")
    if aluminum:
        assumptions.extend(["Constant isotropic aluminum properties", "No internal heat source"])
    else:
        assumptions.append("Constant isotropic user-defined thermal properties")

    if not insulated:
        missing.append("Top and bottom thermal boundary conditions")
    if not steady:
        missing.append("Study type (steady-state or transient)")
    if not two_dimensional:
        missing.append("Geometry dimensionality")

    plan = ScientificPlan(
        workflow_id="heat_transfer_2d_stationary",
        title="2D steady-state heat conduction",
        research_goal=prompt.strip(),
        original_request=prompt.strip(),
        physics=["Heat Transfer in Solids", "Steady-state conduction"] if steady else ["Heat Transfer in Solids"],
        geometry=geometry,
        materials=materials,
        parameters=parameters,
        boundary_conditions=boundary_conditions,
        mesh_strategy=["Physics-controlled 2D mesh", "Record solved node count"] if complete_benchmark else [],
        study=["Stationary study"] if steady else [],
        solver=["COMSOL stationary solver", "Require convergence confirmation"] if steady else [],
        validation=[
            "Verify physical edge mapping from coordinate-resolved temperatures",
            "Verify left and right boundary temperatures within 1 K",
            "Verify the global temperature range remains inside imposed bounds",
            "For the benchmark assumptions, require analytical RMSE ≤ 1 K",
        ] if complete_benchmark else ["Complete missing scientific inputs before defining validation tolerances"],
        expected_outputs=["COMSOL MPH model", "Temperature-field PNG", "Model Card 2.0", "Raw execution log"] if complete_benchmark else (["Steady-state temperature distribution"] if "温度" in prompt or "temperature" in lower else []),
        assumptions=assumptions,
        missing_information=missing,
        steps=[
            {"phase": "Research", "label": "Retrieve traceable local scientific evidence", "purpose": "Ground the plan and parameter provenance"},
            {"phase": "Precheck", "label": "Validate schema, units, geometry and boundaries", "purpose": "Block incomplete plans before COMSOL"},
            {"phase": "Build", "label": "Create geometry, material and Heat Transfer physics", "purpose": "Construct the explicit model tree"},
            {"phase": "Build Validation", "label": "Verify assigned model state", "purpose": "Detect silent setup failure"},
            {"phase": "Solve", "label": "Run the stationary study", "purpose": "Obtain the numerical temperature field"},
            {"phase": "Solution Validation", "label": "Check boundaries, range and analytical error", "purpose": "Separate command success from scientific success"},
            {"phase": "Export", "label": "Save model, image, log and Model Card", "purpose": "Create reproducible artifacts"},
        ],
        warnings=warnings,
        runnable=bool(complete_benchmark and materials and all(name in parameters for name in property_specs)
                      and insulated and steady and two_dimensional),
    )
    return plan.model_dump(mode="json")


def _normalize_concentration_unit(unit: str) -> str:
    return "mol/m^3" if "mol" in unit.lower() else unit


def _concentration_for_side(prompt: str, side: str) -> tuple[float, str] | None:
    labels = {
        "left": ("左侧", "左边界", "左边", "left boundary", "left"),
        "right": ("右侧", "右边界", "右边", "right boundary", "right"),
    }[side]
    names = "|".join(re.escape(item) for item in labels)
    quantity = find_quantity(prompt, names, 'concentration', gap=50, allow_before=True)
    if quantity is not None:
        return (quantity.normalized_value, 'mol/m^3') if quantity.issue is None else None
    return None


def _tds_dimensions(prompt: str) -> tuple[float, str, float, str] | None:
    dimensions = valid_geometry(prompt)
    if dimensions:
        length, height = dimensions
        return length.original_value, length.original_unit, height.original_value, height.original_unit
    return None


def _diffusion_coefficient(prompt: str) -> tuple[float, str] | None:
    quantity = find_quantity(prompt, r'(?<![A-Za-z0-9_])D(?![A-Za-z0-9_(])|diffusion coefficient|扩散系数', 'diffusion')
    return (quantity.normalized_value, 'm^2/s') if quantity and quantity.issue is None else None


def extract_tds_plan(prompt: str) -> dict[str, Any] | None:
    """Extract a general pure-diffusion TDS plan without substituting benchmark values."""

    lower = prompt.lower()
    if not any(term in lower for term in TDS_TERMS) and not ("溶质" in prompt and "扩散" in prompt):
        return None
    dimensions = valid_geometry(prompt)
    diffusion = _diffusion_coefficient(prompt)
    left = _concentration_for_side(prompt, "left")
    right = _concentration_for_side(prompt, "right")
    steady = any(term in lower for term in ("stationary", "steady", "稳态"))
    two_dimensional = any(term in lower for term in ("2d", "2-d", "二维")) or dimensions is not None
    no_flux = any(term in lower for term in ("no flux", "no-flux", "无通量", "零通量"))
    unsupported = [
        term for term in ("convection", "对流", "migration", "电场迁移", "reaction", "反应", "nernst–planck", "nernst-planck")
        if term in lower
    ]
    benchmark = "benchmark" in lower or "基准" in lower
    source = "Phase 3C benchmark definition" if benchmark else "user prompt"
    provenance = ParameterStatus.BENCHMARK_DEFINED if benchmark else ParameterStatus.USER_PROVIDED
    parameters: dict[str, ScientificParameter] = {}
    geometry: list[str] = []
    missing: list[str] = []

    if dimensions:
        for name, symbol, quantity in zip(("length", "height"), ("L", "H"), dimensions):
            parameters[name] = _geometry_parameter(name, symbol, quantity, source=source, status=provenance)
        length_m, height_m = (q.normalized_value for q in dimensions)
        geometry.append(f"2D rectangle: {length_m:g} m × {height_m:g} m")
    else:
        missing.extend(["Rectangle length L", "Rectangle height H"])

    if diffusion:
        raw_diffusion, raw_unit = diffusion
        parameters["diffusion_coefficient"] = parameter("diffusion_coefficient", "D", raw_diffusion, "m^2/s", source, original_value=raw_diffusion, original_unit=raw_unit, status=provenance)
        quantity = find_quantity(prompt, r'(?<![A-Za-z0-9_])D(?![A-Za-z0-9_(])|diffusion coefficient|扩散系数', 'diffusion')
        if quantity and not quantity.issue:
            parameters['diffusion_coefficient'].original_value = quantity.original_value
            parameters['diffusion_coefficient'].original_unit = quantity.original_unit
            parameters['diffusion_coefficient'].quantity = asdict(quantity)
    else:
        missing.append("Diffusion coefficient D with a traceable or user-provided value")

    boundary_conditions: list[str] = []
    for side, parsed in (("left", left), ("right", right)):
        name = f"{side}_concentration"
        if parsed:
            value, raw_unit = parsed
            parameters[name] = parameter(name, "c_left" if side == "left" else "c_right", value, "mol/m^3", source, original_value=value, original_unit=raw_unit, status=provenance)
            boundary_conditions.append(f"{side.title()} boundary: Concentration = {name}")
        else:
            missing.append(f"Numeric {side} boundary concentration")
    if no_flux:
        boundary_conditions.append("Top and bottom boundaries: No Flux")
    else:
        missing.append("Top and bottom boundary behavior (No Flux required by this workflow)")
    if not steady:
        missing.append("Stationary study declaration")
    if not two_dimensional:
        missing.append("2D geometry dimensionality")
    if unsupported:
        missing.append("Remove unsupported transport mechanisms from the pure-diffusion Phase 3C workflow")

    complete = dimensions is not None and diffusion is not None and left is not None and right is not None and steady and two_dimensional and no_flux and not unsupported
    plan = ScientificPlan(
        workflow_id="tds_2d_stationary_diffusion",
        title="2D stationary Transport of Diluted Species — pure diffusion",
        research_goal=prompt.strip(),
        original_request=prompt.strip(),
        physics=["Transport of Diluted Species", "Pure diffusion (zero convection, no migration, no reactions)"],
        geometry=geometry,
        materials=["No material database assignment; explicit diffusion coefficient only"],
        parameters=parameters,
        boundary_conditions=boundary_conditions,
        mesh_strategy=["COMSOL physics-controlled default 2D mesh", "Record element and solved-node counts"] if dimensions else [],
        study=["Stationary study"] if steady else [],
        solver=["COMSOL stationary solver", "Require explicit completion readback"] if steady else [],
        validation=[
            "Coordinate-resolved boundary selection readback",
            "Boundary concentration and physical-range checks",
            "Linear analytical solution RMSE, MAE and maximum-error checks",
            "Monotonicity and cross-y uniformity checks",
            "COMSOL tds.dflux_cx analytical flux comparison",
            "Integrated tds.ndflux_c inflow/outflow mass balance",
        ] if complete else ["Resolve missing inputs before defining executable scientific validation"],
        expected_outputs=["Concentration field", "COMSOL MPH model", "Concentration PNG", "Concentration profile CSV", "Validation JSON", "Model Card 2.0", "Raw execution log"] if complete else ["Stationary concentration field"],
        assumptions=["Constant isotropic D", "Zero velocity", "No migration", "No reactions"] if complete else [],
        missing_information=missing,
        warnings=[f"Unsupported Phase 3C mechanisms requested: {', '.join(unsupported)}"] if unsupported else [],
        runnable=complete,
    )
    return plan.model_dump(mode="json")


def extract_solid_plan(prompt: str) -> dict[str, Any] | None:
    """Parse an explicit 3D uniaxial bar; no material/load value is invented."""

    lower = prompt.lower()
    if not any(term in lower for term in SOLID_TERMS):
        return None

    dimensions = valid_geometry(prompt, 3)
    young_match = re.search(rf"(?:young'?s modulus|\bE\b|杨氏模量)\s*(?:=|为|is)?\s*({NUMBER})\s*(GPa|MPa|Pa)", prompt, re.I)
    poisson_match = re.search(rf"(?:poisson'?s ratio|\bnu\b|\bν\b|泊松比)\s*(?:=|为|is)?\s*({NUMBER})", prompt, re.I)
    density_match = re.search(rf"(?:density|\brho\b|\bρ\b|密度)\s*(?:=|为|is)?\s*({NUMBER})\s*kg/m(?:\^?3|³)", prompt, re.I)
    traction_match = re.search(rf"(?:traction|axial stress|拉力面载荷|拉伸应力|面载荷)\s*(?:=|为|is)?\s*({NUMBER})\s*(GPa|MPa|kPa|Pa)", prompt, re.I)
    fixed = "fixed" in lower or "固定" in prompt
    stationary = any(term in lower for term in ("stationary", "steady")) or "稳态" in prompt
    missing: list[str] = []
    parameters: dict[str, ScientificParameter] = {}
    geometry: list[str] = []

    if dimensions:
        names = ("length", "width", "height")
        for name, quantity in zip(names, dimensions):
            parameters[name] = _geometry_parameter(name, name[0].upper(), quantity)
        geometry = ["3D block defined by length, width and height parameters"]
    else:
        missing.append("Explicit 3D bar dimensions (length × width × height)")

    def pressure(raw: float, unit: str) -> float:
        return raw * {"pa": 1.0, "kpa": 1e3, "mpa": 1e6, "gpa": 1e9}[unit.lower()]

    if young_match:
        raw, unit = float(young_match.group(1)), young_match.group(2)
        parameters["youngs_modulus"] = parameter("youngs_modulus", "E", pressure(raw, unit), "Pa", "user prompt", original_value=raw, original_unit=unit)
    else:
        missing.append("Young's modulus")
    if poisson_match:
        raw = float(poisson_match.group(1))
        parameters["poissons_ratio"] = parameter("poissons_ratio", "nu", raw, "1", "user prompt", original_value=raw, original_unit="1")
    else:
        missing.append("Poisson's ratio")
    if density_match:
        raw = float(density_match.group(1))
        parameters["density"] = parameter("density", "rho", raw, "kg/m^3", "user prompt", original_value=raw, original_unit="kg/m^3")
    else:
        missing.append("Density")
    if traction_match:
        raw, unit = float(traction_match.group(1)), traction_match.group(2)
        parameters["traction"] = parameter("traction", "t_x", pressure(raw, unit), "Pa", "user prompt", original_value=raw, original_unit=unit)
    else:
        missing.append("Right-face axial traction")
    if not fixed:
        missing.append("Fixed support boundary")
    if not stationary:
        missing.append("Stationary study")

    complete = not missing
    plan = ScientificPlan(
        workflow_id="solid_mechanics_uniaxial_bar",
        title="3D uniaxial Solid Mechanics bar",
        research_goal=prompt.strip(),
        original_request=prompt.strip(),
        physics=["Solid Mechanics", "Isotropic linear elasticity"],
        geometry=geometry,
        materials=["User-defined isotropic linear elastic material"] if young_match and poisson_match and density_match else [],
        parameters=parameters,
        boundary_conditions=["Left face: Fixed Constraint", "Right face: axial traction"] if fixed and traction_match else [],
        mesh_strategy=["Physics-controlled tetrahedral mesh", "Record element and solution-node counts"] if complete else [],
        study=["Stationary study"] if stationary else [],
        solver=["COMSOL stationary Solid Mechanics solver", "Require convergence confirmation"] if stationary else [],
        validation=["Verify E, nu, density, selections and study by COMSOL readback", "Compare u_x with sigma L / E", "Compare axial/von Mises stress with applied traction", "Verify force resultant against traction times area"] if complete else [],
        expected_outputs=["Displacement field", "von Mises stress field", "COMSOL MPH model", "Stress PNG", "Displacement profile CSV", "Validation JSON", "Model Card 2.0"] if complete else [],
        missing_information=missing,
        runnable=complete,
        geometry_features=[{"tag": "blk1", "kind": "block", "dimension": 3, "parameters": {"size": ["length", "width", "height"]}}] if dimensions else [],
        selections=[{"tag": "sel_left", "label": "Left face", "entity_dimension": 2, "method": "box"}, {"tag": "sel_right", "label": "Right face", "entity_dimension": 2, "method": "box"}] if dimensions else [],
        boundary_definitions=[{"tag": "fix1", "kind": "Fixed", "selection": "sel_left"}, {"tag": "bndl1", "kind": "BoundaryLoad", "selection": "sel_right", "values": {"F": ["traction", 0, 0]}}] if complete else [],
        mesh_definition={"level": "normal", "physics_controlled": True} if complete else None,
        study_definitions=[{"tag": "std1", "kind": "stationary"}] if stationary else [],
        output_definitions=[{"kind": "mph"}, {"kind": "png", "expression": "solid.mises"}, {"kind": "csv", "expression": "u"}, {"kind": "json"}, {"kind": "model_card"}] if complete else [],
    )
    return plan.model_dump(mode="json")


def extract_electrochem_plan(prompt: str) -> dict[str, Any] | None:
    lower = prompt.lower()
    if not any(term in lower for term in ELECTROCHEM_TERMS):
        return None
    dimensions = valid_geometry(prompt)
    conductivity = re.search(rf"(?:electrolyte conductivity|conductivity|电解质电导率|电导率)\s*(?:=|为|is)?\s*({NUMBER})\s*S/m", prompt, re.I)
    left = re.search(rf"(?:left|左(?:侧|边界)?)\D{{0,30}}?({NUMBER})\s*V", prompt, re.I)
    right = re.search(rf"(?:right|右(?:侧|边界)?)\D{{0,30}}?({NUMBER})\s*V", prompt, re.I)
    stationary = any(term in lower for term in ("stationary", "steady")) or "稳态" in prompt
    parameters: dict[str, ScientificParameter] = {}
    missing: list[str] = []
    if dimensions:
        for name, quantity in zip(("length", "height"), dimensions):
            parameters[name] = _geometry_parameter(name, name[0].upper(), quantity)
    else:
        missing.append("2D electrolyte dimensions")
    if conductivity:
        raw = float(conductivity.group(1)); parameters["electrolyte_conductivity"] = parameter("electrolyte_conductivity", "sigma_l", raw, "S/m", "user prompt", original_value=raw, original_unit="S/m")
    else:
        missing.append("Electrolyte conductivity")
    for name, match in (("left_potential", left), ("right_potential", right)):
        if match:
            raw = float(match.group(1)); parameters[name] = parameter(name, "phi_l", raw, "V", "user prompt", original_value=raw, original_unit="V")
        else:
            missing.append(name.replace("_", " ").title())
    if not stationary:
        missing.append("Stationary study")
    complete = not missing
    return ScientificPlan(
        workflow_id="electrochem_primary_current", title="2D primary current distribution benchmark", research_goal=prompt.strip(), original_request=prompt.strip(),
        physics=["Primary Current Distribution", "Electrolyte charge conservation"], geometry=["2D rectangle defined by length and height"] if dimensions else [],
        materials=["Electrolyte with user-defined conductivity"] if conductivity else [], parameters=parameters,
        boundary_conditions=["Left boundary: Electrolyte Potential", "Right boundary: Electrolyte Potential", "Remaining boundaries: Insulation"] if left and right else [],
        mesh_strategy=["Physics-controlled 2D mesh"] if complete else [], study=["Stationary study"] if stationary else [], solver=["COMSOL stationary primary-current solver"] if stationary else [],
        validation=["Verify Electrochemistry license", "Read back conductivity and Electrolyte Potential selections", "Compare potential with analytical linear field", "Compare current density with Ohm's law"] if complete else [],
        expected_outputs=["Electrolyte potential field", "Current density", "COMSOL MPH model", "Potential PNG", "CSV", "Validation JSON", "Model Card 2.0"] if complete else [], missing_information=missing, runnable=complete,
        geometry_features=[{"tag": "r1", "kind": "rectangle", "dimension": 2, "parameters": {"size": ["length", "height"]}}] if dimensions else [],
        selections=[{"tag": "sel_left", "label": "Left potential", "entity_dimension": 1, "method": "box"}, {"tag": "sel_right", "label": "Right potential", "entity_dimension": 1, "method": "box"}] if dimensions else [],
        boundary_definitions=[{"tag": "phil_left", "kind": "ElectrolytePotential", "selection": "sel_left", "values": {"philbnd": "left_potential"}}, {"tag": "phil_right", "kind": "ElectrolytePotential", "selection": "sel_right", "values": {"philbnd": "right_potential"}}] if complete else [],
        mesh_definition={"level": "normal", "physics_controlled": True} if complete else None, study_definitions=[{"tag": "std1", "kind": "stationary"}] if stationary else [],
        output_definitions=[{"kind": "mph"}, {"kind": "png", "expression": "phil"}, {"kind": "csv", "expression": "cd.IlMag"}, {"kind": "json"}, {"kind": "model_card"}] if complete else [],
    ).model_dump(mode="json")


def extract_battery_plan(prompt: str) -> dict[str, Any] | None:
    lower = prompt.lower()
    if not any(term in lower for term in BATTERY_TERMS):
        return None
    defined = any(term in lower for term in ("baseline", "benchmark", "verification")) or any(term in prompt for term in ("基准", "验收"))
    values = {
        "applied_current_density": (1.0, "i_app", "A/m^2"),
        "duration": (100.0, "t_end", "s"),
        "initial_electrolyte_concentration": (1000.0, "c_l0", "mol/m^3"),
        "negative_electrode_thickness": (50.0, "L_neg", "um"),
        "separator_thickness": (25.0, "L_sep", "um"),
        "positive_electrode_thickness": (50.0, "L_pos", "um"),
        "battery_temperature": (298.15, "T_bat", "K"),
        "electrolyte_conductivity": (1.0, "sigma_l", "S/m"),
        "electrolyte_diffusivity": (1e-10, "D_l", "m^2/s"),
        "cation_transference_number": (0.4, "t_plus", "1"),
        "negative_electrolyte_volume_fraction": (0.4, "epsl_neg", "1"),
        "negative_solid_volume_fraction": (0.5, "epss_neg", "1"),
        "positive_electrolyte_volume_fraction": (0.4, "epsl_pos", "1"),
        "positive_solid_volume_fraction": (0.5, "epss_pos", "1"),
        "negative_solid_conductivity": (100.0, "sigma_s_neg", "S/m"),
        "positive_solid_conductivity": (100.0, "sigma_s_pos", "S/m"),
        "negative_max_solid_concentration": (30000.0, "csmax_neg", "mol/m^3"),
        "positive_max_solid_concentration": (30000.0, "csmax_pos", "mol/m^3"),
        "negative_initial_solid_concentration": (24000.0, "cs0_neg", "mol/m^3"),
        "positive_initial_solid_concentration": (6000.0, "cs0_pos", "mol/m^3"),
        "negative_solid_diffusivity": (1e-13, "Ds_neg", "m^2/s"),
        "positive_solid_diffusivity": (1e-13, "Ds_pos", "m^2/s"),
        "negative_particle_radius": (5.0, "rp_neg", "um"),
        "positive_particle_radius": (5.0, "rp_pos", "um"),
        "negative_rate_constant": (1e-11, "k_neg", "m/s"),
        "positive_rate_constant": (1e-11, "k_pos", "m/s"),
    }
    parameters = {
        name: parameter(name, symbol, value, unit, "v0.7 minimal verification baseline", status=ParameterStatus.BENCHMARK_DEFINED)
        for name, (value, symbol, unit) in values.items()
    } if defined else {}
    missing = [] if defined else ["Applied current density", "Discharge duration", "Initial electrolyte concentration", "Explicit confirmation to use the minimal verification baseline"]
    chemistry = "zinc-ion, user intent only" if "锌" in prompt or "zinc" in lower else "lithium-ion" if "锂" in prompt or "lithium" in lower else "unspecified battery chemistry"
    return ScientificPlan(
        workflow_id="lithium_ion_battery_baseline" if defined else None, title="Minimal lithium-ion battery discharge verification baseline" if defined else "Battery-related electrochemical transport request",
        research_goal=prompt.strip(), original_request=prompt.strip(), physics=["Lithium-Ion Battery (LithiumIonBatteryMPH)"] if defined else ["Battery-related electrochemical transport (interface not selected)"],
        geometry=["1D negative electrode / separator / positive electrode"] if defined else [],
        materials=["Explicit benchmark-defined generic properties; no commercial chemistry claim"] if defined else [], parameters=parameters,
        boundary_conditions=["Negative current collector: Electric Ground", "Positive current collector: applied discharge current density"] if defined else [],
        mesh_strategy=["Physics-controlled 1D mesh"] if defined else [], study=["Current Distribution Initialization", "Time Dependent study"] if defined else [],
        solver=["COMSOL Battery Design Module transient solver"] if defined else [],
        validation=["Require BATTERYDESIGN license and LithiumIonBatteryMPH readback", "Verify discharge voltage direction, current conservation and opposite electrode inventory changes"] if defined else [],
        expected_outputs=["Cell voltage", "Electrolyte concentration", "Current density", "COMSOL MPH model", "PNG", "CSV", "Validation JSON", "Model Card 2.0"] if defined else [],
        missing_information=missing, runnable=defined,
        study_definitions=[{"tag": "std1", "kind": "time_dependent", "times": [0.0, 100.0]}] if defined else [],
        output_definitions=[{"kind": "mph"}, {"kind": "png", "expression": "cl"}, {"kind": "csv", "expression": "cl"}, {"kind": "json"}, {"kind": "model_card"}] if defined else [],
        battery_cell={"chemistry": "minimal lithium-ion verification baseline" if defined else chemistry, "geometry": "1D three-layer cell" if defined else "unspecified", "parameters": parameters, "initial_soc": None, "operating_mode": "current" if defined else None, "module_status": "UNVERIFIED"},
        assumptions=["Generic explicit benchmark properties and simplified concentration-dependent equilibrium-potential expressions are for interface verification only and do not represent a commercial cell"] if defined else [],
    ).model_dump(mode="json")


def extract_multiphysics_plan(prompt: str) -> dict[str, Any] | None:
    lower = prompt.lower()
    if not any(term in lower for term in COUPLING_TERMS) or not (any(term in lower for term in HEAT_TERMS) and any(term in lower for term in TDS_TERMS)):
        return None
    defined = any(term in lower for term in ("baseline", "benchmark", "verification", "d(t)=d0*t/t0")) or any(term in prompt for term in ("基准", "验收", "D(T)=D0*T/T0"))
    values = {
        "length": (1.0, "L", "m"), "height": (0.1, "H", "m"), "diffusion_coefficient": (1e-9, "D0", "m^2/s"),
        "reference_temperature": (293.0, "T0", "K"), "left_temperature": (293.0, "T_left", "K"), "right_temperature": (373.0, "T_right", "K"),
        "left_concentration": (10.0, "c_left", "mol/m^3"), "right_concentration": (1.0, "c_right", "mol/m^3"),
    }
    parameters = {
        name: parameter(name, symbol, value, unit, "v0.7 Heat-to-TDS verification baseline", status=ParameterStatus.BENCHMARK_DEFINED)
        for name, (value, symbol, unit) in values.items()
    } if defined else {}
    missing = [] if defined else ["Geometry, D0, T0, temperature boundaries and concentration boundaries", "Explicit D(T)=D0*T/T0 coupling definition"]
    return ScientificPlan(
        workflow_id="heat_tds_temperature_coupling", title="Heat → TDS temperature-dependent diffusion benchmark",
        research_goal=prompt.strip(), original_request=prompt.strip(), physics=["Heat Transfer in Solids", "Transport of Diluted Species"],
        geometry=["2D rectangle shared by Heat Transfer and TDS"] if defined else [], materials=["User-defined constant thermal properties"] if defined else [], parameters=parameters,
        boundary_conditions=["Left/right Temperature", "Left/right Concentration", "Remaining boundaries: Thermal Insulation and No Flux"] if defined else [],
        mesh_strategy=["Physics-controlled 2D mesh"] if defined else [], study=["Stationary coupling OFF/ON comparison"] if defined else [],
        solver=["COMSOL stationary solver; constant D then D(T)=D0*T/T0"] if defined else [],
        validation=["Read back real T dependency", "Compare OFF and ON solves", "Compare temperature and variable-D concentration with analytical references", "Verify flux and mass balance"] if defined else [],
        expected_outputs=["Temperature field", "Concentration field", "OFF/ON comparison", "COMSOL MPH model", "PNG", "CSV", "Validation JSON", "Model Card 2.0"] if defined else [],
        missing_information=missing, runnable=defined,
        couplings=[{"source": "Heat Transfer", "target": "Transport of Diluted Species", "variables": ["T"], "status": "UNVERIFIED"}] if defined else [],
        study_definitions=[{"tag": "std1", "kind": "stationary"}] if defined else [],
        output_definitions=[{"kind": "mph"}, {"kind": "png", "expression": "T"}, {"kind": "png", "expression": "c"}, {"kind": "csv", "expression": "c"}, {"kind": "json"}, {"kind": "model_card"}] if defined else [],
    ).model_dump(mode="json")


class ScientificPlanner:
    def __init__(self, providers: ProviderManager = provider_manager) -> None:
        self.providers = providers

    async def plan(
        self,
        prompt: str,
        citations: list[dict[str, Any]],
        supplemental_context: str = "",
        original_prompt: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        source_prompt = original_prompt or prompt
        offline_data = extract_multiphysics_plan(source_prompt) or extract_battery_plan(source_prompt) or extract_tds_plan(source_prompt) or extract_heat_transfer_plan(source_prompt) or extract_solid_plan(source_prompt) or extract_electrochem_plan(source_prompt) or generic_plan(source_prompt)
        actual_sources = [item["citation"] for item in citations]
        schema = planning_schema(offline_data)
        response = await self.providers.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a cautious COMSOL scientific planner. Return only one JSON object matching the supplied ScientificPlan schema. "
                        "Never invent dimensions, temperatures, boundary conditions, citations, source pages, material values or COMSOL success. "
                        "Put absent essential facts in missing_information and set runnable=false. Planning must not execute COMSOL. "
                        "HEAT PLAN CONTRACT: For a 2D stationary heat-transfer request, use workflow_id='heat_transfer_2d_stationary'; "
                        "physics must include 'Heat Transfer in Solids'; study must include 'Stationary study'. Use parameters object keys exactly "
                        "width, height, left_temperature, right_temperature. Normalize executable lengths to m and temperatures to K while recording "
                        "original_value/original_unit for conversions. Each user value has source='user prompt', status='User Provided', verified=true. "
                        "When a complete aluminum benchmark lacks a verified alloy source, include thermal_conductivity=238 W/(m*K), density=2700 kg/m^3, "
                        "and heat_capacity=900 J/(kg*K) explicitly as status='Assumption', assumption=true, verified=false, and add a warning. "
                        "Mesh and validation methods are planner choices, not missing user information. If geometry dimensions or numeric boundary "
                        "temperatures are absent, omit those parameter keys, list each missing essential fact, and keep runnable=false. "
                        "TDS PLAN CONTRACT: For a 2D stationary pure-diffusion Transport of Diluted Species request, use "
                        "workflow_id='tds_2d_stationary_diffusion' and parameter keys exactly length, height, diffusion_coefficient, "
                        "left_concentration, right_concentration. Normalize lengths to m, D to m^2/s and concentration to mol/m^3. "
                        "Require explicit D and both boundary concentrations; never borrow D from a benchmark or material database. "
                        "Top/bottom No Flux, zero convection, no migration and no reactions must be explicit. If the request identifies a benchmark, "
                        "use status='Benchmark-defined' and source='Phase 3C benchmark definition'; otherwise numeric inputs are User Provided. "
                        "SOLID PLAN CONTRACT: For an explicit 3D uniaxial bar use workflow_id='solid_mechanics_uniaxial_bar' and preserve exact "
                        "parameter keys length, width, height, youngs_modulus, poissons_ratio, density and traction in SI units. Never invent material "
                        "or load values. Require left Fixed Constraint, right axial traction and Stationary study."
                    ),
                },
                {"role": "user", "content": f"Problem:\n{prompt}\n\nBounded session context (includes selected evidence):\n{supplemental_context}\n\nJSON schema:\n{json.dumps(schema, ensure_ascii=False)}"},
            ],
            json_mode=True,
            json_schema=schema,
        )
        if response is None:
            offline = ScientificPlan.model_validate(offline_data)
            offline.sources = [Citation.model_validate(item) for item in actual_sources]
            return offline.model_dump(mode="json"), "offline-scientific-planner", "none"

        try:
            plan = self._parse_provider_plan(response.text, actual_sources, response.provider_label, response.model, source_prompt)
            plan = enforce_deterministic_contract(plan, ScientificPlan.model_validate(offline_data))
            plan = await self._quality_repair_if_needed(plan, prompt, source_prompt, actual_sources, schema, offline_data)
            plan = enforce_deterministic_contract(plan, ScientificPlan.model_validate(offline_data))
            return plan.model_dump(mode="json"), response.provider_label, response.model
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as first_error:
            repair = await self.providers.complete(
                [
                    {"role": "system", "content": "Repair the invalid ScientificPlan. Return only one complete JSON object. Do not add facts absent from the problem or evidence."},
                    {
                        "role": "user",
                        "content": (
                            f"Problem:\n{prompt}\n\nValidation error:\n{first_error}\n\nInvalid output:\n{response.text[:16000]}"
                            f"\n\nRequired JSON schema:\n{json.dumps(schema, ensure_ascii=False)}"
                        ),
                    },
                ],
                json_mode=True,
                json_schema=schema,
            )
            if repair is None:
                raise PlanGenerationFailure(
                    f"Plan Generation Failure: {response.provider_label} returned invalid JSON/schema and the repair request produced no response"
                ) from first_error
            try:
                repaired = self._parse_provider_plan(repair.text, actual_sources, repair.provider_label, repair.model, source_prompt)
                repaired.warnings.append("Provider output required one explicit JSON/schema repair")
                repaired = enforce_deterministic_contract(repaired, ScientificPlan.model_validate(offline_data))
                repaired = await self._quality_repair_if_needed(repaired, prompt, source_prompt, actual_sources, schema, offline_data)
                repaired = enforce_deterministic_contract(repaired, ScientificPlan.model_validate(offline_data))
                return repaired.model_dump(mode="json"), repair.provider_label, repair.model
            except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as repair_error:
                deterministic = ScientificPlan.model_validate(offline_data)
                if deterministic.workflow_id is not None:
                    deterministic.sources = [Citation.model_validate(item) for item in actual_sources]
                    deterministic.warnings.append(
                        f"Provider output was rejected after one repair ({type(repair_error).__name__}); executable facts remain deterministic."
                    )
                    deterministic.ai_provider = "none"
                    deterministic.ai_model = "none"
                    deterministic.source = "deterministic-safety-envelope"
                    return deterministic.model_dump(mode="json"), "deterministic-safety-envelope", "none"
                raise PlanGenerationFailure(
                    f"Plan Generation Failure: provider output remained invalid after one repair ({type(repair_error).__name__}: {repair_error})"
                ) from repair_error

    @staticmethod
    def _parse_provider_plan(text: str, sources: list[dict[str, Any]], provider_label: str, model: str, original_request: str) -> ScientificPlan:
        parsed = json.loads(strip_json_fence(text))
        if not isinstance(parsed, dict):
            raise ValueError("ScientificPlan must be a JSON object")
        parsed["sources"] = sources
        parsed["ai_provider"] = provider_label
        parsed["ai_model"] = model
        parsed["source"] = provider_label
        parsed["original_request"] = original_request.strip()
        return ScientificPlan.model_validate(parsed)

    async def _quality_repair_if_needed(
        self,
        plan: ScientificPlan,
        prompt: str,
        original_request: str,
        sources: list[dict[str, Any]],
        schema: dict[str, Any],
        offline_data: dict[str, Any],
    ) -> ScientificPlan:
        """Give a complete user-specified benchmark one visible quality repair; never merge parser values."""

        offline = ScientificPlan.model_validate(offline_data)
        quality = plan_quality_validator.validate(plan)
        if not offline.runnable or quality.status != "INVALID":
            return plan
        repair_contract = (
            "Required corrections for this TDS workflow: keep exact parameter keys length, height, diffusion_coefficient, left_concentration, "
            "right_concentration; normalize to m, m^2/s and mol/m^3; require Stationary, left/right Concentration and top/bottom No Flux; "
            "never invent a missing D or concentration."
            if offline.workflow_id == "tds_2d_stationary_diffusion"
            else "Required corrections for this heat benchmark: keep exact parameter keys width, height, left_temperature, right_temperature; "
            "supply Stationary study, explicit temperature-field output and thermal insulation; normalize mixed length units to m and Celsius to K; "
            "include aluminum benchmark properties 238 W/(m*K), 2700 kg/m^3 and 900 J/(kg*K) only as explicit Assumption parameters."
        )
        response = await self.providers.complete(
            [
                {"role": "system", "content": "Repair this schema-valid but scientifically incomplete ScientificPlan. Return only complete JSON matching the schema. Copy every numeric fact from the original request exactly; do not invent facts or citations."},
                {"role": "user", "content": (
                    f"Original request:\n{prompt}\n\nQuality errors:\n- " + "\n- ".join(quality.errors)
                    + f"\n\n{repair_contract} "
                    f"Do not list planner-owned mesh or validation choices as missing information.\n\nInvalid plan:\n{json.dumps(plan.model_dump(mode='json'), ensure_ascii=False)[:18000]}"
                )},
            ],
            json_mode=True,
            json_schema=schema,
        )
        if response is None:
            plan.warnings.append("Plan quality repair was unavailable; the invalid plan remains blocked")
            return plan
        try:
            repaired = self._parse_provider_plan(response.text, sources, response.provider_label, response.model, original_request)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            raise PlanGenerationFailure(f"Plan Generation Failure: quality-repair output violated ScientificPlan schema ({type(exc).__name__}: {exc})") from exc
        repaired.warnings.append("Provider output required one explicit Plan Quality repair")
        return repaired


def enforce_deterministic_contract(provider_plan: ScientificPlan, deterministic: ScientificPlan) -> ScientificPlan:
    """Keep parser-normalized execution fields authoritative over model prose.

    A provider may improve title, explanation and warnings, but cannot make an
    incomplete deterministic workflow runnable or replace normalized facts.
    """
    if deterministic.workflow_id is None:
        provider_plan.runnable = False
        provider_plan.missing_information = list(dict.fromkeys([
            *deterministic.missing_information, *provider_plan.missing_information,
        ]))
        return provider_plan
    protected = (
        'workflow_id', 'physics', 'geometry', 'materials', 'parameters',
        'boundary_conditions', 'mesh_strategy', 'study', 'solver', 'validation',
        'expected_outputs', 'assumptions', 'missing_information', 'steps', 'runnable',
        'geometry_features', 'selections', 'material_definitions', 'initial_conditions',
        'boundary_definitions', 'variables', 'functions', 'mesh_definition',
        'study_definitions', 'solver_definition', 'output_definitions',
        'validation_definitions', 'resource_policy', 'couplings', 'battery_cell',
    )
    for name in protected:
        setattr(provider_plan, name, getattr(deterministic, name))
    provider_plan.warnings = list(dict.fromkeys([
        *deterministic.warnings, *provider_plan.warnings,
        'Executable facts are pinned to deterministic intent/quantity normalization.',
    ]))
    return provider_plan


def generic_plan(prompt: str) -> dict[str, Any]:
    return ScientificPlan(
        title="Scientific modeling plan",
        research_goal=prompt.strip(),
        original_request=prompt.strip(),
        validation=["Define analytical, experimental or convergence checks before execution"],
        missing_information=["Governing physics", "Geometry", "Materials", "Boundary conditions", "Study type"],
        steps=[
            {"phase": "Research", "label": "Identify governing physics and evidence", "purpose": "Complete missing scientific inputs"},
            {"phase": "Plan", "label": "Define geometry, materials, boundaries and units", "purpose": "Build a validated ScientificPlan"},
        ],
        warnings=["No verified RUN executor is selected for this problem"],
        runnable=False,
    ).model_dump(mode="json")


def strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def planning_schema(offline_data: dict[str, Any]) -> dict[str, Any]:
    """Tighten the JSON contract for a complete verified workflow without inserting output values."""

    schema = json.loads(json.dumps(ScientificPlan.model_json_schema()))
    offline = ScientificPlan.model_validate(offline_data)
    if not offline.runnable:
        return schema
    if offline.workflow_id == "tds_2d_stationary_diffusion":
        return tds_planning_schema(schema, offline)
    if offline.workflow_id != "heat_transfer_2d_stationary":
        return schema
    properties = schema["properties"]
    properties["workflow_id"] = {"type": "string", "const": "heat_transfer_2d_stationary"}
    properties["physics"] = {"type": "array", "const": ["Heat Transfer in Solids", "Steady-state conduction"]}
    properties["geometry"] = {"type": "array", "const": ["2D rectangle defined by width and height parameters"]}
    properties["materials"] = {"type": "array", "const": offline.materials}
    properties["boundary_conditions"] = {"type": "array", "const": ["Left: prescribed left_temperature", "Right: prescribed right_temperature", "Top and bottom: thermal insulation"]}
    properties["study"] = {"type": "array", "const": ["Stationary study"]}
    properties["expected_outputs"] = {"type": "array", "const": ["Temperature field", "COMSOL MPH model", "Model Card 2.0", "Raw execution log"]}
    properties["missing_information"] = {"type": "array", "const": []}
    required = set(schema.get("required", []))
    required.update(("workflow_id", "physics", "geometry", "materials", "parameters", "boundary_conditions", "study", "expected_outputs", "missing_information"))
    schema["required"] = sorted(required)

    base_parameter = schema["$defs"]["ScientificParameter"]

    def constrained_parameter(
        unit: str,
        expected_value: float,
        *,
        assumption: bool,
        original_value: float | int | str | None = None,
        original_unit: str | None = None,
    ) -> dict[str, Any]:
        contract = json.loads(json.dumps(base_parameter))
        contract["properties"]["value"] = {"type": "number", "const": expected_value}
        contract["properties"]["unit"] = {"type": "string", "const": unit}
        contract["properties"]["source"] = {"type": "string", "const": "Workbench aluminum benchmark assumption; alloy-specific source required" if assumption else "user prompt"}
        contract["properties"]["assumption"] = {"type": "boolean", "const": assumption}
        contract["properties"]["verified"] = {"type": "boolean", "const": not assumption}
        contract["properties"]["status"] = {"type": "string", "const": "Assumption" if assumption else "User Provided"}
        contract["properties"]["normalized_value"] = {"type": "number", "const": expected_value}
        contract["properties"]["normalized_unit"] = {"type": "string", "const": unit}
        required_fields = set(contract.get("required", []))
        required_fields.update(("name", "value", "unit", "source", "assumption", "verified", "status", "normalized_value", "normalized_unit"))
        if original_value is not None:
            contract["properties"]["original_value"] = {"const": original_value}
            contract["properties"]["original_unit"] = {"type": "string", "const": original_unit}
            required_fields.update(("original_value", "original_unit"))
        contract["required"] = sorted(required_fields)
        return contract

    width = offline.parameters["width"]
    height = offline.parameters["height"]
    width_m = _length_to_m(float(width.value), width.unit)
    height_m = _length_to_m(float(height.value), height.unit)

    parameter_properties = {
        "width": constrained_parameter("m", width_m, assumption=False, original_value=width.original_value, original_unit=width.original_unit),
        "height": constrained_parameter("m", height_m, assumption=False, original_value=height.original_value, original_unit=height.original_unit),
        "left_temperature": constrained_parameter("K", float(offline.parameters["left_temperature"].value), assumption=False, original_value=offline.parameters["left_temperature"].original_value, original_unit=offline.parameters["left_temperature"].original_unit),
        "right_temperature": constrained_parameter("K", float(offline.parameters["right_temperature"].value), assumption=False, original_value=offline.parameters["right_temperature"].original_value, original_unit=offline.parameters["right_temperature"].original_unit),
        "thermal_conductivity": constrained_parameter("W/(m*K)", float(offline.parameters["thermal_conductivity"].value), assumption=offline.parameters["thermal_conductivity"].assumption),
        "density": constrained_parameter("kg/m^3", float(offline.parameters["density"].value), assumption=offline.parameters["density"].assumption),
        "heat_capacity": constrained_parameter("J/(kg*K)", float(offline.parameters["heat_capacity"].value), assumption=offline.parameters["heat_capacity"].assumption),
    }
    properties["parameters"] = {
        "type": "object",
        "properties": parameter_properties,
        "required": list(parameter_properties),
        "additionalProperties": False,
    }
    return schema


def tds_planning_schema(schema: dict[str, Any], offline: ScientificPlan) -> dict[str, Any]:
    """Constrain a complete TDS provider response to facts parsed from the prompt."""

    properties = schema["properties"]
    properties["workflow_id"] = {"type": "string", "const": "tds_2d_stationary_diffusion"}
    properties["physics"] = {"type": "array", "const": ["Transport of Diluted Species", "Pure diffusion (zero convection, no migration, no reactions)"]}
    properties["geometry"] = {"type": "array", "const": offline.geometry}
    properties["materials"] = {"type": "array", "const": ["No material database assignment; explicit diffusion coefficient only"]}
    properties["boundary_conditions"] = {"type": "array", "const": ["Left boundary: Concentration = left_concentration", "Right boundary: Concentration = right_concentration", "Top and bottom boundaries: No Flux"]}
    properties["study"] = {"type": "array", "const": ["Stationary study"]}
    properties["missing_information"] = {"type": "array", "const": []}
    required = set(schema.get("required", []))
    required.update(("workflow_id", "physics", "geometry", "materials", "parameters", "boundary_conditions", "study", "missing_information"))
    schema["required"] = sorted(required)

    base_parameter = schema["$defs"]["ScientificParameter"]
    parameter_properties: dict[str, Any] = {}
    for name in ("length", "height", "diffusion_coefficient", "left_concentration", "right_concentration"):
        expected = offline.parameters[name]
        contract = json.loads(json.dumps(base_parameter))
        contract["properties"]["name"] = {"type": "string", "const": name}
        contract["properties"]["value"] = {"type": "number", "const": float(expected.value)}
        contract["properties"]["unit"] = {"type": "string", "const": expected.unit}
        contract["properties"]["source"] = {"type": "string", "const": expected.source}
        contract["properties"]["assumption"] = {"type": "boolean", "const": False}
        contract["properties"]["verified"] = {"type": "boolean", "const": True}
        contract["properties"]["status"] = {"type": "string", "const": expected.status.value}
        contract["properties"]["normalized_value"] = {"type": "number", "const": float(expected.value)}
        contract["properties"]["normalized_unit"] = {"type": "string", "const": expected.unit}
        required_fields = set(contract.get("required", []))
        required_fields.update(("name", "value", "unit", "source", "assumption", "verified", "status", "normalized_value", "normalized_unit"))
        if expected.original_value is not None:
            contract["properties"]["original_value"] = {"const": expected.original_value}
            contract["properties"]["original_unit"] = {"type": "string", "const": expected.original_unit}
            required_fields.update(("original_value", "original_unit"))
        contract["required"] = sorted(required_fields)
        parameter_properties[name] = contract
    properties["parameters"] = {
        "type": "object",
        "properties": parameter_properties,
        "required": list(parameter_properties),
        "additionalProperties": False,
    }
    return schema


scientific_planner = ScientificPlanner()
