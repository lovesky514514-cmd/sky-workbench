import asyncio
from pathlib import Path

from app.agent.intent_compiler import compile_intent
from app.agent.model_card import format_result_metrics
from app.agent.tool_registry import ToolRegistry
from app.agent.planner import extract_electrochem_plan, extract_solid_plan
from app.scientific.modeling_engine import ModelingEngine, validate_coupling_dag
from app.scientific.resource_guard import GIB, RiskLevel, assess_risk, hardware_profile, runtime_thresholds
from app.scientific.schemas import CouplingEdge, ScientificPlan


SOLID = "Create a 3D Solid Mechanics uniaxial bar, 1 m x 0.1 m x 0.1 m, E=200 GPa, Poisson's ratio=0.3, density=7850 kg/m^3, fixed left face, traction=1 MPa on the right face, stationary."
ELECTROCHEM = "Create a stationary Primary Current Distribution electrochemistry model, rectangle 1 m x 0.1 m, electrolyte conductivity=5 S/m, left potential 0 V and right potential 1 V."


def profile(*, available_gb: float = 16, total_gb: float = 32, disk_gb: float = 100):
    return {
        "memory": {"available_bytes": int(available_gb * GIB), "total_bytes": int(total_gb * GIB), "available_percent": available_gb / total_gb * 100},
        "disk": {"free_bytes": int(disk_gb * GIB), "free_gb": disk_gb},
    }


def plan(**updates):
    data = {"title": "resource test", "research_goal": "test", "physics": ["Solid Mechanics"], "geometry": ["3D block"], "study": ["Stationary"]}
    data.update(updates)
    return ScientificPlan(**data)


def test_hardware_profile_never_fabricates_gpu():
    result = hardware_profile(Path.cwd(), {"runtime": 999_999_999})
    assert result["cpu"]["logical_threads"]
    assert result["memory"]["status"] in {"available", "unavailable"}
    assert result["disk"]["free_bytes"] > 0
    assert result["gpu"]["status"] == "unavailable"
    assert result["process_memory"]["runtime"]["status"] == "unavailable"


def test_resource_stage_a_is_explainable_and_high_requires_override():
    risky = plan(geometry_features=[{"tag": "b1", "kind": "block", "dimension": 3}], mesh_definition={"level": "fine"})
    assessment = assess_risk(risky, profile(available_gb=2), stage="prebuild")
    assert assessment["risk_level"] in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    assert not assessment["allowed"]
    assert assessment["reasons"] and assessment["suggestions"]
    risky.resource_policy.allow_critical_risk = True
    risky.resource_policy.allow_high_risk = True
    assert assess_risk(risky, profile(available_gb=2))["allowed"]


def test_post_mesh_dof_overrides_stage_a_estimate():
    assessment = assess_risk(plan(), profile(), stage="post_mesh", mesh_elements=700_000, dof=5_000_000)
    assert assessment["estimate"]["estimated_dof"] == 5_000_000
    assert assessment["estimate"]["mesh_elements"] == 700_000
    assert assessment["risk_level"] == RiskLevel.CRITICAL


def test_runtime_thresholds_warn_and_critical_without_killing():
    result = runtime_thresholds(profile(available_gb=1, total_gb=32, disk_gb=0.5), plan())
    assert result["highest"] == "critical"
    assert len(result["events"]) == 2


def test_generic_geometry_selection_study_and_dag_validation():
    candidate = plan(
        geometry_features=[{"tag": "b1", "kind": "block", "dimension": 3}],
        selections=[{"tag": "left", "entity_dimension": 2, "method": "explicit", "entities": []}],
        boundary_definitions=[{"tag": "fix", "kind": "Fixed", "selection": "missing"}],
        study_definitions=[{"tag": "time", "kind": "time_dependent"}],
        couplings=[{"source": "solid", "target": "heat"}, {"source": "heat", "target": "solid"}],
    )
    errors = ModelingEngine().validate_plan(candidate)
    assert any("empty" in item for item in errors)
    assert any("unknown selection" in item for item in errors)
    assert any("requires explicit times" in item for item in errors)
    assert any("cycle" in item for item in errors)


def test_sweep_case_count_and_acyclic_coupling():
    candidate = plan(study_definitions=[{"tag": "sweep", "kind": "parametric_sweep", "sweep_parameters": {"a": [1, 2], "b": [3, 4, 5]}}])
    assert candidate.study_definitions[0].case_count() == 6
    assert validate_coupling_dag([CouplingEdge(source="heat", target="solid")]) == []


def test_solid_intent_and_plan_are_explicit_and_runnable():
    intent = compile_intent(SOLID)
    assert "Solid Mechanics" in intent.physics
    parsed = ScientificPlan.model_validate(extract_solid_plan(SOLID))
    assert parsed.runnable and parsed.workflow_id == "solid_mechanics_uniaxial_bar"
    assert float(parsed.parameters["youngs_modulus"].value) == 200e9
    assert parsed.geometry_features[0].kind == "block"


def test_electrochem_intent_and_plan_are_explicit_and_runnable():
    intent = compile_intent(ELECTROCHEM)
    assert "Primary Current Distribution" in intent.physics
    parsed = ScientificPlan.model_validate(extract_electrochem_plan(ELECTROCHEM))
    assert parsed.runnable and parsed.workflow_id == "electrochem_primary_current"
    assert float(parsed.parameters["electrolyte_conductivity"].value) == 5


def test_battery_schema_requires_provenance_objects():
    candidate = plan(battery_cell={"chemistry": "unspecified demonstration chemistry", "geometry": "1D layered cell", "parameters": {"capacity": {"name": "capacity", "value": 1, "unit": "Ah", "source": "user prompt", "status": "User Provided"}}})
    assert candidate.battery_cell is not None
    assert candidate.battery_cell.parameters["capacity"].source == "user prompt"
    assert candidate.battery_cell.module_status == "UNVERIFIED"


def test_intent_recognizes_transient_sweep_and_fine_mesh_without_guessing_values():
    intent = compile_intent("Create a 3D transient Solid Mechanics parameter sweep with a fine mesh")
    assert intent.study == ["Time Dependent", "Parametric Sweep"]
    assert intent.constraints == ["Mesh size: fine"]


def test_normal_cancel_never_force_terminates_an_unsupported_comsol_worker():
    class ActiveProcess:
        returncode = None
        pid = 42
        terminated = False

        def terminate(self):
            self.terminated = True

    registry = ToolRegistry()
    process = ActiveProcess()
    registry._active_processes["session"] = process  # isolated contract test
    result = asyncio.run(registry.cancel("session"))
    assert result["status"] == "safe_cancel_unavailable"
    assert result["cancelled"] is False
    assert process.terminated is False


def test_model_card_uses_real_solid_and_electrochem_metric_keys():
    solid = format_result_metrics({"right_displacement_m": 5e-6, "axial_stress_Pa": 1e6, "von_mises_stress_Pa": 1e6, "internal_force_N": 1e4, "expected_force_N": 1e4})
    electrochem = format_result_metrics({"potential_min_V": 0.0, "potential_max_V": 1.0, "potential_rmse_V": 1e-12, "current_density_mean_A_per_m2": 5.0, "expected_current_density_A_per_m2": 5.0})
    assert "1000000.0 / 1000000.0 Pa" in solid
    assert "Current density: 5.0 A/m^2; expected 5.0" in electrochem
