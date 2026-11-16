"""真实科研讨论契约；模型只提主张、问题和补证请求，不产生审批。"""
from typing import Annotated, Literal
from pydantic import Field

from app.execution.expert_contracts import Contract, ExpertClaim, Text

Role = Literal['research_intent', 'literature_materials', 'physical_modeling', 'numerical_methods',
               'scientific_counterevidence', 'reproducibility', 'software_execution', 'recovery_diagnostics']


class ScientificClaim(ExpertClaim):
    conditions: tuple[Text, ...] = Field(max_length=8)
    uncertainty: Text
    counterexamples: tuple[Text, ...] = Field(max_length=8)
    questions: tuple[Text, ...] = Field(max_length=8)


class ExpertRequest(Contract):
    capability: Role
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_refs: tuple[Text, ...] = Field(max_length=16)


class ExpertOpinion(Contract):
    summary: Annotated[str, Field(min_length=1, max_length=3000)]
    claims: tuple[ScientificClaim, ...] = Field(max_length=12)
    requests: tuple[ExpertRequest, ...] = Field(max_length=4)

    def validate_evidence(self, visible):
        for item in (*self.claims, *self.requests):
            if not set(item.evidence_refs) <= visible:
                raise ValueError('expert_cited_unregistered_or_foreign_evidence')
        return self


class ResearchLimits(Contract):
    model_calls: Annotated[int, Field(strict=True, ge=1, le=32)] = 8
    token_limit: Annotated[int, Field(strict=True, ge=4096, le=1000000)] = 160000
    output_tokens: Annotated[int, Field(strict=True, ge=256, le=4096)] = 1800
    concurrency: Annotated[int, Field(strict=True, ge=1, le=8)] = 3


class ParameterRange(Contract):
    parameter: Text
    lower: Annotated[float, Field(strict=True)]
    upper: Annotated[float, Field(strict=True)]
    unit: Text
    conditions: tuple[Text, ...] = ()


class ResearchEvidence(Contract):
    """内容由用户/已授权检索工具登记；模型不能自登记验证状态或新来源。"""
    evidence_id: Annotated[str, Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$')]
    source: Text
    captured_at: Text
    applicability: Text
    text: Annotated[str, Field(min_length=1, max_length=12000)]
    parameter_ranges: tuple[ParameterRange, ...] = Field(default=(), max_length=16)
    boundary_interpretation: Literal['unspecified', 'insulated', 'convection', 'prescribed_temperature', 'radiation'] = 'unspecified'
    independent: bool = False
    validation_checks: dict[str, bool] = Field(default_factory=dict)
