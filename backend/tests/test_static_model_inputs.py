"""Use only the properties consumed by a stationary model; keep its scope narrow."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from app.agent.planner import ScientificPlanner
from app.scientific.quantity import valid_geometry
from app.scientific.schemas import ScientificPlan
from app.scientific.solver_gate import solver_input_issues
from app.validators import validator_registry


CASES = json.loads(
    (Path(__file__).parent / "fixtures/modeling_pairs_25.json").read_text(
        encoding="utf-8"
    )
)


class Offline:
    async def complete(self, *args, **kwargs):
        return None


def plan(prompt):
    payload, _, _ = asyncio.run(ScientificPlanner(Offline()).plan(prompt, []))
    return ScientificPlan.model_validate(payload)


def test_source_free_stationary_heat_needs_no_invented_mass_or_capacity():
    model = plan(CASES[4]["explicit"])
    assert model.runnable and model.completion is None
    assert "density" not in model.parameters and "heat_capacity" not in model.parameters
    assert not solver_input_issues(model)
    assert validator_registry.get(model.workflow_id).precheck(model).passed


def test_split_section_static_bar_is_ready_without_density():
    model = plan(CASES[6]["explicit"])
    assert model.runnable and model.workflow_id == "solid_mechanics_uniaxial_bar"
    assert model.parameters["length"].value == 0.05
    assert (
        model.parameters["width"].value == 0.002
        and model.parameters["height"].value == 0.003
    )
    assert (
        model.parameters["traction"].value == 1e7 and "density" not in model.parameters
    )
    assert not solver_input_issues(model)
    assert validator_registry.get(model.workflow_id).precheck(model).passed


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("杆长 8 cm，矩形截面 3×4 mm", [0.08, 0.003, 0.004]),
        ("length 0.4 m; cross-section 5 mm x 3 mm", [0.4, 0.005, 0.003]),
        ("截面 0.5 cm×0.2 cm，长度 60 mm", [0.06, 0.005, 0.002]),
    ],
)
def test_section_binding_preserves_units_axis_roles_and_literal_spans(prompt, expected):
    quantities = valid_geometry(prompt, 3)
    assert quantities and [q.normalized_value for q in quantities] == expected
    assert all(prompt[slice(*q.original_span)] == q.original for q in quantities)


@pytest.mark.parametrize(
    "prompt", ["长 2 cm，截面 3×-4 mm", "长 3 cm，长 4 cm，截面 1×2 mm"]
)
def test_invalid_or_ambiguous_split_geometry_cannot_be_executed(prompt):
    assert valid_geometry(prompt, 3) is None


@pytest.mark.parametrize("suffix", ["考虑重力", "consider gravity"])
def test_static_bar_cannot_silently_ignore_a_density_dependent_body_load(suffix):
    assert plan(CASES[6]["explicit"] + "，" + suffix).workflow_id is None


def test_stationary_conduction_cannot_silently_discard_volumetric_heat():
    assert plan(CASES[4]["explicit"] + "，有体热源 100 W/m^3").workflow_id is None


def test_transient_heat_still_requires_density_and_capacity_review():
    model = plan("3×3×20 mm 的烧红铁块在空气中冷却")
    assert model.completion and {"density", "heat_capacity"} <= set(
        model.completion.parameters
    )


def test_runtime_revision_changes_with_the_separate_math_worker(tmp_path, monkeypatch):
    revision = importlib.import_module("app.source_revision")
    backend = tmp_path / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "app/example.py").write_text("x=1", encoding="utf-8")
    for name in ("mathics_worker.py", "desktop_runtime.py"):
        (backend / name).write_text("x=1", encoding="utf-8")
    monkeypatch.setattr(revision, "__file__", str(backend / "app/source_revision.py"))
    revision.source_revision.cache_clear()
    try:
        before = revision.source_revision()["source_tree_sha256"]
        (backend / "mathics_worker.py").write_text("x=2", encoding="utf-8")
        revision.source_revision.cache_clear()
        assert revision.source_revision()["source_tree_sha256"] != before
    finally:
        revision.source_revision.cache_clear()


def test_resource_estimate_counts_interfaces_without_constitutive_description():
    from app.scientific.resource_guard import assess_risk, GIB

    model = plan(CASES[6]["explicit"])
    profile = {
        "memory": {"total_bytes": 32 * GIB, "available_bytes": 24 * GIB},
        "disk": {"free_bytes": 50 * GIB},
    }
    report = assess_risk(model, profile)
    assert report["estimate"]["physics_count"] == 1 and report["allowed"]
    assert report["estimate"]["geometry_dimension"] == 3
    model.physics.append("More description of the same constitutive law")
    assert assess_risk(model, profile)["estimate"] == report["estimate"]
    # Real mesh telemetry retains the existing high/critical approval boundary.
    assert not assess_risk(model, profile, stage="post_mesh", dof=5_000_000)["allowed"]


def test_cooling_resource_estimate_uses_executor_dimension_and_requested_times():
    from app.scientific.plan_completion import accept_completion
    from app.scientific.resource_guard import _estimate_complexity

    draft = plan("3×3×20 mm 的烧红铁块在空气中冷却")
    reviewed = accept_completion(draft, draft.completion.proposal_id, {}, mode="auto")
    reviewed.geometry_features = []
    reviewed.study_definitions = []
    reviewed.geometry = ["长方体"]
    estimate = _estimate_complexity(reviewed)
    assert estimate["geometry_dimension"] == 3
    assert estimate["transient_step_count"] == 301
    assert estimate["geometry_dimension_source"] == "registered_executor"
