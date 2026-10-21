"""Strict SkillCard schema and small seed-card loader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class SkillProvenance(BaseModel):
    kind: str
    title: str
    locator: str
    license: str


class SkillCard(BaseModel):
    schema_version: int = 1
    skill_id: str
    title: str
    workflow_id: str
    provenance: list[SkillProvenance]
    applicability: list[str]
    assumptions: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    quantities_and_units: dict[str, str] = Field(default_factory=dict)
    geometry_procedure: list[str]
    material_procedure: list[str]
    selection_procedure: list[str]
    boundary_procedure: list[str]
    mesh_and_solver: list[str]
    common_mistakes: list[str]
    mandatory_readback: list[str]
    invariants: list[str]
    escalation: list[str]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def require_safety_content(self) -> 'SkillCard':
        if not self.provenance or not self.applicability or not self.mandatory_readback or not self.invariants:
            raise ValueError('SkillCard safety fields may not be empty')
        return self


class SkillLibrary:
    def __init__(self, cards: list[SkillCard]) -> None:
        self.cards = cards

    @classmethod
    def default(cls) -> 'SkillLibrary':
        root = Path(__file__).with_name('cards')
        return cls([SkillCard.model_validate(json.loads(path.read_text(encoding='utf-8'))) for path in sorted(root.glob('*.json'))])

    def route(self, workflow_id: str) -> list[SkillCard]:
        return [card for card in self.cards if card.workflow_id == workflow_id]

    def summaries(self, workflow_id: str) -> list[dict[str, Any]]:
        return [{'skill_id': card.skill_id, 'title': card.title, 'applicability': card.applicability, 'confidence': card.confidence} for card in self.route(workflow_id)]
