"""Small durable-shaped execution graph; it does not execute COMSOL itself."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class NodeState(StrEnum):
    PENDING = 'PENDING'
    READY = 'READY'
    RUNNING = 'RUNNING'
    PAUSE_REQUESTED = 'PAUSE_REQUESTED'
    PAUSED = 'PAUSED'
    NEEDS_REVIEW = 'NEEDS_REVIEW'
    VERIFIED = 'VERIFIED'
    FAILED = 'FAILED'
    INVALIDATED = 'INVALIDATED'
    SKIPPED = 'SKIPPED'


@dataclass(slots=True)
class ExecutionNode:
    node_id: str
    dependencies: tuple[str, ...] = ()
    state: NodeState = NodeState.PENDING


@dataclass(frozen=True, slots=True)
class TypedCorrection:
    path: str
    value: Any
    unit: str
    reason: str


@dataclass(slots=True)
class ExecutionGraph:
    run_id: str
    attempt_id: str
    workflow_id: str
    nodes: dict[str, ExecutionNode]
    pause_requested: bool = False
    corrections: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def small_supported(cls, run_id: str, attempt_id: str, workflow_id: str) -> 'ExecutionGraph':
        if workflow_id not in {'heat_transfer_2d_stationary', 'tds_2d_stationary_diffusion'}:
            raise ValueError('interruptible skeleton currently supports Heat/TDS only')
        nodes = {
            'build': ExecutionNode('build', state=NodeState.READY),
            'solve': ExecutionNode('solve', ('build',)),
            'validate': ExecutionNode('validate', ('solve',)),
            'export': ExecutionNode('export', ('validate',)),
        }
        return cls(run_id, attempt_id, workflow_id, nodes)

    def start(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.state not in {NodeState.READY, NodeState.INVALIDATED}:
            raise RuntimeError(f'{node_id} is not ready')
        if any(self.nodes[dep].state is not NodeState.VERIFIED for dep in node.dependencies):
            raise RuntimeError(f'{node_id} dependencies are not verified')
        node.state = NodeState.RUNNING

    def request_pause(self) -> None:
        self.pause_requested = True
        for node in self.nodes.values():
            if node.state is NodeState.RUNNING:
                node.state = NodeState.PAUSE_REQUESTED

    def safe_boundary(self, node_id: str, *, verified: bool = True) -> None:
        node = self.nodes[node_id]
        if node.state not in {NodeState.RUNNING, NodeState.PAUSE_REQUESTED}:
            raise RuntimeError('safe boundary requires a running node')
        if self.pause_requested:
            node.state = NodeState.PAUSED
            return
        node.state = NodeState.VERIFIED if verified else NodeState.NEEDS_REVIEW
        if verified:
            self._refresh_ready()

    def apply_correction(self, correction: TypedCorrection, *, readback: dict[str, Any]) -> list[str]:
        if not any(node.state in {NodeState.PAUSED, NodeState.NEEDS_REVIEW} for node in self.nodes.values()):
            raise RuntimeError('correction requires PAUSED or NEEDS_REVIEW state')
        if correction.path not in readback or readback[correction.path] != correction.value:
            raise ValueError('correction readback mismatch')
        record = {**asdict(correction), 'provenance': 'HUMAN_CORRECTION', 'readback': readback[correction.path]}
        self.corrections.append(record)
        geometry_parameters = {
            'parameters.width', 'parameters.height', 'parameters.length',
            'parameters.depth', 'parameters.radius',
        }
        first = (
            'build'
            if correction.path in geometry_parameters
            or correction.path.startswith(('geometry.', 'materials.', 'boundary_conditions.'))
            else 'solve'
        )
        invalidated = self._dependent_nodes(first)
        for name, node in self.nodes.items():
            if node.state is NodeState.PAUSED and name not in invalidated:
                node.state = NodeState.VERIFIED
        for name in invalidated:
            self.nodes[name].state = NodeState.INVALIDATED
        self.pause_requested = False
        return invalidated

    def _dependent_nodes(self, node_id: str) -> list[str]:
        selected = {node_id}
        changed = True
        while changed:
            changed = False
            for name, node in self.nodes.items():
                if name not in selected and any(dependency in selected for dependency in node.dependencies):
                    selected.add(name)
                    changed = True
        return [name for name in self.nodes if name in selected]

    def resume(self) -> str:
        for name, node in self.nodes.items():
            if node.state is NodeState.INVALIDATED:
                node.state = NodeState.READY
                return name
        raise RuntimeError('no invalidated node to resume')

    def _refresh_ready(self) -> None:
        for node in self.nodes.values():
            if node.state is NodeState.PENDING and all(self.nodes[dep].state is NodeState.VERIFIED for dep in node.dependencies):
                node.state = NodeState.READY

    def as_dict(self) -> dict[str, Any]:
        return {
            'schema_version': 1, 'run_id': self.run_id, 'attempt_id': self.attempt_id,
            'workflow_id': self.workflow_id, 'pause_requested': self.pause_requested,
            'nodes': {name: {'node_id': node.node_id, 'dependencies': list(node.dependencies), 'state': node.state.value} for name, node in self.nodes.items()},
            'corrections': list(self.corrections),
        }
