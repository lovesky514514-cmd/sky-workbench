"""Model-independent proposal boundary. Proposals are never execution evidence."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelProposal(BaseModel):
    schema_version: int = 1
    action: Literal['clarify', 'propose_plan', 'propose_correction', 'escalate']
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    assumptions: list[str] = Field(default_factory=list)


class ProposalAdapter(ABC):
    @abstractmethod
    async def propose(self, snapshot: dict[str, Any]) -> ModelProposal:
        """Return a typed proposal from a bounded ContextSnapshot."""
        raise NotImplementedError
