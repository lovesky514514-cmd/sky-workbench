"""N03 的矩形稳态 Heat 基准。数据必须来自冻结的结构化读回。"""

from __future__ import annotations

from math import isclose, isfinite
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.execution.expert_contracts import Contract, ExpertScope, Text, VersionedRecord
from app.scientific.heat_recertification import calculate_corrected_heat_validation

MODEL_SOURCE = 'heat.model_readback.v1'
FIELD_SOURCE = 'heat.field_readback.v1'
TEMP_TOLERANCE_K = 1.0
FLUX_REL_TOLERANCE = 0.02
FLUX_ABS_TOLERANCE = 1e-6
GRID = (0.0, 0.5, 1.0)
Number = Annotated[float, Field(strict=True)]
Positive = Annotated[float, Field(strict=True, gt=0)]
Side = Literal['left', 'right', 'top', 'bottom']
CheckStatus = Literal['PASS', 'FAIL', 'UNVERIFIED']


class HeatBenchmark(VersionedRecord):
    scope: ExpertScope
    width_m: Positive
    height_m: Positive
    conductivity_W_m_K: Positive
    left_K: Positive
    right_K: Positive

    @property
    def analytical_flux(self) -> float:
        return self.conductivity_W_m_K * ((self.left_K - self.right_K) / self.width_m)

    @model_validator(mode='after')
    def validate_flux(self) -> HeatBenchmark:
        if not isfinite(self.analytical_flux):
            raise ValueError('Analytical heat flux must be finite')
        return self


class Boundary(Contract):
    side: Side
    kind: Literal['fixed_temperature', 'insulated']
    temperature_K: Number | None = None


class HeatModelReadback(VersionedRecord):
    geometry_kind: Text
    width_m: Number
    height_m: Number
    conductivity_W_m_K: Number
    heat_source_W_m3: Number
    boundaries: tuple[Boundary, ...] = Field(max_length=4)


class HeatSample(Contract):
    x_m: Number
    y_m: Number
    temperature_K: Number
    qx_W_m2: Number
    qy_W_m2: Number


class HeatFieldReadback(VersionedRecord):
    samples: tuple[HeatSample, ...] = Field(max_length=9)


class CheckObservation(Contract):
    evidence_id: str
    status: CheckStatus
    reason: str


CHECKS = {
    'model_geometry': ('矩形的宽高与冻结计划一致', MODEL_SOURCE),
    'material_and_source': ('导热率与冻结计划一致且无体热源', MODEL_SOURCE),
    'boundary_selection': ('左右定温，上下绝热，边界选择与温度一致', MODEL_SOURCE),
    'temperature': ('九点温度、边界与中心符合解析解', FIELD_SOURCE),
    'heat_flux': ('热流方向与大小符合 Fourier 定律，横向热流近零', FIELD_SOURCE),
}


def _observation(ref: str, key: str, passed: bool) -> CheckObservation:
    return CheckObservation(evidence_id=ref, status='PASS' if passed else 'FAIL', reason=CHECKS[key][0])


def check_model(plan: HeatBenchmark, actual: HeatModelReadback, ref: str) -> dict[str, CheckObservation]:
    geometry = (actual.geometry_kind == 'rectangle' and isclose(actual.width_m, plan.width_m, rel_tol=1e-6)
                and isclose(actual.height_m, plan.height_m, rel_tol=1e-6))
    material = isclose(actual.conductivity_W_m_K, plan.conductivity_W_m_K, rel_tol=1e-6) and (
        actual.heat_source_W_m3 == 0)
    by_side = {boundary.side: boundary for boundary in actual.boundaries}
    boundary_status: CheckStatus = 'PASS'
    if len(by_side) != len(actual.boundaries):
        boundary_status = 'FAIL'
    for side, expected in (('left', plan.left_K), ('right', plan.right_K), ('top', None), ('bottom', None)):
        boundary = by_side.get(side)
        if boundary is None:
            if boundary_status == 'PASS':
                boundary_status = 'UNVERIFIED'
            continue
        if expected is None:
            if boundary.kind != 'insulated' or boundary.temperature_K is not None:
                boundary_status = 'FAIL'
        elif boundary.kind != 'fixed_temperature':
            boundary_status = 'FAIL'
        elif boundary.temperature_K is None:
            if boundary_status == 'PASS':
                boundary_status = 'UNVERIFIED'
        elif abs(boundary.temperature_K - expected) > TEMP_TOLERANCE_K:
            boundary_status = 'FAIL'
    return {
        'model_geometry': _observation(ref, 'model_geometry', geometry),
        'material_and_source': _observation(ref, 'material_and_source', material),
        'boundary_selection': CheckObservation(evidence_id=ref, status=boundary_status,
                                               reason=CHECKS['boundary_selection'][0]),
    }


def check_field(plan: HeatBenchmark, field: HeatFieldReadback, ref: str) -> dict[str, CheckObservation]:
    field_checks = ('temperature', 'heat_flux')
    coordinates = set()
    for sample in field.samples:
        x = next((part for part in GRID if abs(sample.x_m / plan.width_m - part) <= 1e-6), None)
        y = next((part for part in GRID if abs(sample.y_m / plan.height_m - part) <= 1e-6), None)
        if x is None or y is None or (x, y) in coordinates:
            return {key: CheckObservation(evidence_id=ref, status='FAIL', reason='采样坐标不符或重复')
                    for key in field_checks}
        coordinates.add((x, y))
    if len(coordinates) != len(GRID) ** 2:
        return {key: CheckObservation(evidence_id=ref, status='UNVERIFIED', reason='缺少冻结采样点')
                for key in field_checks}

    samples = field.samples
    measured = {'x': [item.x_m for item in samples], 'y': [item.y_m for item in samples],
                'temperature_K': [item.temperature_K for item in samples]}
    temperature = calculate_corrected_heat_validation(measured, plan.width_m, plan.height_m,
                                                       plan.left_K, plan.right_K)
    expected_qx = plan.analytical_flux
    tolerance = max(FLUX_ABS_TOLERANCE, abs(expected_qx) * FLUX_REL_TOLERANCE)
    flux = all(abs(item.qx_W_m2 - expected_qx) <= tolerance and abs(item.qy_W_m2) <= tolerance
               and (expected_qx == 0 or (item.qx_W_m2 > 0) == (expected_qx > 0) and item.qx_W_m2 != 0)
               for item in samples)
    return {'temperature': _observation(ref, 'temperature', temperature['passed']),
            'heat_flux': _observation(ref, 'heat_flux', flux)}
