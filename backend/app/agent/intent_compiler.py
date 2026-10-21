"""Deterministic bilingual scientific-intent normalization before planning."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, Field
from app.scientific.quantity import find_quantity, geometry_quantities as parse_geometry_quantities


class IntentParameter(BaseModel):
    name: str
    symbol: str
    value: float | str | None = None
    unit: str = ""
    original: str = ""
    resolved: bool = False
    quantity: dict = Field(default_factory=dict)


class IntentBoundary(BaseModel):
    location: str
    condition: str
    value: float | None = None
    unit: str = ""
    resolved: bool = True
    quantity: dict = Field(default_factory=dict)


class ScientificIntent(BaseModel):
    mode: Literal["PLAN"] = "PLAN"
    language: Literal["zh-CN", "en-US"]
    goal: str
    physics: list[str] = Field(default_factory=list)
    geometry: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    parameters: dict[str, IntentParameter] = Field(default_factory=dict)
    boundary_conditions: list[IntentBoundary] = Field(default_factory=list)
    initial_conditions: list[str] = Field(default_factory=list)
    study: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    confidence: Literal["high", "needs_confirmation", "missing_information"]
    canonical_query: str
    original_text: str
    geometry_quantities: list[dict] = Field(default_factory=list)


HEAT_ALIASES = ("热传导", "传热", "heat transfer", "heat-transfer", "heat transfer in solids")
TDS_ALIASES = ("稀物质传递", "稀释物质传递", "溶质扩散", "diluted species", "transport of diluted species", "tds", "pure diffusion model")
SOLID_ALIASES = ("固体力学", "单轴拉伸", "拉伸杆", "solid mechanics", "uniaxial", "tensile bar")
ELECTROCHEM_ALIASES = ("电化学", "一次电流分布", "electrochemistry", "primary current distribution")
BATTERY_ALIASES = ("电池", "锂离子", "锌离子", "battery", "lithium-ion", "lithium ion", "zinc-ion", "zinc ion")


def compile_intent(text: str) -> ScientificIntent:
    original = text.strip()
    lower = original.lower()
    language: Literal["zh-CN", "en-US"] = "zh-CN" if re.search(r"[\u3400-\u9fff]", original) else "en-US"
    physics: list[str] = []
    if any(alias in lower for alias in HEAT_ALIASES):
        physics.append("Heat Transfer in Solids")
    if any(alias in lower for alias in TDS_ALIASES) or ("扩散" in original and any(term in original for term in ("浓度", "浓一点", "淡一点"))):
        physics.append("Transport of Diluted Species")
    if any(alias in lower for alias in SOLID_ALIASES):
        physics.append("Solid Mechanics")
    if any(alias in lower for alias in ELECTROCHEM_ALIASES):
        physics.append("Primary Current Distribution")
    if any(alias in lower for alias in BATTERY_ALIASES):
        physics.append("Lithium-Ion Battery" if any(term in lower for term in ("lithium", "锂离子", "battery baseline")) or "电池基准" in original else "Battery-related electrochemical transport")

    battery_baseline = (
        any("battery" in value.lower() for value in physics)
        and any(term in lower for term in ("baseline", "benchmark", "verification"))
    ) or (any("battery" in value.lower() for value in physics) and any(term in original for term in ("基准", "验收")))
    coupling_baseline = (
        "Heat Transfer in Solids" in physics
        and "Transport of Diluted Species" in physics
        and any(term in lower for term in ("d(t)", "temperature-dependent", "temperature dependent"))
        and any(term in lower for term in ("baseline", "benchmark", "verification"))
    ) or (
        "Heat Transfer in Solids" in physics
        and "Transport of Diluted Species" in physics
        and "温度依赖" in original
        and any(term in original for term in ("基准", "验收"))
    )

    geometry: list[str] = []
    if battery_baseline:
        geometry.append("1D negative electrode / separator / positive electrode")
    elif coupling_baseline:
        geometry.append("2D Heat Transfer / TDS verification rectangle")
    if re.search(r"\b2\s*d\b|二维", lower):
        geometry.append("2D")
    if re.search(r"\b3\s*d\b|三维", lower) or "Solid Mechanics" in physics:
        geometry.append("3D")
    dimensions = parse_geometry_quantities(original, 3)
    if not dimensions and "3D" not in geometry:
        dimensions = parse_geometry_quantities(original, 2)
    if dimensions:
        shape = "Block" if len(dimensions) == 3 else "Rectangle"
        geometry.append(shape + " " + " × ".join(q.original for q in dimensions))


    materials = ["Aluminum"] if "铝" in original or "aluminum" in lower or "aluminium" in lower else []
    parameters: dict[str, IntentParameter] = {}
    boundary_conditions: list[IntentBoundary] = []
    unknowns: list[str] = []
    issues: list[str] = []
    if "Heat Transfer in Solids" in physics:
        properties = {
            "thermal_conductivity": (r'(?<![A-Za-z0-9_])k\s*=|thermal conductivity|热导率', 'thermal_conductivity', 'k'),
            "density": (r'density|(?<![A-Za-z0-9_])rho\s*=|密度', 'density', 'rho'),
            "heat_capacity": (r'heat capacity|specific heat|(?<![A-Za-z0-9_])Cp\s*=|比热容', 'heat_capacity', 'Cp'),
        }
        for name, (labels, kind, symbol) in properties.items():
            quantity = find_quantity(original, labels, kind, gap=12)
            if quantity is None:
                continue
            if quantity.issue:
                issues.append(f"{name}: {quantity.issue}: {quantity.original}")
            parameters[name] = IntentParameter(
                name=name.replace('_', ' ').title(), symbol=symbol,
                value=quantity.normalized_value, unit=quantity.si_unit,
                original=quantity.original, resolved=quantity.issue is None,
                quantity=asdict(quantity),
            )
        if not materials and parameters.get("thermal_conductivity") and parameters["thermal_conductivity"].resolved:
            materials = ["User-defined thermal material (property provenance requires review)"]
    if "Heat Transfer in Solids" in physics and not materials and not coupling_baseline:
        unknowns.append("Material definition")
    if "Primary Current Distribution" in physics:
        side_potentials = {
            side: find_quantity(original, labels, "potential", gap=40, allow_before=True)
            for side, labels in (("Left", r'left|左(?:侧|边界)?'), ("Right", r'right|右(?:侧|边界)?'))
        }
        if not side_potentials["Left"] or side_potentials["Left"].issue:
            unknowns.append("Left potential boundary")
        if not side_potentials["Right"] or side_potentials["Right"].issue:
            unknowns.append("Right potential boundary")
        if not re.search(r"(?:conductivity|电导率).{0,24}" + r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)\s*S/m", original, re.I):
            unknowns.append("Electrolyte conductivity")
    geometry_quantities: list[dict] = []
    for quantity in dimensions:
        geometry_quantities.append(asdict(quantity))
        if quantity.issue:
            issues.append(f'Geometry length: {quantity.issue}: {quantity.original}')

    outputs: list[str] = []

    if "Transport of Diluted Species" in physics:
        quantity = find_quantity(original, r"扩散系数|(?<![A-Za-z0-9_])D(?![A-Za-z0-9_(])|diffusion coefficient", "diffusion")
        d_value = quantity.normalized_value if quantity else None
        if quantity and quantity.issue:
            issues.append(f"Diffusion coefficient D: {quantity.issue}: {quantity.original}")
        parameters["diffusion_coefficient"] = IntentParameter(name="Diffusion coefficient", symbol="D", value=d_value, unit="m²/s", original=quantity.original if quantity else "", resolved=d_value is not None, quantity=asdict(quantity) if quantity else {})
        if d_value is None and not coupling_baseline:
            unknowns.append("Diffusion coefficient D")
        elif d_value is not None and d_value <= 0:
            issues.append("Diffusion coefficient D must be greater than zero")
        concentration_patterns = {
            "Left": (r"(?:左侧|左边界|左边).{0,24}?(-?[\d.]+(?:e[-+]?\d+)?)\s*mol/m(?:³|\^3)", r"left.{0,28}?(-?[\d.]+(?:e[-+]?\d+)?)\s*mol/m(?:³|\^3)"),
            "Right": (r"(?:右侧|右边界|右边).{0,24}?(-?[\d.]+(?:e[-+]?\d+)?)\s*mol/m(?:³|\^3)", r"right.{0,28}?(-?[\d.]+(?:e[-+]?\d+)?)\s*mol/m(?:³|\^3)"),
        }
        for location, patterns in concentration_patterns.items():
            labels = r"左侧|左边界|左边|\bleft\b" if location == "Left" else r"右侧|右边界|右边|\bright\b"
            quantity = find_quantity(original, labels, "concentration")
            value = quantity.normalized_value if quantity else None
            mentioned = (location == "Left" and ("左" in original or "left" in lower)) or (location == "Right" and ("右" in original or "right" in lower))
            if mentioned:
                if quantity and quantity.issue:
                    issues.append(f"{location} concentration: {quantity.issue}: {quantity.original}")
                boundary_conditions.append(IntentBoundary(location=location, condition="Fixed concentration", value=value, unit="mol/m³", resolved=value is not None, quantity=asdict(quantity) if quantity else {}))
                if value is None:
                    unknowns.append(f"{location} fixed concentration")
        if any(term in lower for term in ("no flux", "no-flux")) or any(term in original for term in ("无通量", "不要漏", "不漏")):
            boundary_conditions.append(IntentBoundary(location="Top/Bottom", condition="No Flux"))

    if "Heat Transfer in Solids" in physics:
        for location, patterns in {
            "Left": (r"(?:左侧|左边界|左边).{0,20}?(-?[\d.]+(?:e[-+]?\d+)?)\s*K", r"left.{0,24}?(-?[\d.]+(?:e[-+]?\d+)?)\s*K"),
            "Right": (r"(?:右侧|右边界|右边).{0,20}?(-?[\d.]+(?:e[-+]?\d+)?)\s*K", r"right.{0,24}?(-?[\d.]+(?:e[-+]?\d+)?)\s*K"),
        }.items():
            labels = r"左侧|左边界|左边|\bleft\b" if location == "Left" else r"右侧|右边界|右边|\bright\b"
            quantity = find_quantity(original, labels, "temperature")
            value = quantity.normalized_value if quantity else None
            if quantity:
                if quantity.issue:
                    issues.append(f"{location} temperature: {quantity.issue}: {quantity.original}")
                boundary_conditions.append(IntentBoundary(location=location, condition="Temperature", value=value, unit="K", resolved=value is not None, quantity=asdict(quantity)))
        if "绝热" in original or any(term in lower for term in ("thermal insulation", "insulated", "insulate", "adiabatic", "no-flux", "no flux")):
            boundary_conditions.append(IntentBoundary(location="Remaining boundaries", condition="Thermal Insulation"))

    if any("battery" in value.lower() for value in physics):
        if not battery_baseline:
            unknowns.extend(["Geometry", "Electrode definitions", "Material parameters", "Boundary conditions", "Study type"])
        else:
            materials.append("Explicit benchmark-defined generic battery properties")
            boundary_conditions.extend([
                IntentBoundary(location="Negative current collector", condition="Electric Ground"),
                IntentBoundary(location="Positive current collector", condition="Benchmark discharge current density"),
            ])
            outputs.extend(["Cell voltage", "Electrolyte concentration", "Current density"])
        if any(term in lower for term in ("zinc", "zn2+")) or any(term in original for term in ("锌离子", "Zn²⁺")):
            materials.append("Species intent: Zn²⁺ (unconfirmed model input)")
        requested = []
        if any(term in lower for term in ("diffusion", "transport", "concentration")) or any(term in original for term in ("扩散", "传递", "浓度")):
            requested.append("ion transport")
        if any(term in lower for term in ("potential", "current", "conductivity")) or any(term in original for term in ("电势", "电流", "电导率")):
            requested.append("potential/current")
        outputs.extend(requested)

    study: list[str] = []
    if battery_baseline:
        study.extend(["Current Distribution Initialization", "Time Dependent"])
    elif coupling_baseline:
        study.append("Stationary")
    if "稳态" in original or "stationary" in lower or "steady" in lower:
        study.append("Stationary")
    if any(term in lower for term in ("transient", "time dependent", "time-dependent")) or "瞬态" in original or "时间依赖" in original:
        study.append("Time Dependent")
    if any(term in lower for term in ("parameter sweep", "parametric sweep")) or "参数扫描" in original:
        study.append("Parametric Sweep")
    constraints: list[str] = []
    if any(term in lower for term in ("fine mesh", "finer mesh")) or "细网格" in original or "精细网格" in original:
        constraints.append("Mesh size: fine")
    if any(term in lower for term in ("temperature field", "concentration field")) or "温度场" in original or "浓度场" in original:
        outputs.append("Primary field")
    if not physics:
        unknowns.append("Physics interface")
    if not study:
        unknowns.append("Study type")
    if not geometry:
        unknowns.append("Geometry and dimension")

    coupled = "Heat Transfer in Solids" in physics and "Transport of Diluted Species" in physics and any(term in lower for term in ("coupling", "d(t)", "temperature-dependent", "temperature dependent")) or any(term in original for term in ("耦合", "温度依赖"))
    goal = "Heat-to-TDS temperature-dependent diffusion coupling" if coupled else "Battery-related electrochemical transport" if any("battery" in value.lower() for value in physics) else "Stationary diffusion" if "Transport of Diluted Species" in physics and "Stationary" in study else "Heat-transfer analysis" if "Heat Transfer in Solids" in physics else "Uniaxial solid-mechanics analysis" if "Solid Mechanics" in physics else "Primary-current electrochemistry analysis" if "Primary Current Distribution" in physics else "Scientific modeling request"
    confidence: Literal["high", "needs_confirmation", "missing_information"] = "missing_information" if unknowns or issues else "high"
    if confidence == "high" and not boundary_conditions:
        confidence = "needs_confirmation"
    canonical = {
        "goal": goal,
        "physics": physics,
        "geometry": geometry,
        "geometry_quantities": geometry_quantities,
        "materials": materials,
        "parameters": {key: value.model_dump() for key, value in parameters.items()},
        "boundary_conditions": [item.model_dump() for item in boundary_conditions],
        "study": study,
        "outputs": outputs,
        "unknowns": unknowns,
        "validation_issues": issues,
    }
    import json
    return ScientificIntent(
        language=language,
        goal=goal,
        physics=physics,
        geometry=geometry,
        geometry_quantities=geometry_quantities,
        materials=materials,
        parameters=parameters,
        boundary_conditions=boundary_conditions,
        study=study,
        outputs=outputs,
        constraints=constraints,
        unknowns=unknowns,
        validation_issues=issues,
        confidence=confidence,
        canonical_query=json.dumps(canonical, ensure_ascii=False, separators=(",", ":")),
        original_text=original,
    )
