"""Focused contracts for the v0.7 scientific expansion."""

from app.agent.intent_compiler import compile_intent
from app.agent.planner import extract_battery_plan, extract_multiphysics_plan
from app.comsol.battery_worker import expression
from app.knowledge.semantic_runtime import EMBEDDING_MODEL_ID, RERANKER_MODEL_ID, semantic_runtime
from app.scientific.schemas import ScientificPlan
from app.validators import validator_registry


def test_battery_baseline_is_explicit_and_generic_request_is_blocked():
    baseline = ScientificPlan.model_validate(extract_battery_plan("Run the lithium-ion battery verification baseline"))
    assert baseline.workflow_id == "lithium_ion_battery_baseline"
    assert baseline.runnable is True
    assert baseline.battery_cell is not None
    assert validator_registry.get(baseline.workflow_id).precheck(baseline).passed

    generic = ScientificPlan.model_validate(extract_battery_plan("Build a lithium-ion battery"))
    assert generic.runnable is False
    assert generic.missing_information


def test_battery_worker_uses_plan_parameter_expressions():
    plan = extract_battery_plan("Run the lithium-ion battery verification baseline")
    assert expression(plan, "negative_electrode_thickness", {"um"}) == "50[um]"
    assert expression(plan, "cation_transference_number", {"1"}) == "0.4"


def test_multiphysics_plan_has_real_one_way_dependency_contract():
    plan = ScientificPlan.model_validate(
        extract_multiphysics_plan(
            "Run the Heat Transfer and Transport of Diluted Species D(T)=D0*T/T0 verification baseline"
        )
    )
    assert plan.workflow_id == "heat_tds_temperature_coupling"
    assert plan.runnable is True
    assert [(edge.source, edge.target, edge.variables) for edge in plan.couplings] == [
        ("Heat Transfer", "Transport of Diluted Species", ["T"])
    ]
    assert validator_registry.get(plan.workflow_id).precheck(plan).passed


def test_intent_compiler_distinguishes_battery_and_coupling():
    battery = compile_intent("Build a lithium-ion battery")
    assert "Lithium-Ion Battery" in battery.physics
    assert battery.unknowns
    battery_baseline = compile_intent("Run the lithium-ion battery verification baseline")
    assert battery_baseline.unknowns == []
    assert battery_baseline.study == ["Current Distribution Initialization", "Time Dependent"]
    assert battery_baseline.geometry == ["1D negative electrode / separator / positive electrode"]
    coupling = compile_intent(
        "stationary Heat Transfer and Transport of Diluted Species coupling with temperature-dependent D(T)"
    )
    assert coupling.goal == "Heat-to-TDS temperature-dependent diffusion coupling"
    coupling_baseline = compile_intent(
        "Run the Heat Transfer and Transport of Diluted Species D(T)=D0*T/T0 verification baseline"
    )
    assert coupling_baseline.unknowns == []
    assert coupling_baseline.study == ["Stationary"]


def test_semantic_runtime_never_claims_ready_from_files_alone():
    original_verified = semantic_runtime._verified
    original_embedding = semantic_runtime._embedding_state
    original_reranker = semantic_runtime._reranker_state
    try:
        semantic_runtime._verified = None
        semantic_runtime._embedding_state = "Unavailable"
        semantic_runtime._reranker_state = "Unavailable"
        status = semantic_runtime.status()
        assert status["embedding"]["model"] == EMBEDDING_MODEL_ID
        assert status["reranker"]["model"] == RERANKER_MODEL_ID
        assert status["embedding"]["status"] != "Ready"
        assert status["reranker"]["status"] != "Ready"
    finally:
        semantic_runtime._verified = original_verified
        semantic_runtime._embedding_state = original_embedding
        semantic_runtime._reranker_state = original_reranker
