"""Requested model features are constraints on an adapter's physical scope."""

import re

FEATURES = {
    "variable_geometry": r"变截面|截面.{0,12}随(?:位置|长度)|tapered|nonuniform.cross.section",
    "dynamic_mechanics": r"固有频率|模态|振动|eigenfrequency|\bmodal\b|vibration|harmonic",
    "body_force": r"重力|自重|离心|\bgravity\b|\bcentrifugal\b|body.force|inertial.force",
    "volumetric_heat": r"体热源|内热源|volumetric.heat|internal.heat.source",
    "transient": r"瞬态|暂态|随时间|transient|time.dependent|time.varying",
    "variable_coefficient": r"(?:扩散系数|热导率|导热系数|比热|密度).{0,12}(?:随(?:空间|位置|温度|浓度)|依赖(?:空间|位置|温度|浓度)|非常数)|\bD\s*\(\s*[xycT]|spatially.varying|concentration.dependent|temperature.dependent",
    "layered": r"分层|多层|layered|multilayer",
    "nonlinear_material": r"非线性|nonlinear|塑性|plasticity|超弹性|hyperelastic",
    "multiple_species": r"两种物质|多物种|多组分|two species|multiple species|multicomponent|coupled diffusion",
    "thermal_mechanics": r"热应力|热膨胀|热变形|thermal stress|thermal expansion",
    "phase_change": r"相变|熔化|熔融|phase change|melting",
}


def requested_model_features(text: str) -> list[str]:
    from app.scientific.request_analysis import positive_mentions

    return [
        key
        for key, pattern in FEATURES.items()
        if any(
            positive_mentions(text, (match.group(),))[0]
            for match in re.finditer(pattern, text, re.I)
        )
    ]


def adapter_scope_issues(plan) -> list[str]:
    features = set(
        (plan.modeling_brief.model_outline.get("model_features") or [])
        if plan.modeling_brief
        else []
    )
    features.update(
        requested_model_features(plan.original_request or plan.research_goal)
    )
    forbidden = {
        "tds_2d_stationary_diffusion": {
            "transient",
            "variable_coefficient",
            "layered",
            "nonlinear_material",
            "multiple_species",
        },
        "tds_2d_transient_closed": {
            "variable_coefficient",
            "layered",
            "nonlinear_material",
            "multiple_species",
        },
        "heat_transfer_2d_stationary": {
            "dynamic_mechanics",
            "volumetric_heat",
            "transient",
            "variable_coefficient",
            "layered",
            "nonlinear_material",
            "thermal_mechanics",
            "phase_change",
        },
        "solid_mechanics_uniaxial_bar": {
            "variable_geometry",
            "dynamic_mechanics",
            "body_force",
            "transient",
            "layered",
            "nonlinear_material",
            "thermal_mechanics",
            "phase_change",
        },
        "heat_transfer_3d_cooling": {"layered", "thermal_mechanics", "dynamic_mechanics", "phase_change"},
    }.get(plan.workflow_id, set())
    if (
        plan.workflow_id == "heat_transfer_3d_cooling"
        and plan.cooling_model.property_law == "constant"
    ):
        forbidden = forbidden | {"variable_coefficient", "nonlinear_material"}
    return [
        f"{feature}: outside {plan.workflow_id} physical scope"
        for feature in sorted(features & forbidden)
    ]
