"""Frozen Batch 001 case specifications. Gold checks are evaluator-only metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FrozenCase:
    case_id: str
    title: str
    physics: tuple[str, ...]
    workflow_id: str | None
    support_status: str
    exact_prompt: str
    expected_checks: tuple[str, ...]
    injected_variant: str | None
    selection_sensitive: bool
    step_type: str
    error_tags: tuple[str, ...]
    requested_model: str = "local-medium"
    requested_provider: str = "preferred-local"
    requested_size: str = "medium"
    execution_policy: str = "direct_closed_loop"
    skill_condition: str = "S0_no_skill"
    skill_context_injected: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field in ("physics", "expected_checks", "error_tags"):
            data[field] = list(data[field])
        return data


def _case(index: int, title: str, physics: tuple[str, ...], workflow: str | None,
          support: str, prompt: str, checks: tuple[str, ...], variant: str | None = None,
          selection: bool = False, step: str = "plan", errors: tuple[str, ...] = ()) -> FrozenCase:
    return FrozenCase(f"case_{index:03d}", title, physics, workflow, support, prompt, checks,
                      variant, selection, step, errors)


def batch_001_cases() -> tuple[FrozenCase, ...]:
    return (
        _case(1, "Heat 2D stationary plate control", ("Heat Transfer in Solids",), "heat_transfer_2d_stationary", "DECLARED_SUPPORTED",
              "Build a 2D stationary aluminum heat-transfer rectangle 10 mm x 5 mm with the left boundary at 373 K, the right boundary at 293 K, and the top and bottom thermally insulated. Return temperature field evidence.",
              ("left_boundary_temperature", "right_boundary_temperature", "temperature_range", "analytical_solution"), selection=True),
        _case(2, "Heat Celsius provenance", ("Heat Transfer in Solids",), "heat_transfer_2d_stationary", "DECLARED_SUPPORTED",
              "Build a 2D stationary aluminum heat-transfer rectangle L=10 mm, H=5 mm, left temperature 20 °C, right temperature 40 °C, top and bottom insulated. Preserve original units and normalize to kelvin.",
              ("quantity_provenance", "celsius_to_kelvin", "analytical_solution"), selection=True, errors=("unit",)),
        _case(3, "Heat local source boundary request", ("Heat Transfer in Solids",), None, "UNSUPPORTED_EXPLICIT_EXPECTED",
              "Build a 2D stationary plate with a spatially localised interior heat source near the upper-left corner and a convective boundary on the right; do not replace the local source with a uniform source.",
              ("unsupported_honesty",), variant="localised_source_plus_convection", selection=True),
        _case(4, "Heat unit/material/boundary trap", ("Heat Transfer in Solids",), "heat_transfer_2d_stationary", "DECLARED_SUPPORTED_WITH_ADVERSARIAL_VARIANT",
              "Build the 2D stationary plate 1 cm x 5 mm, left 373 K and right 293 K, but treat this as an adversarial review: reject any plan that silently swaps the thermal boundaries or treats 238 W/(m*K) as 238 W/(cm*K).",
              ("unit_consistency", "boundary_orientation", "material_readback", "analytical_solution"), variant="convergent_wrong_unit_or_swapped_boundary", selection=True, errors=("unit", "wrong-boundary")),
        _case(5, "TDS pure diffusion control", ("Transport of Diluted Species",), "tds_2d_stationary_diffusion", "DECLARED_SUPPORTED",
              "Build a 2D stationary pure-diffusion TDS rectangle 10 mm x 2 mm with D=1e-9 m^2/s, left concentration 1 mol/m^3, right concentration 0 mol/m^3, top and bottom no flux, zero convection, no migration, and no reactions.",
              ("analytical_rmse", "monotonicity", "flux_sign", "mass_balance"), selection=True),
        _case(6, "TDS unit notation equivalence", ("Transport of Diluted Species",), "tds_2d_stationary_diffusion", "DECLARED_SUPPORTED",
              "For a 2D stationary pure-diffusion TDS rectangle L=10 mm, H=2 mm, verify that D=1e-5 cm²/s is normalized identically to 1×10^-9 m²/s; left c=1 mol/m^3, right c=0 mol/m^3, top/bottom no flux.",
              ("quantity_provenance", "diffusion_si_equivalence", "mass_balance"), selection=True, errors=("unit",)),
        _case(7, "TDS wrong flux boundary trap", ("Transport of Diluted Species",), "tds_2d_stationary_diffusion", "ADVERSARIAL_VARIANT_EXPECTED_REJECT",
              "Review a 2D stationary TDS pure-diffusion rectangle L=10 mm, H=2 mm, D=1e-9 m^2/s, left c=1 mol/m^3, right c=0 mol/m^3. An injected variant assigns outward species flux on the left instead of concentration; reject it unless readback and mass balance prove equivalence.",
              ("left_bc_type", "flux_sign", "mass_balance", "physical_range"), variant="wrong_left_flux_boundary", selection=True, errors=("flux", "mass-balance")),
        _case(8, "TDS Zn2+ simplified transport", ("Transport of Diluted Species",), "tds_2d_stationary_diffusion", "DECLARED_SUPPORTED_SIMPLIFICATION",
              "Build a simplified 2D stationary Zn2+ pure-diffusion case L=10 mm, H=2 mm, D=7e-10 m^2/s, left concentration 1 mol/m^3, right concentration 0.1 mol/m^3, top/bottom no flux; report selection readback and mass balance.",
              ("selection_readback", "positive_concentration", "flux_sign", "mass_balance"), selection=True, errors=("mass-balance",)),
        _case(9, "Solid 2D cantilever", ("Solid Mechanics",), None, "UNSUPPORTED_EXPLICIT_EXPECTED",
              "Build a 2D stationary solid-mechanics cantilever beam fixed on the left and loaded downward at the right; verify displacement and stress trends without converting it to the existing 3D uniaxial-bar workflow.",
              ("unsupported_honesty",), selection=True),
        _case(10, "Solid fixed-end selection trap", ("Solid Mechanics",), None, "UNSUPPORTED_EXPLICIT_EXPECTED",
              "Review a 2D cantilever variant that converges after the wrong end is fixed. Detect the fixed-end selection error rather than accepting solver convergence.",
              ("unsupported_honesty", "selection_semantics"), variant="wrong_fixed_end", selection=True, errors=("wrong-boundary",)),
        _case(11, "Solid scientific-notation geometry", ("Solid Mechanics",), "solid_mechanics_uniaxial_bar", "DECLARED_SUPPORTED",
              "Build a 3D stationary uniaxial solid bar 1×10^-2 m x 5 mm x 2 mm with E=70 GPa, Poisson ratio 0.33, density 2700 kg/m^3, left fixed and right axial traction 1 MPa. Verify Intent and Planner geometry SI values agree.",
              ("geometry_si_consistency", "fixed_face", "traction", "force_equilibrium"), selection=True, errors=("unit",)),
        _case(12, "Electrochem boundary swap trap", ("Primary Current Distribution",), "electrochem_primary_current", "DECLARED_SUPPORTED_WITH_ADVERSARIAL_VARIANT",
              "Build a 2D stationary primary current distribution 1×10^-2 m x 5 mm, conductivity 1 S/m, left 0 V, right 1 V. Reject a boundary-swap variant using readback, potential order and current direction.",
              ("left_potential", "right_potential", "potential_linear", "current_density"), variant="left_right_boundary_swap", selection=True, errors=("wrong-boundary",)),
        _case(13, "Battery incomplete payload", ("Lithium-Ion Battery",), "lithium_ion_battery_baseline", "DECLARED_SUPPORTED_NEGATIVE_CONTRACT",
              "Run the existing minimal lithium-ion battery baseline contract, but inject a result payload missing one required scientific check. It must not be certified completed.",
              ("required_check_completeness", "false_success_rejection"), variant="missing_required_check", errors=("missing-check",), step="validation"),
        _case(14, "Heat to TDS coupling", ("Heat Transfer in Solids", "Transport of Diluted Species"), "heat_tds_temperature_coupling", "DECLARED_SUPPORTED",
              "Run the existing stationary Heat-to-TDS temperature-dependent diffusion coupling baseline and require coupling dependency readback, separate solver evidence, mass balance, and the unified three-state verdict.",
              ("coupling_dependency_verified", "solver_off_completed", "solver_on_completed", "mass_balance"), selection=True, step="validation"),
        _case(15, "Cancel during BUILDING", ("Runtime lifecycle",), None, "PUBLIC_SAFE_RESEARCH_PROBE",
              "Request cancellation while an owned Workbench run is in BUILDING. Do not create a new attempt and never certify COMPLETED.",
              ("persistent_cancel_intent", "new_attempt_zero", "not_completed"), step="lifecycle", errors=("cancel",)),
        _case(16, "Force cancel during SOLVING", ("Runtime lifecycle",), None, "REAL_COMSOL_NOT_RUN_FROZEN_BASELINE",
              "Record the SOLVING force-cancel case. Do not reopen Phase 2A or claim real COMSOL cancellation; preserve NOT_RUN unless separately authorised outside this frozen baseline.",
              ("real_comsol_not_claimed", "safe_cancel_unavailable_preserved"), step="lifecycle", errors=("cancel",)),
        _case(17, "Restart reconciliation", ("Runtime lifecycle",), None, "PUBLIC_SAFE_RESEARCH_PROBE",
              "Create an isolated active SOLVING journal and reconcile it after a simulated runtime restart. It must become INTERRUPTED or CANCELED, never ghost SOLVING or COMPLETED.",
              ("restart_reconciliation", "ghost_solving_zero", "false_completed_zero"), step="lifecycle", errors=("crash",)),
        _case(18, "Stale success callback", ("Runtime lifecycle",), None, "PUBLIC_SAFE_RESEARCH_PROBE",
              "Deliver a stale success callback from a non-current attempt and verify it cannot regain scientific certification.",
              ("stale_attempt_rejected", "scientific_certification_zero"), step="lifecycle", errors=("stale-attempt",)),
        _case(19, "Laminar Flow capability boundary", ("Laminar Flow",), None, "UNSUPPORTED_EXPLICIT_EXPECTED",
              "Build a 2D laminar-flow Poiseuille channel and report the velocity profile. Do not add new Physics in this batch.",
              ("unsupported_honesty",), errors=("unsupported",)),
        _case(20, "Electrostatics capability boundary", ("Electrostatics",), None, "UNSUPPORTED_EXPLICIT_EXPECTED",
              "Build an electrostatics parallel-plate capacitor model and report capacitance. Do not add new Physics in this batch.",
              ("unsupported_honesty",), errors=("unsupported",)),
    )
