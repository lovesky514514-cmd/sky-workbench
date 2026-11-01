"""Sealed transient diffusion: a separate executable model with a known mass invariant."""

from app.scientific.quantity import find_quantity, valid_geometry
from app.scientific.schemas import ScientificParameter, ScientificPlan

WORKFLOW = "tds_2d_transient_closed"
INITIAL = "mean_concentration+concentration_amplitude*cos(pi*x/length)"
BOUNDARY = "All boundaries: No Flux; no source or reaction"
UNITS = {
    "length": "m",
    "height": "m",
    "diffusion_coefficient": "m^2/s",
    "mean_concentration": "mol/m^3",
    "concentration_amplitude": "mol/m^3",
    "duration": "s",
    "output_interval": "s",
}
BUILD = (
    "geometry",
    "interface",
    "diffusion",
    "initial_profile",
    "sealed_boundaries",
    "parameter_readback",
    "transient_study",
)
SCIENCE = (
    "finite_fields",
    "requested_time",
    "analytical_profile",
    "mass_conservation",
    "physical_range",
    "mesh_agreement",
    "time_agreement",
)


def transient_diffusion_plan(prompt: str) -> dict | None:
    import re
    from app.scientific.request_analysis import positive_mentions

    if any(
        positive_mentions(prompt, (term,))[0]
        for term in ("对流", "反应", "迁移", "convection", "reaction", "migration")
    ):
        return None
    if re.search(
        r"非线性|分层|依赖浓度|依赖温度|D\s*\(|左侧|右侧|左边界|右边界|left boundary|right boundary",
        prompt,
        re.I,
    ):
        return None

    if not (
        re.search(r"扩散|diffusion|\bTDS\b", prompt, re.I)
        and re.search(r"瞬态|随时间|transient", prompt, re.I)
        and re.search(r"全[部边界]*无通量|all.{0,16}no.flux|封闭", prompt, re.I)
        and re.search(
            r"(?:初态|initial profile)\s*(?:=|为|:)?\s*(?:平均浓度\s*加\s*振幅\s*乘\s*)?cos\s*\(\s*(?:pi|π)\s*\*?\s*x\s*/\s*(?:length|L)\s*\)\s*(?=[，,。;；\n]|$)",
            prompt,
            re.I,
        )
        and re.search(r"无源|无反应|no source|no reaction", prompt, re.I)
    ):
        return None
    geometry = valid_geometry(prompt)
    if geometry is None:
        return None
    quantities = {
        name: find_quantity(prompt, labels, kind)
        for name, labels, kind in (
            ("diffusion_coefficient", r"扩散系数|\bD\s*=", "diffusion"),
            ("mean_concentration", r"平均浓度|mean concentration", "concentration"),
            ("concentration_amplitude", r"振幅|amplitude", "concentration"),
            ("duration", r"时长|duration", "time"),
            ("output_interval", r"输出间隔|output interval", "time"),
        )
    }
    if any(q is None or q.issue for q in quantities.values()):
        return None
    quantities.update(zip(("length", "height"), geometry))
    parameters = {
        name: ScientificParameter(
            name=name,
            value=q.normalized_value,
            unit=UNITS[name],
            source="user prompt",
            verified=True,
            status="User Provided",
            original_value=q.original_value,
            original_unit=q.original_unit,
            normalized_value=q.normalized_value,
            normalized_unit=UNITS[name],
        )
        for name, q in quantities.items()
    }
    return ScientificPlan(
        workflow_id=WORKFLOW,
        title="封闭域余弦初态的瞬态扩散",
        research_goal=prompt,
        original_request=prompt,
        physics=[
            "Transport of Diluted Species",
            "Transient constant isotropic diffusion; no convection, migration or reactions",
        ],
        geometry=["2D rectangle defined by length and height"],
        materials=["Explicit constant diffusion coefficient"],
        parameters=parameters,
        boundary_conditions=[BOUNDARY],
        study=["Time Dependent"],
        initial_conditions=[
            {"tag": "init1", "kind": "concentration", "values": {"c": INITIAL}}
        ],
        mesh_strategy=["Three independent mesh levels and a refined time solve"],
        solver=["COMSOL transient BDF"],
        validation=[
            "Read real concentration field",
            "Compare cosine-decay solution",
            "Check total mass and convergence",
        ],
        expected_outputs=[
            "COMSOL MPH model",
            "Concentration field CSV",
            "Mass-time CSV",
            "Validation JSON",
        ],
        runnable=True,
    ).model_dump(mode="json")
