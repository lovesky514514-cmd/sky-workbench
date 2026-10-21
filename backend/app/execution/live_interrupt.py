"""Safe-boundary adapter for a Workbench-owned real COMSOL Java Bridge."""
from __future__ import annotations

import asyncio
from typing import Any

from app.execution.graph import ExecutionGraph, NodeState, TypedCorrection
from app.execution.boundary_feature import BoundaryFeatureMutation, HEAT_FEATURES, kelvin_expression, read_kelvin
from app.session.assumptions import AssumptionLedger
from app.session.state_grounding import ModelStateLedger, Provenance


def _expression(value: Any, unit: str) -> str:
    numeric = format(float(value), ".15g") if isinstance(value, (int, float)) else str(value).strip()
    return f"{numeric}[{unit}]" if unit.strip() else numeric


def _same_expression(left: Any, right: Any) -> bool:
    return "".join(str(left).split()) == "".join(str(right).split())


def comsol_parameter_name(workflow_id: str, plan_name: str) -> str:
    aliases = {
        'tds_2d_stationary_diffusion': {
            'length': 'L', 'height': 'H', 'diffusion_coefficient': 'D',
            'left_concentration': 'c_left', 'right_concentration': 'c_right',
        },
    }
    return aliases.get(workflow_id, {}).get(plan_name, plan_name)


class LiveInterruptAdapter:
    def __init__(self, store: Any, bridge: Any) -> None:
        self.store = store
        self.bridge = bridge
        self._runs: dict[str, tuple[str, ExecutionGraph]] = {}
        self._resume_events: dict[str, asyncio.Event] = {}
        self._heat_boundaries: dict[str, dict[str, dict[str, Any]]] = {}

    def register(self, session_id: str, graph: ExecutionGraph, *, replace: bool = False) -> None:
        if graph.run_id in self._runs and not replace:
            raise ValueError("run already registered")
        self._runs[graph.run_id] = (session_id, graph)
        self._resume_events[graph.run_id] = asyncio.Event()
        self.store.append_event(session_id, "execution.graph_registered", graph.as_dict())

    def graph(self, run_id: str) -> ExecutionGraph:
        try:
            return self._runs[run_id][1]
        except KeyError as exc:
            raise KeyError(f"unknown interruptible run: {run_id}") from exc

    def register_heat_boundaries(self, run_id: str, edge_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Bind stable domain IDs to worker-calibrated selections and fixed COMSOL tags."""
        from app.adapter.rc42_contract import object_id
        session_id, graph = self._runs[run_id]
        if graph.workflow_id != 'heat_transfer_2d_stationary':
            raise ValueError('Heat boundary mapping requires a Heat run')
        if set(HEAT_FEATURES) - set(edge_map):
            raise ValueError('both Heat boundary selections are required')
        selections = [edge_map[side] for side in HEAT_FEATURES]
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in selections) \
                or len(set(selections)) != len(selections):
            raise ValueError('Heat boundary selections must be distinct positive COMSOL entity IDs')
        mapping = {
            object_id(session_id, 'boundary', side): {
                'side': side, 'physicsInterfaceId': 'comp1:ht',
                'featureId': feature, 'propertyKey': 'T0',
                'selectionIdentity': f'comp1:geom1:boundary:{edge_map[side]}',
                'selection': edge_map[side],
            }
            for side, feature in HEAT_FEATURES.items()
        }
        self._heat_boundaries[run_id] = mapping
        self.store.append_event(session_id, 'boundary.mapping_attached', {
            'run_id': run_id, 'attempt_id': graph.attempt_id, 'boundaries': mapping,
            'source': 'worker_coordinate_calibration_pending_bridge_confirmation',
        })
        return mapping

    def _boundary_readback(self, mapping: dict[str, Any], result: dict[str, Any], *,
                           model_tag: str, expected_expression: str | None = None) -> str:
        if not result.get('command_success') or not result.get('model_state_verified') \
                or result.get('model_tag') != model_tag \
                or result.get('component_tag') != 'comp1' or result.get('physics_tag') != 'ht' \
                or result.get('feature_tag') != mapping['featureId'] \
                or result.get('feature_type') != 'TemperatureBoundary' \
                or result.get('property_key') != 'T0' \
                or result.get('selection') != [mapping['selection']] \
                or result.get('unit') != 'K':
            raise RuntimeError('COMSOL boundary feature identity/selection/property readback mismatch')
        expression = result.get('expression')
        if not isinstance(expression, str):
            raise RuntimeError('COMSOL boundary feature value readback is missing')
        read_kelvin(expression)
        if expected_expression is not None and not _same_expression(expression, expected_expression):
            raise RuntimeError('COMSOL boundary feature value drifted from reviewed state')
        return expression

    async def request_boundary_review(self, run_id: str, boundary_object_id: str,
                                      proposed_value: float, unit: str, reason: str) -> dict[str, Any]:
        import uuid
        session_id, graph = self._runs[run_id]
        self.store.assert_current(run_id, graph.attempt_id)
        if graph.pause_requested or not any(node.state is NodeState.RUNNING for node in graph.nodes.values()):
            raise RuntimeError('boundary review must precede pause request at a running node')
        if graph.workflow_id != 'heat_transfer_2d_stationary' or unit != 'K':
            raise ValueError('only literal Heat T0 correction in K is supported')
        proposed = kelvin_expression(proposed_value)
        mapping = self._heat_boundaries.get(run_id, {}).get(boundary_object_id)
        if not mapping:
            raise ValueError('boundary object ID is not mapped to the current Heat attempt')
        health = await self.bridge.health()
        if health.get('real_comsol') is not True or health.get('model_connected') is not True:
            raise RuntimeError('real COMSOL model attachment is not verified')
        actual = await self.bridge.boundary_feature(component_tag='comp1', physics_tag='ht',
            feature_tag=mapping['featureId'], property_key='T0')
        current = self._boundary_readback(mapping, actual, model_tag=health['model_tag'])
        review = self.store.create_review(session_id, {
            'reviewId': str(uuid.uuid4()), 'runId': run_id, 'attemptId': graph.attempt_id,
            'affectedObjectId': boundary_object_id, 'currentValue': current,
            'proposedValue': proposed_value, 'unit': unit, 'reason': reason,
            'downstreamImpact': 'Existing numerical/scientific evidence becomes stale; a new solve and validation are required.',
            'allowedActions': ['approve', 'reject', 'defer'],
            'boundaryFeature': {key: mapping[key] for key in
                                ('physicsInterfaceId', 'featureId', 'selectionIdentity', 'propertyKey')},
        })
        self.store.append_event(session_id, 'boundary.readback_verified', {
            'run_id': run_id, 'attempt_id': graph.attempt_id,
            'boundary_object_id': boundary_object_id, 'expression': current,
            'selection_identity': mapping['selectionIdentity'],
            'model_tag': health['model_tag'], 'scientific_validation_passed': False,
        })
        return review

    async def apply_boundary_correction(self, run_id: str, review_id: str) -> dict[str, Any]:
        session_id, graph = self._runs[run_id]
        self.store.assert_current(run_id, graph.attempt_id)
        if not any(node.state is NodeState.PAUSED for node in graph.nodes.values()):
            raise RuntimeError('boundary correction requires a verified paused safe point')
        review = self.store.get_review(review_id)
        if not review or review.get('state') != 'OPEN' or review.get('runId') != run_id \
                or review.get('attemptId') != graph.attempt_id:
            raise ValueError('boundary review is missing or stale')
        mapping = self._heat_boundaries.get(run_id, {}).get(review['affectedObjectId'])
        if not mapping or review.get('boundaryFeature') != {key: mapping[key] for key in
                                                    ('physicsInterfaceId', 'featureId', 'selectionIdentity', 'propertyKey')}:
            raise ValueError('boundary feature identity no longer matches the review')
        if review.get('unit') != 'K':
            raise ValueError('boundary feature unit must be K')
        requested = kelvin_expression(review['proposedValue'])
        health = await self.bridge.health()
        if health.get('real_comsol') is not True or health.get('model_connected') is not True:
            raise RuntimeError('real COMSOL model attachment is not verified')
        before = await self.bridge.boundary_feature(component_tag='comp1', physics_tag='ht',
            feature_tag=mapping['featureId'], property_key='T0')
        current = self._boundary_readback(mapping, before, model_tag=health['model_tag'],
                                          expected_expression=review['currentValue'])
        mutation = BoundaryFeatureMutation(run_id, graph.attempt_id, review['affectedObjectId'],
            mapping['physicsInterfaceId'], mapping['featureId'], mapping['selectionIdentity'],
            'T0', current, float(review['proposedValue']), 'K')
        self.store.assert_current(run_id, graph.attempt_id)
        self.store.append_event(session_id, 'evidence.invalidated', {
            'run_id': run_id, 'attempt_id': graph.attempt_id,
            'object_id': mutation.boundaryObjectId,
            'evidence_id': f'evidence-{run_id}-{graph.attempt_id}',
            'reason': 'boundary_feature_mutation_before_new_solve',
        })
        self.store.assert_current(run_id, graph.attempt_id)
        written = await self.bridge.set_boundary_feature(
            component_tag='comp1', physics_tag='ht', feature_tag=mutation.featureId,
            property_key='T0', expected_selection=mapping['selection'],
            expected_expression=current, proposed_expression=requested)
        if not written.get('command_success') or not written.get('model_state_verified'):
            raise RuntimeError('COMSOL boundary feature mutation was not verified')
        self.store.assert_current(run_id, graph.attempt_id)
        after = await self.bridge.boundary_feature(component_tag='comp1', physics_tag='ht',
            feature_tag=mutation.featureId, property_key='T0')
        actual = self._boundary_readback(mapping, after, model_tag=health['model_tag'],
                                         expected_expression=requested)
        invalidated = graph.apply_correction(TypedCorrection(
            f"boundary_conditions.{mapping['side']}", mutation.proposedValue, 'K', review['reason']),
            readback={f"boundary_conditions.{mapping['side']}": mutation.proposedValue})
        result = {**mutation.as_dict(), 'readback': after, 'invalidated': invalidated,
                  'command_success': True, 'model_state_verified': True,
                  'scientific_validation_passed': False}
        self.store.append_event(session_id, 'model.object_changed', {
            'run_id': run_id, 'attempt_id': graph.attempt_id, 'object_id': mutation.boundaryObjectId,
            'feature_id': mutation.featureId, 'old_value': current, 'new_value': actual,
            'scientific_validation_passed': False,
        })
        self.store.append_event(session_id, 'model.readback_updated', {
            'run_id': run_id, 'attempt_id': graph.attempt_id, 'object_id': mutation.boundaryObjectId,
            'verified_real_comsol': True, 'model_tag': health['model_tag'],
            'selection_identity': mutation.selectionIdentity, 'readback': after,
            'scientific_validation_passed': False,
        })
        self.store.resolve_review(session_id, review_id, 'approve')
        return result

    def start(self, run_id: str, node_id: str) -> None:
        session_id, graph = self._runs[run_id]
        graph.start(node_id)
        self.store.append_event(session_id, "execution.node_started", {
            "run_id": run_id, "attempt_id": graph.attempt_id, "node_id": node_id,
        })

    def request_pause(self, run_id: str) -> None:
        session_id, graph = self._runs[run_id]
        graph.request_pause()
        self.store.append_event(session_id, "execution.pause_requested", graph.as_dict())

    def safe_boundary(self, run_id: str, node_id: str) -> NodeState:
        session_id, graph = self._runs[run_id]
        graph.safe_boundary(node_id)
        state = graph.nodes[node_id].state
        self.store.append_event(session_id, "execution.safe_boundary", {
            "run_id": run_id, "attempt_id": graph.attempt_id,
            "node_id": node_id, "state": state.value,
        })
        return state

    async def apply_parameter_correction(self, run_id: str, correction: TypedCorrection,
                                         *, review_id: str | None = None) -> dict[str, Any]:
        session_id, graph = self._runs[run_id]
        if not correction.path.startswith("parameters."):
            raise ValueError("the verified live adapter currently permits typed parameter corrections only")
        if not any(node.state in {NodeState.PAUSED, NodeState.NEEDS_REVIEW} for node in graph.nodes.values()):
            raise RuntimeError("live correction requires a verified paused boundary")
        health = await self.bridge.health()
        if not health.get("real_comsol") or not health.get("model_connected"):
            raise RuntimeError("real COMSOL model attachment is not verified")
        plan_name = correction.path.removeprefix("parameters.")
        name = comsol_parameter_name(graph.workflow_id, plan_name)
        from app.adapter.rc42_contract import object_id
        stable_id = object_id(session_id, 'parameter', plan_name)
        if review_id is not None:
            review = self.store.get_review(review_id)
            if (not review or review.get('state') != 'OPEN'
                    or review.get('runId') != run_id
                    or review.get('attemptId') != graph.attempt_id
                    or review.get('affectedObjectId') != stable_id
                    or review.get('unit') != correction.unit
                    or float(review.get('proposedValue')) != float(correction.value)):
                raise ValueError('correction does not match an open typed review')
            before = await self.bridge.parameters()
            if (before.get('model_state_verified') is not True
                    or (before.get('parameters') or {}).get(name) != review.get('currentValue')):
                raise RuntimeError('review current value no longer matches COMSOL readback')
        requested = _expression(correction.value, correction.unit)
        mutation = await self.bridge.set_parameter(name, requested)
        if not mutation.get("command_success") or not mutation.get("model_state_verified"):
            raise RuntimeError("COMSOL parameter mutation was not verified by direct readback")
        if not _same_expression(mutation.get("readback"), requested):
            raise RuntimeError("COMSOL parameter readback mismatch")
        parameter_state = await self.bridge.parameters()
        readback = (parameter_state.get("parameters") or {}).get(name)
        if not parameter_state.get("model_state_verified") or not _same_expression(readback, requested):
            raise RuntimeError("COMSOL parameter collection readback mismatch")
        # Publish invalidation before the corrected object can become current in
        # any adapter projection. Human approval/readback is not recertification.
        self.store.append_event(session_id, 'evidence.invalidated', {
            'run_id': run_id, 'attempt_id': graph.attempt_id,
            'object_id': stable_id, 'object_label': plan_name,
            'evidence_id': f'evidence-{run_id}-{graph.attempt_id}',
            'reason': 'human_correction',
        })
        invalidated = graph.apply_correction(correction, readback={correction.path: correction.value})
        ModelStateLedger(self.store).append(
            session_id, f"model.parameters.{plan_name}",
            {"value": correction.value, "unit": correction.unit, "expression": readback},
            Provenance.COMSOL_READBACK, run_id=run_id, attempt_id=graph.attempt_id,
        )
        AssumptionLedger(self.store).record(
            session_id=session_id, run_id=run_id, attempt_id=graph.attempt_id,
            plan_node_id="build", choice={"parameter": plan_name, "comsol_tag": name, "value": correction.value, "unit": correction.unit},
            reason=correction.reason, source="HUMAN_CORRECTION", confidence=1.0,
            user_confirmed=True, affected_nodes=invalidated,
        )
        result = {
            "run_id": run_id, "attempt_id": graph.attempt_id,
            "object_id": stable_id,
            "verified_real_comsol": True, "model_tag": health.get("model_tag"),
            "requested": requested, "readback": readback, "invalidated": invalidated,
            "provenance": "HUMAN_CORRECTION",
        }
        self.store.append_event(session_id, "execution.correction_verified", result)
        self.store.append_event(session_id, "model.readback_updated", {
            "run_id": run_id, "attempt_id": graph.attempt_id,
            "object_id": stable_id, "verified_real_comsol": True,
            "model_tag": health.get("model_tag"), "readback": readback,
            "scientific_validation_passed": False,
        })
        if review_id is not None:
            self.store.resolve_review(session_id, review_id, 'approve')
        return result

    async def wait_for_resume(self, run_id: str, *, timeout: float = 600.0) -> None:
        await asyncio.wait_for(self._resume_events[run_id].wait(), timeout=timeout)

    async def resume(self, run_id: str, *, study_tag: str = "std1") -> dict[str, Any]:
        session_id, graph = self._runs[run_id]
        health = await self.bridge.health()
        if not health.get("real_comsol") or not health.get("model_connected"):
            raise RuntimeError("real COMSOL model attachment was lost before resume")
        resume_node = graph.resume()
        graph.start(resume_node)
        study = await self.bridge.run_study(study_tag)
        verified = bool(study.get("command_success") and study.get("model_state_verified") and study.get("last_computation_date"))
        graph.safe_boundary(resume_node, verified=verified)
        if not verified:
            raise RuntimeError("resumed COMSOL study was not verified")
        result = {
            "run_id": run_id, "attempt_id": graph.attempt_id,
            "resume_node": resume_node, "study": study, "graph": graph.as_dict(),
        }
        self.store.append_event(session_id, "execution.resumed", result)
        self._resume_events[run_id].set()
        return result

    def status(self, run_id: str) -> dict[str, Any]:
        session_id, graph = self._runs[run_id]
        return {"session_id": session_id, **graph.as_dict()}
