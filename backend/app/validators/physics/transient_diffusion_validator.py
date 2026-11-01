import math
import csv
import hashlib
from pathlib import Path
from app.validators.base_validator import (
    BaseValidator,
    ValidationCheck,
    ValidationReport,
)
from app.scientific.transient_diffusion import (
    WORKFLOW,
    UNITS,
    INITIAL,
    BOUNDARY,
    BUILD,
    SCIENCE,
)


class TransientDiffusionValidator(BaseValidator):
    def precheck(self, plan):
        checks = []

        def add(name, ok, message):
            checks.append(
                ValidationCheck(
                    category="Transient diffusion",
                    name=name,
                    status="passed" if ok else "failed",
                    message=message,
                )
            )

        values = {}
        for name, unit in UNITS.items():
            parameter = plan.parameters.get(name)
            try:
                value = float(parameter.value)
                valid = (
                    parameter.unit == unit
                    and math.isfinite(value)
                    and (
                        not parameter.assumption
                        or parameter.use_authorized
                        or parameter.user_confirmed
                    )
                )
            except (AttributeError, TypeError, ValueError):
                value, valid = math.nan, False
            values[name] = value
            add(name, valid, f"Finite authorized {name} in {unit}")
        add(
            "workflow",
            plan.workflow_id == WORKFLOW,
            "Supported sealed transient diffusion model",
        )
        add(
            "positive_inputs",
            all(
                values[k] > 0
                for k in (
                    "length",
                    "height",
                    "diffusion_coefficient",
                    "duration",
                    "output_interval",
                )
            ),
            "Positive geometry, diffusion and time",
        )
        add(
            "initial_range",
            0 <= values["concentration_amplitude"] <= values["mean_concentration"],
            "Nonnegative cosine concentration profile",
        )
        add(
            "time_count",
            1 <= values["duration"] / max(values["output_interval"], 1e-30) <= 500,
            "Bounded output sample count",
        )
        add(
            "boundary",
            plan.boundary_conditions == [BOUNDARY],
            "Exact sealed boundary contract",
        )
        add(
            "initial",
            len(plan.initial_conditions) == 1
            and plan.initial_conditions[0].values == {"c": INITIAL},
            "Exact initial cosine profile",
        )
        add("study", plan.study == ["Time Dependent"], "Transient study is explicit")
        add(
            "complete",
            not plan.missing_information and plan.completion is None,
            "All required facts are present",
        )
        return ValidationReport.from_checks("precheck", checks)

    def validate_execution(self, plan, execution):
        checks = [
            ValidationCheck(
                category="Transient diffusion",
                name=name,
                status="passed"
                if execution.get(section, {}).get("checks", {}).get(name) is True
                else "failed",
                message=f"Required real readback/check: {name}",
            )
            for section, names in (("build_readback", BUILD), ("validation", SCIENCE))
            for name in names
        ]
        validation = execution.get("validation", {})
        for name, limit in (
            ("relative_profile_error", 0.002),
            ("relative_mass_error", 1e-5),
            ("mesh_relative_difference", 0.002),
            ("time_relative_difference", 0.002),
        ):
            value = validation.get(name)
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= limit
            )
            checks.append(
                ValidationCheck(
                    category="Independent threshold",
                    name=name,
                    status="passed" if valid else "failed",
                    message=f"{name} <= {limit}",
                )
            )
        try:
            folder = Path(execution["artifacts"]["model"]).parent.resolve()
            records = validation["solves"]
            valid = len(records) == 4 and len({r["solve_id"] for r in records}) == 4
            valid &= (
                records[0]["dof"]
                < records[1]["dof"]
                < records[2]["dof"]
                == records[3]["dof"]
            )
            valid &= (
                records[3]["rtol"] < records[2]["rtol"]
                and records[3]["maximum_step_s"] < records[2]["maximum_step_s"]
            )
            for record in records:
                path = folder / record["field_file"]
                valid &= (
                    path.resolve().parent == folder
                    and hashlib.sha256(path.read_bytes()).hexdigest()
                    == record["sha256"]
                )
            values = {k: float(v.value) for k, v in plan.parameters.items()}
            error = 0.0
            with (folder / records[-1]["field_file"]).open(encoding="utf-8") as stream:
                rows = 0
                for row in csv.DictReader(stream):
                    t, x, c = (
                        float(row[k]) for k in ("time_s", "x_m", "comsol_concentration")
                    )
                    expected = values["mean_concentration"] + values[
                        "concentration_amplitude"
                    ] * math.exp(
                        -values["diffusion_coefficient"]
                        * math.pi**2
                        * t
                        / values["length"] ** 2
                    ) * math.cos(math.pi * x / values["length"])
                    if not all(math.isfinite(v) for v in (t, x, c)):
                        raise ValueError("Nonfinite raw concentration field")
                    error = max(
                        error,
                        abs(c - expected)
                        / max(values["concentration_amplitude"], 1e-30),
                    )
                    rows += 1
            valid &= rows > 0 and error <= 0.002
        except (KeyError, IndexError, OSError, TypeError, ValueError):
            valid = False
        checks.append(
            ValidationCheck(
                category="Independent artifact readback",
                name="raw_solver_field",
                status="passed" if valid else "failed",
                message="Reopen real c(x,t), recompute the reference and verify four mesh/time histories",
            )
        )
        return ValidationReport.from_checks(
            "solution",
            checks,
            command_success=execution.get("command_success") is True,
            model_state_verified=all(
                execution.get("build_readback", {}).get("checks", {}).get(k) is True
                for k in BUILD
            ),
        )
