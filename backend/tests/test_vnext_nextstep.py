from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.state import assert_transition
from app.agent.tool_registry import worker_process_environment
from app.execution.graph import ExecutionGraph, NodeState, TypedCorrection
from app.models import AgentState
from app.execution.live_interrupt import LiveInterruptAdapter
from app.memory.store import (
    MemoryAdmissionPolicy,
    MemoryCandidate,
    MemoryStore,
    MemoryType,
    ValidationStatus,
)
from app.recovery.controller import (
    ControllerInput,
    RecoverabilityAction,
    RecoverabilityActionController,
)
from app.session.assumptions import AssumptionDecision, AssumptionLedger
from app.session.state_grounding import rebuild_context_snapshot
from app.session.store import SessionStore
from app.validators.execution_verdict import ExecutionVerdict, verify_artifact_integrity


def initialized_store(tmp_path: Path) -> tuple[SessionStore, str]:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    store.initialize()
    session = store.create("default", "vNext NextStep")
    return store, session["id"]


def test_worker_subprocess_forces_utf8_protocol() -> None:
    environment = worker_process_environment({"PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"})
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"


def test_export_failure_can_enter_bounded_repair() -> None:
    assert_transition(AgentState.EXPORTING, AgentState.REPAIRING)


def test_artifact_reopen_rehash_rejects_modified_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "result.csv"
    artifact.write_bytes(b"verified-bytes")
    expected = {
        "path": str(artifact),
        "size_bytes": len(b"verified-bytes"),
        "sha256": "35b1135247e25b36525c4f5bb88038cf310f05ab54aa450e406e9ef5c5fde911",
    }
    assert verify_artifact_integrity(artifact, expected)["passed"]
    artifact.write_bytes(b"modified-bytes")
    result = verify_artifact_integrity(artifact, expected)
    assert not result["passed"]
    assert "artifact:sha256_mismatch" in result["issues"]


def test_completed_evidence_is_bound_to_current_attempt(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    store.begin_run(session_id, "run-evidence", {"workflow_id": "tds_2d_stationary_diffusion"})
    attempt_id = store.begin_attempt("run-evidence")
    store.finish_run("run-evidence", attempt_id, "Completed")
    evidence = {"passed": True, "artifacts": {"result": {"sha256": "abc", "size_bytes": 3}}}
    store.append_event(session_id, "run.completed", {
        "run_id": "run-evidence", "attempt_id": attempt_id, "evidence_contract": evidence,
    })
    assert store.completed_evidence("run-evidence") == evidence

    store.append_event(session_id, "run.completed", {
        "run_id": "different-run", "attempt_id": "different-attempt",
        "evidence_contract": {"passed": True, "artifacts": {}},
    })
    assert store.completed_evidence("run-evidence") == evidence

    store.append_event(session_id, "run.completed", {
        "run_id": "run-evidence", "attempt_id": "stale-attempt", "evidence_contract": evidence,
    })
    assert store.completed_evidence("run-evidence") is None


def test_human_correction_invalidates_scientific_and_evidence_gates() -> None:
    original = ExecutionVerdict(
        "run-live", "tds_2d_stationary_diffusion", True, True, True, (), True,
    )
    invalidated = original.invalidate_scientific_evidence("human_correction")

    assert invalidated.command_success is True
    assert invalidated.model_state_verified is True
    assert invalidated.scientific_validation_success is False
    assert invalidated.evidence_contract_passed is False
    assert invalidated.completed is False
    assert invalidated.issues == ("human_correction",)


def test_assumption_ledger_logs_safe_default_and_blocks_domain_sensitive_choice(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    ledger = AssumptionLedger(store)

    safe = ledger.record(
        session_id=session_id,
        run_id="run-1",
        attempt_id="attempt-1",
        plan_node_id="build",
        choice={"parameter": "k", "value": 238.0, "unit": "W/(m*K)"},
        reason="Frozen supported Heat benchmark default",
        source="DETERMINISTIC_DEFAULT",
        confidence=0.8,
        user_confirmed=False,
        affected_nodes=["build", "solve", "validate", "export"],
        safe_default=True,
        domain_sensitive=False,
    )
    sensitive = ledger.record(
        session_id=session_id,
        run_id="run-1",
        attempt_id="attempt-1",
        plan_node_id="build",
        choice={"boundary": "left", "entity": 7},
        reason="Entity identity is ambiguous without COMSOL readback",
        source="MODEL_PROPOSAL",
        confidence=0.99,
        user_confirmed=False,
        affected_nodes=["build", "solve", "validate", "export"],
        safe_default=False,
        domain_sensitive=True,
    )

    assert safe["decision"] == AssumptionDecision.CONTINUE
    assert sensitive["decision"] == AssumptionDecision.ASK_HUMAN
    assert not sensitive["executable"]
    assert ledger.active(session_id) == [safe, sensitive]


def test_assumption_supersession_returns_only_affected_nodes(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    ledger = AssumptionLedger(store)
    old = ledger.record(
        session_id=session_id,
        run_id="run-1",
        attempt_id="attempt-1",
        plan_node_id="build",
        choice={"parameter": "width", "value": 0.01, "unit": "m"},
        reason="Initial explicit plan value",
        source="PLAN",
        confidence=1.0,
        user_confirmed=True,
        affected_nodes=["build", "solve", "validate", "export"],
    )
    replacement = ledger.record(
        session_id=session_id,
        run_id="run-1",
        attempt_id="attempt-1",
        plan_node_id="build",
        choice={"parameter": "width", "value": 0.02, "unit": "m"},
        reason="Human correction",
        source="HUMAN_CORRECTION",
        confidence=1.0,
        user_confirmed=True,
        affected_nodes=["build", "solve", "validate", "export"],
    )

    invalidated = ledger.supersede(old["assumption_id"], replacement["assumption_id"])

    assert invalidated == ["build", "solve", "validate", "export"]
    assert [item["assumption_id"] for item in ledger.active(session_id)] == [replacement["assumption_id"]]


def memory_candidate(
    memory_type: MemoryType,
    *,
    outcome: str = "Completed",
    evidence: bool = True,
    scientific: bool = True,
    false_pass: bool = False,
    version_scope: str = "1.1",
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=memory_type,
        content={"repair": "rebuild the named selection from readback"},
        provenance={"run_id": "run-1", "attempt_id": "attempt-1"},
        version_scope=version_scope,
        applicability={"workflow_id": "heat_transfer_2d_stationary"},
        source_outcome=outcome,
        evidence_contract_passed=evidence,
        scientific_validation_passed=scientific,
        false_pass=false_pass,
    )


@pytest.mark.parametrize("outcome,evidence,scientific,false_pass", [
    ("Failed", True, True, False),
    ("Unverifiable", True, True, False),
    ("Completed", False, True, False),
    ("Completed", True, False, False),
    ("Completed", True, True, True),
])
def test_untrusted_runs_cannot_create_trusted_procedural_memory(
    tmp_path: Path, outcome: str, evidence: bool, scientific: bool, false_pass: bool
) -> None:
    store, _ = initialized_store(tmp_path)
    memories = MemoryStore(store)
    record = memories.admit(
        memory_candidate(
            MemoryType.PROCEDURAL_REPAIR,
            outcome=outcome,
            evidence=evidence,
            scientific=scientific,
            false_pass=false_pass,
        ),
        MemoryAdmissionPolicy(),
    )
    assert record["validation_status"] == ValidationStatus.REJECTED


def test_memory_admission_supersession_stale_quarantine_and_context_filter(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    store.set_plan(session_id, {"workflow_id": "heat_transfer_2d_stationary", "version": "1.1"})
    memories = MemoryStore(store)
    trusted = memories.admit(memory_candidate(MemoryType.PROCEDURAL_REPAIR), MemoryAdmissionPolicy())
    negative = memories.admit(
        memory_candidate(MemoryType.NEGATIVE_REPAIR, outcome="Failed", evidence=False, scientific=False),
        MemoryAdmissionPolicy(),
    )
    stale = memories.admit(
        memory_candidate(MemoryType.SEMANTIC_FAILURE_PATTERN, version_scope="0.7"),
        MemoryAdmissionPolicy(),
    )

    relevant = memories.relevant("heat_transfer_2d_stationary", version_scope="1.1")
    snapshot = rebuild_context_snapshot(store, session_id, persist=False)

    assert trusted["validation_status"] == ValidationStatus.TRUSTED
    assert negative["validation_status"] == ValidationStatus.TRUSTED_NEGATIVE
    assert stale["validation_status"] == ValidationStatus.TRUSTED
    assert {item["memory_id"] for item in relevant} == {trusted["memory_id"], negative["memory_id"]}
    assert memories.get(stale["memory_id"])["validation_status"] == ValidationStatus.QUARANTINED
    assert snapshot.schema_version == 2
    assert {item["memory_id"] for item in snapshot.validated_memories} == {trusted["memory_id"], negative["memory_id"]}

    successor = memories.admit(memory_candidate(MemoryType.PROCEDURAL_REPAIR), MemoryAdmissionPolicy())
    memories.supersede(trusted["memory_id"], successor["memory_id"])
    assert memories.get(trusted["memory_id"])["validation_status"] == ValidationStatus.SUPERSEDED


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"safety_blocked": True}, RecoverabilityAction.PAUSE),
        ({"domain_sensitive_ambiguity": True}, RecoverabilityAction.ASK_HUMAN),
        ({"all_certification_gates_passed": True}, RecoverabilityAction.CERTIFY),
        ({"missing_required_evidence": True, "evidence_acquisition_available": True}, RecoverabilityAction.CONTINUE),
        ({"failure_class": "retryable", "attempt_count": 0, "max_attempts": 1}, RecoverabilityAction.RETRY_SAME_MODEL),
        ({"failure_class": "repairable", "applicable_skill_ids": ("heat-2d",)}, RecoverabilityAction.APPLY_SKILL),
        ({"failure_class": "repairable", "validated_memory_ids": ("memory-1",)}, RecoverabilityAction.RECALL_MEMORY),
        ({"failure_class": "capacity", "larger_model_allowed": True}, RecoverabilityAction.ESCALATE_MODEL),
        ({"failure_class": "capacity", "larger_model_allowed": False}, RecoverabilityAction.NONMODEL_ESCALATION),
    ],
)
def test_recoverability_action_controller_is_deterministic(payload: dict[str, object], expected: RecoverabilityAction) -> None:
    decision = RecoverabilityActionController().decide(ControllerInput(**payload))
    assert decision.action is expected
    assert decision.priority in {"SAFETY", "EVIDENCE", "RECOVERY", "COST"}


class FakeLiveBridge:
    def __init__(self, *, readback: str = "0.02[m]") -> None:
        self.readback = readback
        self.study_runs = 0

    async def health(self) -> dict[str, object]:
        return {"real_comsol": True, "model_connected": True, "model_tag": "workbench_heat_live"}

    async def set_parameter(self, name: str, value: str) -> dict[str, object]:
        return {
            "name": name,
            "requested": value,
            "readback": self.readback,
            "command_success": True,
            "model_state_verified": value == self.readback,
        }

    async def parameters(self) -> dict[str, object]:
        return {"parameters": {"width": self.readback}, "model_state_verified": True}

    async def run_study(self, study_tag: str = "std1") -> dict[str, object]:
        self.study_runs += 1
        return {
            "study_tag": study_tag,
            "command_success": True,
            "model_state_verified": True,
            "last_computation_date": 1,
        }


@pytest.mark.asyncio
async def test_live_interrupt_requires_real_readback_and_records_human_provenance(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    graph = ExecutionGraph.small_supported("run-live", "attempt-live", "heat_transfer_2d_stationary")
    bridge = FakeLiveBridge()
    adapter = LiveInterruptAdapter(store, bridge)
    adapter.register(session_id, graph)
    adapter.start("run-live", "build")
    adapter.request_pause("run-live")
    assert adapter.safe_boundary("run-live", "build") == NodeState.PAUSED

    result = await adapter.apply_parameter_correction(
        "run-live",
        TypedCorrection(path="parameters.width", value=0.02, unit="m", reason="User increased width"),
    )
    resumed = await adapter.resume("run-live")

    assert result["verified_real_comsol"] is True
    assert result["invalidated"] == ["build", "solve", "validate", "export"]
    assert resumed["resume_node"] == "build"
    assert resumed["study"]["model_state_verified"] is True
    assert bridge.study_runs == 1
    events = store.get(session_id)["events"]
    assert any(item["type"] == "execution.correction_verified" for item in events)
    assert adapter.graph("run-live").corrections[0]["provenance"] == "HUMAN_CORRECTION"


@pytest.mark.asyncio
async def test_live_interrupt_rejects_mismatched_bridge_readback(tmp_path: Path) -> None:
    store, session_id = initialized_store(tmp_path)
    graph = ExecutionGraph.small_supported("run-live", "attempt-live", "heat_transfer_2d_stationary")
    adapter = LiveInterruptAdapter(store, FakeLiveBridge(readback="0.03[m]"))
    adapter.register(session_id, graph)
    adapter.start("run-live", "build")
    adapter.request_pause("run-live")
    adapter.safe_boundary("run-live", "build")

    with pytest.raises(RuntimeError, match="readback"):
        await adapter.apply_parameter_correction(
            "run-live",
            TypedCorrection(path="parameters.width", value=0.02, unit="m", reason="User increased width"),
        )
    assert adapter.graph("run-live").nodes["build"].state is NodeState.PAUSED


def test_physics_parameter_correction_invalidates_only_solve_descendants() -> None:
    graph = ExecutionGraph.small_supported("run-tds", "attempt-tds", "tds_2d_stationary_diffusion")
    graph.start("build")
    graph.request_pause()
    graph.safe_boundary("build")

    invalidated = graph.apply_correction(
        TypedCorrection(
            path="parameters.diffusion_coefficient", value=2e-9,
            unit="m^2/s", reason="User correction",
        ),
        readback={"parameters.diffusion_coefficient": 2e-9},
    )

    assert invalidated == ["solve", "validate", "export"]
    assert graph.nodes["build"].state is NodeState.VERIFIED
    assert graph.resume() == "solve"
    graph.start("solve")
