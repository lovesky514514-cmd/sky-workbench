"""Query vocabulary belongs to domain adapters, not ranking algorithms."""

DOMAIN_ALIASES = {
    "java api": "model component geom feature create selection set getType Java API",
    "heat transfer": "Heat Transfer in Solids thermal insulation temperature heat flux",
    "thermal insulation": "Thermal Insulation no heat flux boundary",
    "solid mechanics": "Solid Mechanics solid stress strain boundary load fixed constraint",
    "transport of diluted species": "Transport of Diluted Species concentration flux diffusion",
    "tds": "Transport of Diluted Species concentration diffusion",
    "solver": "solver convergence stationary time dependent segregated fully coupled",
    "mesh": "mesh element quality refinement physics controlled mesh",
    "validation": "validation convergence mesh independence verification result",
    "troubleshooting": "troubleshooting error warning failed convergence license",
    "version": "version release notes legacy current applies to versions",
    "绝热": "Thermal Insulation no heat flux boundary",
    "传热": "Heat Transfer in Solids temperature heat flux",
    "固体力学": "Solid Mechanics stress strain boundary load",
    "稀物质": "Transport of Diluted Species concentration diffusion",
    "网格": "mesh element quality refinement",
    "求解器": "solver convergence stationary time dependent",
    "验证": "validation verification convergence mesh independence",
    "故障": "troubleshooting error warning failed convergence",
}


def expand_query(query: str) -> str:
    """Add narrow scientific aliases without changing the user's visible query."""

    lower = query.lower()
    additions: list[str] = []
    if "thermal insulation" in lower or "绝热" in query or "不散热" in query:
        additions.append("zero normal heat flux thermally insulated temperature gradient")
    if "heat transfer in solids" in lower:
        additions.append("conductive heat flux boundary")
    if (("铝" in query and "热导" in query) or ("aluminum" in lower and "thermal conductivity" in lower)):
        additions.append("aluminum thermal_conductivity thermal conductivity W m K assumption alloy-specific verify")
    return " ".join((query, *additions))


def scientific_bonus(query: str, text: str) -> float:
    """Prefer passages that define a queried named scientific feature over index/table mentions."""

    lower_query = query.lower()
    lower_text = text.lower()
    bonus = 0.0
    if "thermal insulation" in lower_query or "绝热" in query or "不散热" in query:
        if "thermal insulation" in lower_text:
            bonus += 0.25
        if "no heat flux across the boundary" in lower_text:
            bonus += 0.55
    if (("铝" in query and "热导" in query) or ("aluminum" in lower_query and "thermal conductivity" in lower_query)):
        if "thermal_conductivity" in lower_text and "assumption" in lower_text:
            bonus += 0.5
    return bonus

