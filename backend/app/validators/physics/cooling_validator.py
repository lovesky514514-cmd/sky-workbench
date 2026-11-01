"""Typed preflight and independent numerical checks for the cooling equation."""

import math
import csv
import hashlib
from pathlib import Path

from app.scientific.cooling import UNITS, WORKFLOW
from app.validators.base_validator import (
    BaseValidator,
    ValidationCheck,
    ValidationReport,
)

BUILD_CHECKS = (
    "geometry_size",
    "six_faces",
    "heat_interface",
    "material_properties",
    "initial_temperature",
    "all_faces_flux",
    "transient_study",
    "parameter_readback",
)
SCIENCE_CHECKS = (
    "solver_completed",
    "finite_fields",
    "temperature_bounds",
    "monotonic_cooling",
    "requested_time_reached",
    "energy_balance",
    "small_biot_reference",
    "mesh_agreement",
    "time_agreement",
    "independent_resolves",
    "energy_components",
)


def check(name, passed, message):
    return ValidationCheck(
        category="Cooling",
        name=name,
        status="passed" if passed else "failed",
        message=message,
    )


class CoolingValidator(BaseValidator):
    def precheck(self, plan):
        checks = [
            check(
                "workflow",
                plan.workflow_id == WORKFLOW,
                "Three-dimensional cooling workflow",
            ),
            check(
                "complete",
                not plan.missing_information,
                "Review missing cooling conditions",
            ),
        ]
        values = {}
        for name, unit in UNITS.items():
            item = plan.parameters.get(name)
            try:
                value = float(item.value)
                valid = math.isfinite(value) and item.unit == unit
                valid &= (
                    not item.assumption or item.use_authorized or item.user_confirmed
                )
            except (TypeError, ValueError, AttributeError):
                value, valid = math.nan, False
            values[name] = value
            checks.append(
                check(name, valid, f"{name} must be finite, authorized and in {unit}")
            )
        checks.extend(
            [
                check(
                    "dimensions",
                    all(0 < values[k] <= 0.2 for k in ("width", "height", "length")),
                    "Dimensions in (0, 0.2] m",
                ),
                check(
                    "temperatures",
                    0
                    < values["ambient_temperature"]
                    < values["initial_temperature"]
                    <= 1500,
                    "Positive cooling temperatures; model excludes melting",
                ),
                check(
                    "material",
                    all(
                        values[k] > 0
                        for k in ("thermal_conductivity", "density", "heat_capacity")
                    ),
                    "Positive constant material properties",
                ),
                check(
                    "heat_exchange",
                    values["heat_transfer_coefficient"] >= 0
                    and 0 <= values["emissivity"] <= 1
                    and (
                        values["heat_transfer_coefficient"] > 0
                        or values["emissivity"] > 0
                    ),
                    "Convection/radiation coefficient bounds",
                ),
                check(
                    "time_range",
                    0 < values["output_interval"] <= values["duration"] <= 3600
                    and values["duration"] / max(values["output_interval"], 1e-30)
                    <= 600,
                    "At most 601 output times over 1 hour",
                ),
                check(
                    "boundary_scope",
                    any(
                        ("convection" in b.lower() and "radiation" in b.lower())
                        or ("空气" in b and "辐射" in b)
                        for b in plan.boundary_conditions
                    ),
                    "Confirm all-face air convection and radiation",
                ),
                check(
                    "study",
                    any("time dependent" in s.lower() for s in plan.study),
                    "Time-dependent study is required",
                ),
            ]
        )
        if plan.cooling_model.property_law == "linear_temperature":
            try:
                reference = float(
                    plan.parameters["property_reference_temperature"].value
                )
                factors = [
                    1 + float(plan.parameters[name].value) * (temperature - reference)
                    for name in (
                        "heat_capacity_temperature_coefficient",
                        "thermal_conductivity_temperature_coefficient",
                    )
                    for temperature in (
                        values["initial_temperature"],
                        values["ambient_temperature"],
                    )
                ]
                valid = 0 < reference <= 1500 and all(
                    math.isfinite(f) and f > 0 for f in factors
                )
            except (KeyError, ValueError, TypeError):
                valid = False
            checks.append(
                check(
                    "positive_temperature_property_law",
                    valid,
                    "Reviewed k(T) and Cp(T) must remain positive over the temperature interval",
                )
            )
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan, execution):
        checks = [
            check(
                name,
                execution.get(section, {}).get("checks", {}).get(name) is True,
                f"Required actual readback/check: {name}",
            )
            for section, names in (
                ("build_readback", BUILD_CHECKS),
                ("validation", SCIENCE_CHECKS),
            )
            for name in names
        ]
        validation = execution.get("validation", {})
        for name, limit in (
            ("energy_relative_error", 0.02),
            ("reference_relative_error", 0.02),
            ("mesh_relative_difference", 0.01),
        ):
            value = validation.get(name)
            checks.append(
                check(
                    "independent_" + name,
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(value)
                    and 0 <= value <= limit,
                    f"{name} <= {limit}",
                )
            )
        records = validation.get("meshes", [])
        try:
            folder = Path(execution["artifacts"]["model"]).parent.resolve()
            independent = (
                len(records) == 4 and len({r["solve_id"] for r in records}) == 4
            )
            independent &= (
                records[0]["dof"]
                < records[1]["dof"]
                < records[2]["dof"]
                == records[3]["dof"]
            )
            independent &= (
                records[3]["rtol"] < records[2]["rtol"]
                and records[3]["maximum_step_s"] < records[2]["maximum_step_s"]
            )
            for record in records:
                path = folder / record["history_file"]
                independent &= path.resolve().parent == folder and path.is_file()
                independent &= (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    == record["history_sha256"]
                )
            with Path(execution["artifacts"]["cooling_validation_history"]).open(
                encoding="utf-8"
            ) as stream:
                rows = list(csv.DictReader(stream))

            def integrate(key):
                return sum(
                    (float(b["time_s"]) - float(a["time_s"]))
                    * (float(a[key]) + float(b[key]))
                    / 2
                    for a, b in zip(rows, rows[1:])
                )

            components = all(
                math.isclose(
                    integrate(key), validation[name], rel_tol=1e-8, abs_tol=1e-10
                )
                for key, name in (
                    ("convection_W", "convective_energy_J"),
                    ("radiation_W", "radiative_energy_J"),
                )
            )
        except (KeyError, IndexError, OSError, TypeError, ValueError):
            independent, components = False, False
        checks.append(
            check(
                "independent_convergence_artifacts",
                bool(independent),
                "Reopen four fresh mesh/time histories and verify DOF, settings and file digests",
            )
        )
        checks.append(
            check(
                "independent_energy_components",
                bool(components),
                "Reintegrate separate convection and radiation from solver CSV",
            )
        )
        return ValidationReport.from_checks(
            "solution",
            checks,
            command_success=execution.get("command_success") is True,
            model_state_verified=all(
                execution.get("build_readback", {}).get("checks", {}).get(k) is True
                for k in BUILD_CHECKS
            ),
        )
