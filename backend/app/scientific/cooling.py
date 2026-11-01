"""Reviewable constant-property iron cooling case, separate from the 2D benchmark."""

from __future__ import annotations

import re
from dataclasses import asdict

from app.scientific.quantity import valid_geometry, find_quantity
from app.scientific.schemas import PlanCompletion, ScientificParameter, ScientificPlan
from app.scientific.request_analysis import analyze_request
from app.scientific.general_planning import enrich_plan

WORKFLOW = "heat_transfer_3d_cooling"
UNITS = {
    "width": "m",
    "height": "m",
    "length": "m",
    "initial_temperature": "K",
    "ambient_temperature": "K",
    "thermal_conductivity": "W/(m*K)",
    "density": "kg/m^3",
    "heat_capacity": "J/(kg*K)",
    "heat_transfer_coefficient": "W/(m^2*K)",
    "emissivity": "1",
    "duration": "s",
    "output_interval": "s",
}
DEFAULTS = {
    "initial_temperature": 1073.15,
    "ambient_temperature": 298.15,
    "thermal_conductivity": 45.0,
    "density": 7870.0,
    "heat_capacity": 650.0,
    "heat_transfer_coefficient": 10.0,
    "emissivity": 0.8,
    "duration": 300.0,
    "output_interval": 1.0,
}
REASONS = {
    "initial_temperature": "“烧红”不等于已知温度；800 °C 只是可修改的探索起点。",
    "ambient_temperature": "暂以 25 °C 空气及等温辐射环境为探索条件，可修改。",
    "thermal_conductivity": "45 W/(m·K) 为未校准的常数近似，不代表此铁块的实测性质。",
    "density": "7870 kg/m³ 为探索近似，具体成分、温度和状态仍需校准。",
    "heat_capacity": "650 J/(kg·K) 为未校准常数；未计温度依赖、磁性转变或相变。",
    "heat_transfer_coefficient": "10 W/(m²·K) 为静止空气换热的探索假设，不是计算得到的自然对流系数。",
    "emissivity": "0.8 为表面发射率假设，氧化、粗糙度和涂层会影响该值。",
    "duration": "先观察 300 s，可按研究目标修改。",
    "output_interval": "先每 1 s 输出，求解器仍使用自适应内部时间步。",
}


def is_iron_cooling(prompt: str) -> bool:
    brief = analyze_request(prompt)
    materials = {fact.value for fact in brief.facts if fact.key.startswith("material:")}
    known = {fact.key for fact in brief.facts if fact.normalized}
    geometry = valid_geometry(prompt, 3)
    # Missing dimensions can be proposed for a named block. Explicit partial or
    # invalid dimensions remain unresolved rather than being replaced by defaults.
    missing_block_geometry = bool(re.search(r"铁块|iron\s+block", prompt, re.I)) and not any(
        f.key in {"width", "height", "length"}
        or f.unit in {"m", "cm", "mm", "um", "nm"}
        for f in brief.facts
    )
    return (
        materials == {"Iron"}
        and (geometry is not None or missing_block_geometry)
        and "thermal" in brief.concepts
        and not known & {"left_temperature", "right_temperature"}
        and (
            brief.model_outline["thermal_action"] == "cooling"
            or brief.model_outline["initial_state"] == "hot_unspecified"
            or {"initial_temperature", "ambient_temperature"} <= known
        )
    )


def cooling_plan(prompt: str) -> dict | None:
    if not is_iron_cooling(prompt):
        return None
    variable_properties = bool(
        re.search(
            r"温度相关|物性随温度|temperature.dependent|alpha_k|alpha_Cp", prompt, re.I
        )
    )
    coefficients = {}
    if variable_properties:
        if not re.search(r"线性|linear", prompt, re.I):
            return None
        for name, label, kind in (
            (
                "property_reference_temperature",
                r"T_ref\s*=|物性参考温度",
                "temperature",
            ),
            (
                "thermal_conductivity_temperature_coefficient",
                r"alpha_k\s*=|热导率温度系数",
                "inverse_temperature",
            ),
            (
                "heat_capacity_temperature_coefficient",
                r"alpha_Cp\s*=|比热温度系数",
                "inverse_temperature",
            ),
        ):
            q = find_quantity(prompt, label, kind)
            if q is None or q.issue:
                return None
            coefficients[name] = ScientificParameter(
                name=name,
                value=q.normalized_value,
                unit=q.si_unit,
                source="user prompt",
                status="User Provided",
                verified=True,
                quantity=asdict(q),
            )
    dimensions = valid_geometry(prompt, 3) or ()
    parameters = {
        name: ScientificParameter(
            name=name,
            symbol=name,
            value=q.normalized_value,
            unit="m",
            original_value=q.original_value,
            original_unit=q.original_unit,
            source="user prompt",
            status="User Provided",
            quantity=asdict(q),
            verified=True,
        )
        for name, q in zip(("width", "height", "length"), dimensions)
    }
    proposed = {
        name: ScientificParameter(
            name=name,
            symbol=name,
            value=value,
            unit=UNITS[name],
            source="Workbench exploratory proposal; unverified",
            assumption=True,
            rationale=REASONS[name],
            applicability="常物性、无支撑接触、空气对流+灰体辐射的探索模型。",
        )
        for name, value in DEFAULTS.items()
    }
    if not dimensions:
        for name, value in {"width": 0.003, "height": 0.003, "length": 0.02}.items():
            proposed[name] = ScientificParameter(
                name=name,
                value=value,
                unit="m",
                source="Workbench exploratory geometry; unverified",
                assumption=True,
                rationale="原文未提供尺寸；此值仅为可修改的探索起点，不能当作用户尺寸。",
                applicability="长方体近似需要审核，真实尺寸确定后需重新求解。",
            )
    reason = (
        "建议先按空气中自由冷却计算三维温度随时间变化；冷却介质和所有建议值均需审核。"
    )
    decisions = {
        "boundary_conditions": [
            "All six faces: convection to ambient_temperature and grey-body radiation; no contact heat transfer"
        ],
        "study": ["Time Dependent"],
        "mesh_strategy": ["Three independent tetrahedral meshes and a refined time-step solve"],
        "solver": ["COMSOL time-dependent heat conduction"],
        "expected_outputs": [
            "3D temperature field",
            "Temperature-time CSV",
            "COMSOL MPH model",
            "Numerical validation JSON",
        ],
    }
    if not dimensions:
        decisions["geometry"] = [
            "Exploratory 3D rectangular block defined by reviewed width, height and length"
        ]
    incompatible = any(
        term in prompt.lower()
        for term in ("水", "油", "真空", "water", "oil", "vacuum", "持续加热")
    )
    remaining = ["Required decision: boundary_conditions"] if incompatible else []
    if incompatible:
        decisions.pop("boundary_conditions")
        reason = (
            "已识别三维铁块，但当前执行器只支持空气冷却；请明确选择空气模型或保留草案。"
        )
    plan = ScientificPlan(
        workflow_id=WORKFLOW,
        title="三维铁块瞬态冷却",
        research_goal=prompt,
        original_request=prompt,
        physics=["Heat Transfer in Solids"],
        geometry=["3D block: " + " × ".join(q.original for q in dimensions)]
        if dimensions
        else ["3D block proposal; dimensions require review"],
        materials=["Iron; uncalibrated constant-property approximation"],
        parameters=parameters,
        validation=[
            "Check geometry/units, energy balance, small-Biot reference and mesh agreement"
        ],
        missing_information=[
            "Review proposed temperatures, properties, cooling environment and time range"
        ],
        warnings=[
            "Exploratory constant-property model; no phase changes, oxidation kinetics, contact conduction or resolved air flow."
        ],
        completion=PlanCompletion(
            source="example",
            parameters=proposed,
            decisions=decisions,
            reason=reason,
            applicability="只验证所声明的简化方程；不作为真实铁块冷却预测的实验认证。",
            remaining_missing=remaining,
        ),
        steps=[
            {
                "phase": "Build",
                "label": "Build the 3D block and read back all six faces",
            },
            {
                "phase": "Solve",
                "label": "Solve transient convection and radiation cooling",
            },
            {"phase": "Validate", "label": "Check energy, time and mesh consistency"},
        ],
    )
    if variable_properties:
        plan.cooling_model.property_law = "linear_temperature"
        plan.parameters.update(coefficients)
        plan.title = "三维块体冷却 · 线性温度相关物性"
        plan.completion.applicability = (
            "采用明确系数的线性 k(T)/Cp(T) 近似；需要独立材料数据校准，不包含相变。"
        )
        plan.warnings = [
            "Linear temperature-dependent properties are an exploratory approximation, not calibrated material data."
        ]
    return enrich_plan(plan, prompt).model_dump(mode="json")
