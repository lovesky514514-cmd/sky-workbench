"""Small declarative modeling core shared by physics-specific executors.

This module validates intent and dependencies.  It deliberately does not
pretend to execute COMSOL: only an adapter backed by real API readback may
promote a capability to VERIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.scientific.schemas import CouplingEdge, ScientificPlan


class CapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    EXPERIMENTAL = "EXPERIMENTAL"
    MODULE_MISSING = "MODULE_MISSING"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class PhysicsAdapter(Protocol):
    workflow_id: str

    def build(self, plan: ScientificPlan, context: dict[str, Any]) -> dict[str, Any]: ...

    def readback(self, context: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class AdapterRegistration:
    adapter: PhysicsAdapter | None
    status: CapabilityStatus
    reason: str


class ModelingEngine:
    def __init__(self) -> None:
        self._adapters: dict[str, AdapterRegistration] = {}

    def register(
        self,
        workflow_id: str,
        adapter: PhysicsAdapter | None,
        status: CapabilityStatus,
        reason: str,
    ) -> None:
        if status == CapabilityStatus.VERIFIED and adapter is None:
            raise ValueError("A VERIFIED capability requires a real adapter")
        self._adapters[workflow_id] = AdapterRegistration(adapter, status, reason)

    def capability_matrix(self) -> dict[str, dict[str, str]]:
        return {
            name: {"status": item.status.value, "reason": item.reason}
            for name, item in sorted(self._adapters.items())
        }

    def validate_plan(self, plan: ScientificPlan) -> list[str]:
        errors: list[str] = []
        feature_tags: set[str] = set()
        for feature in plan.geometry_features:
            if feature.tag in feature_tags:
                errors.append(f"Duplicate geometry feature tag: {feature.tag}")
            feature_tags.add(feature.tag)
            if feature.kind in {"rectangle", "circle", "ellipse", "polygon"} and feature.dimension != 2:
                errors.append(f"{feature.kind} must be 2D: {feature.tag}")
            if feature.kind in {"block", "cylinder", "sphere"} and feature.dimension != 3:
                errors.append(f"{feature.kind} must be 3D: {feature.tag}")
            missing_inputs = [tag for tag in feature.inputs if tag not in feature_tags]
            if missing_inputs:
                errors.append(f"Geometry feature {feature.tag} has unknown/forward inputs: {missing_inputs}")

        selections = {item.tag: item for item in plan.selections}
        if len(selections) != len(plan.selections):
            errors.append("Selection tags must be unique")
        geometry_dimension = max((item.dimension for item in plan.geometry_features), default=None)
        for selection in plan.selections:
            if not selection.allow_empty and selection.method == "explicit" and not selection.entities:
                errors.append(f"Selection {selection.tag} is empty")
            if geometry_dimension is not None and selection.entity_dimension > geometry_dimension:
                errors.append(
                    f"Selection {selection.tag} entity dimension {selection.entity_dimension} exceeds geometry dimension {geometry_dimension}"
                )

        for condition in [*plan.initial_conditions, *plan.boundary_definitions]:
            if condition.selection and condition.selection not in selections:
                errors.append(f"Condition {condition.tag} references unknown selection {condition.selection}")
        for material in plan.material_definitions:
            if material.selection and material.selection not in selections:
                errors.append(f"Material {material.tag} references unknown selection {material.selection}")

        errors.extend(validate_coupling_dag(plan.couplings))
        for study in plan.study_definitions:
            if study.kind == "time_dependent" and not study.times:
                errors.append(f"Time-dependent study {study.tag} requires explicit times")
            if study.kind == "parametric_sweep" and not study.sweep_parameters:
                errors.append(f"Parametric sweep {study.tag} requires sweep parameters")
        return errors


def validate_coupling_dag(edges: list[CouplingEdge]) -> list[str]:
    graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.source == edge.target:
            return [f"Coupling self-cycle is not allowed: {edge.source}"]
        graph.setdefault(edge.source, set()).add(edge.target)
        graph.setdefault(edge.target, set())

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return ["Coupling graph contains a cycle"] if any(visit(node) for node in graph) else []


modeling_engine = ModelingEngine()
modeling_engine.register("heat_transfer_2d_stationary", None, CapabilityStatus.UNVERIFIED, "Registered by legacy real executor at runtime")
modeling_engine.register("tds_2d_stationary_diffusion", None, CapabilityStatus.UNVERIFIED, "Registered by legacy real executor at runtime")
modeling_engine.register("solid_mechanics_uniaxial_bar", None, CapabilityStatus.UNVERIFIED, "Real COMSOL acceptance pending")
modeling_engine.register("electrochemistry", None, CapabilityStatus.UNVERIFIED, "Licensed module probe pending")
modeling_engine.register("lithium_ion_battery_baseline", None, CapabilityStatus.UNVERIFIED, "Real runtime acceptance is reported by the capability endpoint")
modeling_engine.register("battery", None, CapabilityStatus.UNVERIFIED, "Real runtime acceptance is reported by the capability endpoint")
modeling_engine.register("general_modeling_engine", None, CapabilityStatus.UNVERIFIED, "Real COMSOL primitive/boolean/study acceptance pending")
modeling_engine.register("multiphysics_coupling", None, CapabilityStatus.UNVERIFIED, "DAG validation is available; no real coupled benchmark is verified")
modeling_engine.register("heat_tds_temperature_coupling", None, CapabilityStatus.UNVERIFIED, "Real runtime acceptance is reported by the capability endpoint")
