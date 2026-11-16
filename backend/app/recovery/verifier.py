"""恢复动作执行器和独立复验边界。本版本只支持无副作用的模拟。"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from app.execution.expert_contracts import Contract
from app.recovery.schemas import Action, DiagnosticSnapshot, Digest, Identifier, Proposal


class SimulationClaim(Contract):
    proposal_id: Identifier
    approval_ref: Identifier
    digest: Digest


class SimulationReceipt(Contract):
    case_id: Identifier
    proposal_id: Identifier
    proposal_digest: Digest
    simulated_snapshot: Digest
    mode: Literal['simulation'] = 'simulation'


class Verification(Contract):
    case_id: Identifier
    passed: bool = Field(strict=True)
    independent: Literal[True] = True
    mode: Literal['simulation', 'observed']
    evidence_refs: tuple[Identifier, ...]
    reason: Literal['matches', 'receipt_mismatch', 'state_changed', 'independent_check_failed']


class RecoveryActionExecutor(Protocol):
    async def execute(self, proposal: Proposal, snapshot: DiagnosticSnapshot) -> SimulationReceipt: ...


class SimulatedActionExecutor:
    async def execute(self, proposal: Proposal, snapshot: DiagnosticSnapshot) -> SimulationReceipt:
        if proposal.action is not Action.SIMULATE:
            raise PermissionError('action_not_simulatable')
        return SimulationReceipt(case_id=proposal.case_id, proposal_id=proposal.proposal_id,
                                 proposal_digest=proposal.digest, simulated_snapshot=snapshot.sha256)


class IndependentVerifier:
    async def verify(self, proposal: Proposal, before: DiagnosticSnapshot, after: DiagnosticSnapshot,
                     receipt: SimulationReceipt) -> Verification:
        matches = (receipt.case_id == proposal.case_id and receipt.proposal_id == proposal.proposal_id
                   and receipt.proposal_digest == proposal.digest and receipt.simulated_snapshot == before.sha256)
        valid_scope = ((after.run_state == 'active' and after.owner_matches is True) if proposal.scientific_attempt_id
                       else (after.run_state == 'none' and after.owner_matches is None))
        stable = before.sha256 == after.sha256 and valid_scope
        return Verification(case_id=proposal.case_id, passed=matches and stable, mode='simulation',
                            evidence_refs=(before.snapshot_id, after.snapshot_id),
                            reason='matches' if matches and stable else
                            'state_changed' if matches else 'receipt_mismatch')


class ConnectionVerifier:
    """宿主独立判定连接读回，不接受专家的 success 或科学评分。"""
    def verify(self, payload: dict, before: DiagnosticSnapshot, after: DiagnosticSnapshot) -> bool:
        return (before.sha256 == after.sha256 and after.run_state in {'none','active'}
                and all(payload.get(key) is True for key in ('connected','real_comsol','comsol_api_loaded','model_connected')))
