"""Typed Heat boundary identity; never substitute a model parameter for T0."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re


HEAT_FEATURES = {'left': 'temp_left', 'right': 'temp_right'}
_KELVIN = re.compile(r'^\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\[K\]\s*$')


def kelvin_expression(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('absolute boundary temperature must be finite and positive')
    return f'{value:.15g}[K]'


def read_kelvin(expression: str) -> float:
    match = _KELVIN.fullmatch(expression)
    if not match:
        raise ValueError('COMSOL boundary T0 must be a literal Kelvin expression')
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('COMSOL boundary T0 must be finite and positive')
    return value


@dataclass(frozen=True, slots=True)
class BoundaryFeatureMutation:
    runId: str
    attemptId: str
    boundaryObjectId: str
    physicsInterfaceId: str
    featureId: str
    selectionIdentity: str
    propertyKey: str
    currentValue: str
    proposedValue: float
    unit: str

    def as_dict(self) -> dict:
        return asdict(self)
