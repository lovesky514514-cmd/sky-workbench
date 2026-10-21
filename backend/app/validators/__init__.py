"""Scientific validators separate command success from physical validity."""

from app.validators.physics.heat_transfer_validator import HeatTransferValidator
from app.validators.physics.tds_benchmark_validator import TDSBenchmarkValidator
from app.validators.physics.solid_mechanics_validator import SolidMechanicsValidator
from app.validators.physics.electrochem_validator import ElectrochemistryValidator
from app.validators.physics.battery_validator import BatteryValidator
from app.validators.physics.multiphysics_validator import MultiphysicsValidator
from app.validators.base_validator import BaseValidator
from app.validators.plan_quality_validator import ScientificPlanQualityValidator, plan_quality_validator


class ValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, BaseValidator] = {
            "heat_transfer_2d_stationary": HeatTransferValidator(),
            "tds_2d_stationary_diffusion": TDSBenchmarkValidator(),
            "solid_mechanics_uniaxial_bar": SolidMechanicsValidator(),
            "electrochem_primary_current": ElectrochemistryValidator(),
            "lithium_ion_battery_baseline": BatteryValidator(),
            "heat_tds_temperature_coupling": MultiphysicsValidator(),
        }

    def get(self, workflow_id: str | None) -> BaseValidator | None:
        return self._validators.get(workflow_id or "")


validator_registry = ValidatorRegistry()

__all__ = ["HeatTransferValidator", "TDSBenchmarkValidator", "SolidMechanicsValidator", "ElectrochemistryValidator", "BatteryValidator", "MultiphysicsValidator", "ScientificPlanQualityValidator", "plan_quality_validator", "validator_registry"]
