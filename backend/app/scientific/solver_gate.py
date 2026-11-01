"""Common preflight before numeric input reaches a supported solver."""

from app.scientific.execution_units import plan_unit_issues
from app.scientific.model_features import adapter_scope_issues
from app.scientific.engine_intent import workflow_engine_issue


COOLING_BOUNDARY = "All six faces: convection to ambient_temperature and grey-body radiation; no contact heat transfer"


def solver_input_issues(plan) -> list[str]:
    issues = plan_unit_issues(plan) + adapter_scope_issues(plan)
    engine_issue = workflow_engine_issue(
        plan.workflow_id, plan.original_request or plan.research_goal
    )
    if engine_issue:
        issues.append(engine_issue)
    brief = plan.modeling_brief
    if brief and (
        brief.model_outline.get("object_scope") == "multiple_entities_requires_binding"
        or brief.relations
    ):
        issues.append("This executor does not implement the scoped multi-object model")
    if plan.workflow_id == "heat_transfer_3d_cooling":
        property_names = {
            "property_reference_temperature",
            "thermal_conductivity_temperature_coefficient",
            "heat_capacity_temperature_coefficient",
        }
        if plan.cooling_model.property_law == "linear_temperature":
            if property_names - set(plan.parameters):
                issues.append(
                    "Temperature-dependent properties require reference temperature and both coefficients"
                )
        elif property_names & set(plan.parameters):
            issues.append(
                "Constant-property executor cannot silently ignore temperature coefficients"
            )
        if plan.boundary_conditions != [COOLING_BOUNDARY]:
            issues.append(
                "Cooling boundary differs from the implemented all-face equation"
            )
        if brief and any(
            word in brief.exclusions
            for word in ("对流", "辐射", "convection", "radiation")
        ):
            issues.append(
                "Cooling equation conflicts with an explicit mechanism exclusion"
            )
        if (
            plan.functions
            or plan.variables
            or plan.boundary_definitions
            or plan.initial_conditions
        ):
            issues.append(
                "Cooling executor cannot ignore additional functions or conditions"
            )
    if plan.workflow_id == "tds_2d_transient_closed":
        if (
            plan.functions
            or plan.variables
            or plan.boundary_definitions
            or plan.couplings
        ):
            issues.append(
                "Sealed diffusion executor cannot ignore additional functions, boundaries or couplings"
            )
    return issues


def require_solver_inputs(plan) -> None:
    issues = solver_input_issues(plan)
    if issues:
        raise ValueError("Solver input contract rejected: " + "; ".join(issues))
