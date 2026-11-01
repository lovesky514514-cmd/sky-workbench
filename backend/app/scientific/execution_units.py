"""Check the unit actually consumed by each numeric solver input."""

import math

from app.scientific.plan_completion import quantity_kind
from app.scientific.quantity import parse_quantity


EXACT_UNITS = {
    "youngs_modulus": {"Pa"},
    "traction": {"Pa"},
    "poissons_ratio": {"1"},
    "electrolyte_conductivity": {"S/m"},
    "applied_current_density": {"A/m^2"},
    "electrolyte_diffusivity": {"m^2/s"},
    "cation_transference_number": {"1"},
    "negative_electrolyte_volume_fraction": {"1"},
    "negative_solid_volume_fraction": {"1"},
    "positive_electrolyte_volume_fraction": {"1"},
    "positive_solid_volume_fraction": {"1"},
    "negative_solid_conductivity": {"S/m"},
    "positive_solid_conductivity": {"S/m"},
    "negative_solid_diffusivity": {"m^2/s"},
    "positive_solid_diffusivity": {"m^2/s"},
    "negative_rate_constant": {"m/s"},
    "positive_rate_constant": {"m/s"},
}


def plan_unit_issues(plan) -> list[str]:
    issues = []
    for name, item in plan.parameters.items():
        base = name.split("@", 1)[0]
        kind = quantity_kind(base)
        if base.endswith(("_thickness", "_radius")):
            kind = "length"
        if base.endswith("_potential"):
            kind = "potential"
        try:
            numeric = float(item.value)
            if isinstance(item.value, bool) or not math.isfinite(numeric):
                raise ValueError
        except (ValueError, TypeError):
            issues.append(f"{name}: numeric execution input is not finite")
            continue
        if base in EXACT_UNITS:
            if item.unit not in EXACT_UNITS[base]:
                issues.append(f"{name}: incompatible unit {item.unit}")
            continue
        if kind is None:
            issues.append(f"{name}: unit contract is not defined")
            continue
        quantity = parse_quantity(f"{numeric} {item.unit}", kind)
        if quantity.issue or item.quantity.get("issue"):
            issues.append(f"{name}: unresolved or incompatible unit {item.unit}")
            continue
        if item.normalized_value is not None and not math.isclose(
            float(item.normalized_value),
            quantity.normalized_value,
            rel_tol=1e-10,
            abs_tol=1e-18,
        ):
            issues.append(f"{name}: normalized value disagrees with declared unit")
    return issues
