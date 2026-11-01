"""Real transient diffusion fields, mass balance and independent mesh/time reruns."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.comsol.heat_worker import emit, result
from app.scientific.schemas import ScientificPlan
from app.scientific.solver_gate import require_solver_inputs
from app.scientific.transient_diffusion import (
    INITIAL,
    UNITS,
)
from app.validators.physics.transient_diffusion_validator import (
    TransientDiffusionValidator,
)
from app.source_revision import source_revision


def solve(plan, workspace):
    import mph
    import numpy as np

    require_solver_inputs(plan)
    values = {key: float(plan.parameters[key].value) for key in UNITS}
    length, height, diffusion, mean, amplitude, duration, interval = (
        values[k]
        for k in (
            "length",
            "height",
            "diffusion_coefficient",
            "mean_concentration",
            "concentration_amplitude",
            "duration",
            "output_interval",
        )
    )
    times = np.arange(0, duration, interval).tolist() + [duration]
    folder = workspace / "outputs"
    folder.mkdir(parents=True, exist_ok=False)
    client = mph.Client(cores=1)
    try:
        model = client.create("sky_closed_diffusion")
        java = model.java
        for key, value in values.items():
            java.param().set(key, f"{value:.16g}[{UNITS[key]}]")
        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 2)
        geometry.lengthUnit("m")
        rectangle = geometry.feature().create("r1", "Rectangle")
        rectangle.set("size", ["length", "height"])
        geometry.run()
        physics = component.physics().create("tds", "DilutedSpecies", "geom1")
        transport = physics.feature("cdm1")
        transport.set("D_c_mat", "userdef")
        transport.set("D_c", "diffusion_coefficient")
        transport.set("u_src", "userdef")
        transport.set("u", ["0[m/s]", "0[m/s]", "0[m/s]"])
        initial = physics.feature("init1")
        initial.set("initc", INITIAL)
        sealed = physics.feature().create("sealed", "NoFlux", 1)
        sealed.selection().all()
        for tag, kind in (
            ("intop1", "Integration"),
            ("minop1", "Minimum"),
            ("maxop1", "Maximum"),
        ):
            operator = component.cpl().create(tag, kind)
            operator.selection().geom("geom1", 2)
            operator.selection().all()
        mesh = component.mesh().create("mesh1")
        study = java.study().create("std1")
        transient = study.feature().create("time", "Transient")
        transient.set("tlist", " ".join(f"{t:.16g}" for t in times))
        records, profiles, errors, masses = [], [], [], []
        for index, (level, tolerance, max_step) in enumerate(
            (
                (4, 1e-5, interval),
                (3, 1e-5, interval),
                (2, 1e-5, interval),
                (2, 1e-6, interval / 2),
            )
        ):
            if "sol1" in [str(tag) for tag in java.sol().tags()]:
                java.sol("sol1").clearSolutionData()
            mesh.autoMeshSize(level)
            mesh.run()
            study.createAutoSequences("all")
            solver = java.sol("sol1").feature("t1")
            solver.set("rtol", str(tolerance))
            solver.set("consistent", "off")
            solver.set("maxstepconstraintbdf", "const")
            solver.set("maxstepbdf", str(max_step))
            emit(
                "Solve",
                f"Closed transient diffusion: fresh mesh {level}, rtol {tolerance:g}",
            )
            java.sol("sol1").runAll()
            read = lambda expression: np.real(np.asarray(model.evaluate(expression)))
            concentrations = read("c")
            x = read("x")
            x = x[-1] if x.ndim > 1 else x
            y = read("y")
            y = y[-1] if y.ndim > 1 else y
            concentrations = concentrations.reshape(len(times), -1)
            exact = mean + amplitude * np.exp(
                -diffusion * math.pi**2 * np.asarray(times)[:, None] / length**2
            ) * np.cos(math.pi * x[None, :] / length)
            error = float(
                np.max(np.abs(concentrations - exact)) / max(amplitude, 1e-30)
            )
            mass = read("intop1(c)").reshape(-1)
            observed_times = read("intop1(t)/intop1(1)").reshape(-1)
            history = folder / f"field_{index + 1}.csv"
            with history.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    [
                        "time_s",
                        "x_m",
                        "y_m",
                        "comsol_concentration",
                        "analytic_concentration",
                    ]
                )
                for t, observed, reference in zip(
                    observed_times, concentrations, exact
                ):
                    writer.writerows(zip(np.full(x.size, t), x, y, observed, reference))
            records.append(
                {
                    "solve_id": str(uuid.uuid4()),
                    "mesh_level": level,
                    "dof": int(java.sol("sol1").getSize()[0]),
                    "element_count": int(mesh.getNumElem()),
                    "rtol": tolerance,
                    "maximum_step_s": max_step,
                    "field_file": history.name,
                    "sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
                }
            )
            # Point extrema are comparable across meshes even when nodes differ.
            profiles.append((concentrations.min(axis=1), concentrations.max(axis=1)))
            errors.append(error)
            masses.append(mass)
        emit('Solution Validation','Independently checking raw concentration fields, mass and convergence')
        initial_mass = mean * length * height
        mass_error = float(
            np.max(np.abs(masses[-1] - initial_mass)) / max(initial_mass, 1e-30)
        )
        mesh_difference = float(
            max(np.max(np.abs(a - b)) for a, b in zip(profiles[1], profiles[2]))
            / max(amplitude, 1e-30)
        )
        time_difference = float(
            max(np.max(np.abs(a - b)) for a, b in zip(profiles[2], profiles[3]))
            / max(amplitude, 1e-30)
        )
        parameter_readback = {k: float(java.param().evaluate(k)) for k in values}
        build = {
            "geometry": int(geometry.getNBoundaries()) == 4,
            "interface": str(physics.getType()) == "DilutedSpecies",
            "diffusion": str(transport.getString("D_c")) == "diffusion_coefficient",
            "initial_profile": str(initial.getString("initc")) == INITIAL,
            "sealed_boundaries": len(sealed.selection().entities()) == 4,
            "parameter_readback": all(
                np.isclose(parameter_readback[k], v, rtol=1e-10)
                for k, v in values.items()
            ),
            "transient_study": str(transient.getType()) == "Transient",
        }
        checks = {
            "finite_fields": bool(
                np.all(np.isfinite(concentrations)) and np.all(np.isfinite(masses[-1]))
            ),
            "requested_time": bool(np.allclose(observed_times, times)),
            "analytical_profile": errors[-1] <= 0.002,
            "mass_conservation": mass_error <= 1e-5,
            "physical_range": bool(
                concentrations.min() >= mean - amplitude - 0.002 * amplitude
                and concentrations.max() <= mean + amplitude + 0.002 * amplitude
            ),
            "mesh_agreement": mesh_difference <= 0.002
            and records[0]["dof"] < records[1]["dof"] < records[2]["dof"],
            "time_agreement": time_difference <= 0.002,
        }
        validation = {
            "passed": all(checks.values()),
            "checks": checks,
            "dof": records[-1]["dof"],
            "relative_profile_error": errors[-1],
            "relative_mass_error": mass_error,
            "mesh_relative_difference": mesh_difference,
            "time_relative_difference": time_difference,
            "solves": records,
            "raw_field_source": "COMSOL model.evaluate(c), model.evaluate(x), model.evaluate(y)",
            "validation_scope": "Constant isotropic diffusion, explicit cosine initial profile and sealed rectangular domain only",
        }
        mass_path = folder / "mass_history.csv"
        with mass_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["time_s", "mass_per_depth_mol_m"])
            writer.writerows(zip(observed_times, masses[-1]))
        model_path = folder / "closed_transient_diffusion.mph"
        model.save(str(model_path))
        validation_path = folder / "validation.json"
        validation_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
        artifacts = {
            "model": str(model_path),
            "validation": str(validation_path),
            "mass_history": str(mass_path),
            **{
                f"field_{i + 1}": str(folder / r["field_file"])
                for i, r in enumerate(records)
            },
        }
        return {
            "success": True,
            "command_success": True,
            "model_state_verified": all(build.values()),
            "scientific_validation_success": all(checks.values()),
            "run_id": None,
            "comsol_version": str(client.version),
            "code_revision": source_revision(),
            "validation": validation,
            "build_readback": {"checks": build, "parameters": parameter_readback},
            "artifacts": artifacts,
            "model_state": {
                "geometry": ["2D sealed rectangle"],
                "physics": ["Transport of Diluted Species"],
                "boundary_conditions": ["All boundaries No Flux"],
                "materials": ["Explicit D"],
                "parameters": plan.model_dump(mode="json")["parameters"],
                "mesh": [str(records)],
                "study": ["Time Dependent"],
                "solver": ["COMSOL transient BDF"],
                "results": ["Actual concentration fields and invariant mass history"],
            },
        }
    finally:
        client.clear()


def main(path):
    try:
        request = json.loads(path.read_text(encoding="utf-8-sig"))
        plan = ScientificPlan.model_validate(request["plan"])
        report = TransientDiffusionValidator().precheck(plan)
        if not report.passed:
            raise ValueError("Transient diffusion precheck failed")
        payload = solve(plan, Path(request["workspace"]))
        payload["run_id"] = request["run_id"]
        result(payload)
        return 0
    except Exception as exc:
        result(
            {
                "success": False,
                "command_success": False,
                "model_state_verified": False,
                "scientific_validation_success": False,
                "error": str(exc),
            }
        )
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().request))
