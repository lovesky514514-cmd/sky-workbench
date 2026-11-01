"""Isolated 3D conduction with reviewed convection and grey-body radiation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import traceback
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.comsol.heat_worker import emit, result
from app.scientific.cooling import UNITS
from app.scientific.schemas import ScientificPlan
from app.source_revision import source_revision
from app.validators.physics.cooling_validator import CoolingValidator
from app.scientific.solver_gate import require_solver_inputs

SIGMA = 5.670374419e-8
FLUX = "heat_transfer_coefficient*(ambient_temperature-T)+emissivity*sigma_const*(ambient_temperature^4-T^4)"
CONVECTION = "heat_transfer_coefficient*(T-ambient_temperature)"
RADIATION = "emissivity*sigma_const*(T^4-ambient_temperature^4)"
PROPERTY_UNITS = {
    "property_reference_temperature": "K",
    "thermal_conductivity_temperature_coefficient": "1/K",
    "heat_capacity_temperature_coefficient": "1/K",
}


def lumped_reference(p, times):
    """Independent RK4 energy ODE; applicable only when Biot is small."""
    w, h, l = (p[k] for k in ("width", "height", "length"))
    area, volume = 2 * (w * h + w * l + h * l), w * h * l
    ambient = p["ambient_temperature"]
    capacity = p["density"] * p["heat_capacity"] * volume
    reference = p.get("property_reference_temperature", ambient)
    alpha = p.get("heat_capacity_temperature_coefficient", 0)

    def rate(temperature):
        loss = p["heat_transfer_coefficient"] * (temperature - ambient)
        loss += p["emissivity"] * SIGMA * (temperature**4 - ambient**4)
        return -area * loss / (capacity * (1 + alpha * (temperature - reference)))

    value, previous, output = p["initial_temperature"], 0.0, []
    for target in times:
        while previous < target - 1e-12:
            step = min(0.05, target - previous)
            k1 = rate(value)
            k2 = rate(value + step * k1 / 2)
            k3 = rate(value + step * k2 / 2)
            k4 = rate(value + step * k3)
            value += step * (k1 + 2 * k2 + 2 * k3 + k4) / 6
            previous += step
        output.append(value)
    return output


def cooling_sample_times(parameters):
    """Keep requested outputs while resolving early heat loss for quadrature."""
    import numpy as np

    duration = parameters["duration"]
    requested = np.append(
        np.arange(0, duration, parameters["output_interval"]), duration
    )
    w, h, l = (parameters[k] for k in ("width", "height", "length"))
    initial, ambient = (
        parameters[k] for k in ("initial_temperature", "ambient_temperature")
    )
    radiation_h = (
        parameters["emissivity"]
        * SIGMA
        * (initial + ambient)
        * (initial**2 + ambient**2)
    )
    reference = parameters.get("property_reference_temperature", ambient)
    capacity_factor = min(
        1 + parameters.get("heat_capacity_temperature_coefficient", 0) * (t - reference)
        for t in (initial, ambient)
    )
    capacity = parameters["density"] * parameters["heat_capacity"] * w * h * l
    conductance = (
        2
        * (w * h + w * l + h * l)
        * (parameters["heat_transfer_coefficient"] + radiation_h)
    )
    first = max(
        np.finfo(float).tiny,
        min(duration / 600, capacity * capacity_factor / conductance / 50),
    )
    # Uniform late-time coverage and geometric early samples serve independent
    # energy integration. Neither changes the user's requested CSV timestamps.
    audit = np.unique(
        np.concatenate(
            (
                requested,
                np.linspace(0, duration, 601),
                np.geomspace(first, duration, 601),
            )
        )
    )
    return requested, audit


def solve(plan, workspace):
    import mph
    import numpy as np

    require_solver_inputs(plan)
    units = {
        **UNITS,
        **(
            PROPERTY_UNITS
            if plan.cooling_model.property_law == "linear_temperature"
            else {}
        ),
    }
    parameters = {key: float(plan.parameters[key].value) for key in units}
    w, h, l = (parameters[k] for k in ("width", "height", "length"))
    volume, area = w * h * l, 2 * (w * h + w * l + h * l)
    initial, ambient = (
        parameters[k] for k in ("initial_temperature", "ambient_temperature")
    )
    requested_times, times = cooling_sample_times(parameters)
    folder = workspace / "outputs"
    folder.mkdir(parents=True, exist_ok=False)
    client = mph.Client(cores=1)
    try:
        emit(
            "Build",
            "Creating a new 3D cooling model; existing Desktop models are not accessed",
        )
        model = client.create("sky_iron_cooling")
        java = model.java
        for name, value in parameters.items():
            java.param().set(
                name,
                f"{value:.16g}[{units[name]}]"
                if units[name] != "1"
                else f"{value:.16g}",
            )
        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 3)
        geometry.lengthUnit("m")
        block = geometry.feature().create("blk1", "Block")
        block.set("size", [w, h, l])
        geometry.run()
        heat = component.physics().create("ht", "HeatTransfer", "geom1")
        solid = heat.feature("solid1")
        expressions = {
            "k": "thermal_conductivity",
            "rho": "density",
            "Cp": "heat_capacity",
        }
        alpha, tref, enthalpy = 0.0, ambient, "T"
        if plan.cooling_model.property_law == "linear_temperature":
            expressions["k"] += (
                "*(1+thermal_conductivity_temperature_coefficient*(T-property_reference_temperature))"
            )
            expressions["Cp"] += (
                "*(1+heat_capacity_temperature_coefficient*(T-property_reference_temperature))"
            )
            alpha = parameters["heat_capacity_temperature_coefficient"]
            tref = parameters["property_reference_temperature"]
            enthalpy = "T+heat_capacity_temperature_coefficient*(T-property_reference_temperature)^2/2"
        for key, expression in expressions.items():
            solid.set(key + "_mat", "userdef")
            solid.set(key, expression)
        material = component.material().create("mat1", "Common")
        material.label(
            "Iron exploratory constant properties; not experimentally calibrated"
        )
        heat.feature("init1").set("Tinit", "initial_temperature")
        flux = heat.feature().create("cooling", "HeatFluxBoundary", 2)
        flux.selection().all()
        flux.set("q0", FLUX)
        for tag, kind, dimension in (
            ("aveop1", "Average", 3),
            ("minop1", "Minimum", 3),
            ("maxop1", "Maximum", 3),
            ("intop1", "Integration", 2),
        ):
            coupling = component.cpl().create(tag, kind)
            coupling.selection().geom("geom1", dimension)
            coupling.selection().all()
        mesh = component.mesh().create("mesh1")
        study = java.study().create("std1")
        transient = study.feature().create("time", "Transient")
        time_list = " ".join(f"{value:.16g}" for value in times)
        transient.set("tlist", time_list)
        profiles, meshes, energy_profiles, enthalpies, flux_peaks = [], [], [], [], []
        settings = [
            (
                level,
                plan.cooling_model.relative_tolerance,
                parameters["output_interval"],
            )
            for level in plan.cooling_model.mesh_levels
        ]
        settings.append(
            (
                plan.cooling_model.mesh_levels[-1],
                plan.cooling_model.relative_tolerance / 10,
                parameters["output_interval"] / 2,
            )
        )
        for index, (level, tolerance, max_step) in enumerate(settings):
            started = time.monotonic()
            emit("Mesh", f"Solving cooling case with mesh level {level}")
            if "sol1" in [str(tag) for tag in java.sol().tags()]:
                java.sol("sol1").clearSolutionData()
            mesh.autoMeshSize(level)
            mesh.run()
            study.createAutoSequences("all")
            time_solver = java.sol("sol1").feature("t1")
            # Preserve the specified uniform initial field and resolve the early cooling layer.
            time_solver.set("rtol", str(tolerance))
            time_solver.set("consistent", "off")
            time_solver.set("maxstepconstraintbdf", "const")
            time_solver.set("maxstepbdf", str(max_step))
            emit("Solve", f"Computing transient cooling with mesh level {level}")
            java.sol("sol1").runAll()
            read = lambda expression: np.real(
                np.asarray(model.evaluate(expression))
            ).reshape(-1)
            observed_times = read("aveop1(t)")
            average, minimum, maximum = (
                read(f"{name}(T)") for name in ("aveop1", "minop1", "maxop1")
            )
            outward = read("intop1(-(" + FLUX + "))")
            profiles.append((observed_times, average, minimum, maximum, outward))
            convective, radiative = (
                read(f"intop1({CONVECTION})"),
                read(f"intop1({RADIATION})"),
            )
            energy_profiles.append((convective, radiative))
            enthalpies.append(read(f"aveop1({enthalpy})"))
            flux_peaks.append(
                float(
                    read(
                        f"maxop1(sqrt(d(T,x)^2+d(T,y)^2+d(T,z)^2)*({expressions['k']}))"
                    )[-1]
                )
            )
            history = folder / f"convergence_{index + 1}.csv"
            with history.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    [
                        "time_s",
                        "average_K",
                        "minimum_K",
                        "maximum_K",
                        "outward_heat_W",
                        "convection_W",
                        "radiation_W",
                    ]
                )
                writer.writerows(zip(*profiles[-1], convective, radiative))
            meshes.append(
                {
                    "auto_mesh_size": level,
                    "dof": int(java.sol("sol1").getSize()[0]),
                    "elements": int(mesh.getNumElem()),
                    "solve_id": str(uuid.uuid4()),
                    "rtol": float(time_solver.getDouble("rtol")),
                    "maximum_step_s": float(time_solver.getDouble("maxstepbdf")),
                    "elapsed_s": time.monotonic() - started,
                    "history_file": history.name,
                    "history_sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
                    "final_average_K": float(average[-1]),
                }
            )
        emit(
            "Solution Validation",
            "Checking energy balance, reference and mesh consistency",
        )
        t, average, minimum, maximum, outward = profiles[-1]
        scale = initial - ambient
        reference = np.asarray(lumped_reference(parameters, t))
        energy_change = (
            parameters["density"]
            * parameters["heat_capacity"]
            * volume
            * (initial + alpha * (initial - tref) ** 2 / 2 - enthalpies[-1][-1])
        )
        energy_out = float(np.trapezoid(outward, t))
        convective_energy, radiative_energy = (
            float(np.trapezoid(component, t)) for component in energy_profiles[-1]
        )
        energy_error = float(
            abs(energy_out - energy_change) / max(abs(energy_change), 1e-30)
        )
        reference_error = float(np.max(np.abs(average - reference)) / scale)
        mesh_differences = {
            name: float(np.max(np.abs(profiles[1][index] - profiles[2][index])) / scale)
            for index, name in ((1, "average"), (2, "minimum"), (3, "maximum"))
        }
        mesh_difference = max(mesh_differences.values())
        time_difference = float(
            max(
                np.max(np.abs(profiles[2][index] - profiles[3][index])) / scale
                for index in (1, 2, 3)
            )
        )
        radiation_h = (
            parameters["emissivity"]
            * SIGMA
            * (initial + ambient)
            * (initial**2 + ambient**2)
        )
        minimum_k = parameters["thermal_conductivity"] * min(
            1
            + parameters.get("thermal_conductivity_temperature_coefficient", 0)
            * (temperature - tref)
            for temperature in (initial, ambient)
        )
        biot = (
            (parameters["heat_transfer_coefficient"] + radiation_h)
            * (volume / area)
            / minimum_k
        )
        parameter_readback = {
            name: float(java.param().evaluate(name)) for name in parameters
        }
        actual_size = [float(value) for value in block.getDoubleArray("size")]
        faces = [int(value) for value in flux.selection().entities()]
        build = {
            "geometry_size": bool(np.allclose(actual_size, [w, h, l], rtol=1e-10)),
            "six_faces": int(geometry.getNBoundaries()) == 6 and len(faces) == 6,
            "heat_interface": str(heat.getType()) == "HeatTransfer",
            "material_properties": all(
                str(solid.getString(k)) == expression
                for k, expression in expressions.items()
            ),
            "initial_temperature": str(heat.feature("init1").getString("Tinit"))
            == "initial_temperature",
            "all_faces_flux": str(flux.getString("q0")) == FLUX and len(faces) == 6,
            "transient_study": str(transient.getType()) == "Transient"
            and str(transient.getString("tlist")) == time_list,
            "parameter_readback": all(
                np.isclose(parameter_readback[k], v, rtol=1e-10)
                for k, v in parameters.items()
            ),
        }
        checks = {
            "solver_completed": len(t) == len(times),
            "finite_fields": bool(all(np.all(np.isfinite(x)) for x in profiles[-1])),
            "temperature_bounds": bool(
                minimum.min() >= ambient - 0.01 and maximum.max() <= initial + 0.01
            ),
            "monotonic_cooling": bool(
                np.all(np.diff(average) <= 0.01) and average[-1] < initial
            ),
            "requested_time_reached": bool(np.allclose(t, times, rtol=1e-9, atol=1e-9)),
            "energy_balance": energy_error <= 0.02,
            "small_biot_reference": biot < 0.1 and reference_error <= 0.02,
            "mesh_agreement": mesh_difference <= 0.01
            and meshes[0]["dof"] < meshes[1]["dof"] < meshes[2]["dof"],
            "time_agreement": time_difference <= 0.002,
            "independent_resolves": len({m["solve_id"] for m in meshes}) == 4
            and all(m["elements"] > 0 for m in meshes),
            "energy_components": convective_energy >= 0
            and radiative_energy >= 0
            and abs(energy_out - convective_energy - radiative_energy)
            <= 1e-6 * max(energy_out, 1e-30),
        }
        validation = {
            "passed": all(checks.values()),
            "checks": checks,
            "energy_relative_error": energy_error,
            "reference_relative_error": reference_error,
            "mesh_relative_difference": mesh_difference,
            "biot_upper_estimate": biot,
            "meshes": meshes,
            "dof": meshes[-1]["dof"],
            "time_relative_tolerance": float(time_solver.getDouble("rtol")),
            "energy_loss_J": float(energy_change),
            "boundary_heat_loss_J": energy_out,
            "final_average_C": float(average[-1] - 273.15),
            "final_minimum_C": float(minimum[-1] - 273.15),
            "final_maximum_C": float(maximum[-1] - 273.15),
            "initial_temperature_C": initial - 273.15,
            "validation_scope": "Numerical consistency of the reviewed constant-property equation only",
            "material_calibration": "UNVERIFIED; temperature-dependent properties and transformations excluded",
            "requested_output_interval_s": parameters["output_interval"],
            "validation_sample_count": len(times),
        }
        validation.update(
            mesh_temperature_differences=mesh_differences,
            time_relative_difference=time_difference,
            convective_energy_J=convective_energy,
            radiative_energy_J=radiative_energy,
            peak_flux_W_per_m2=flux_peaks,
            property_law=plan.cooling_model.property_law,
            validation_scope="Numerical consistency of the reviewed PDE; mesh checks cover mean and extreme temperatures, not full-field gradient certification",
            material_calibration="UNVERIFIED; physical property coefficients require independent data",
        )
        csv_path = folder / "cooling_history.csv"
        audit_path = folder / "cooling_validation_history.csv"
        columns = (
            t,
            average - 273.15,
            minimum - 273.15,
            maximum - 273.15,
            outward,
            reference - 273.15,
            *energy_profiles[-1],
        )
        requested_indices = np.searchsorted(times, requested_times)
        for path, indices in (
            (csv_path, requested_indices),
            (audit_path, range(len(t))),
        ):
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    [
                        "time_s",
                        "average_C",
                        "minimum_C",
                        "maximum_C",
                        "outward_heat_W",
                        "reference_C",
                        "convection_W",
                        "radiation_W",
                    ]
                )
                writer.writerows(
                    tuple(column[index] for column in columns) for index in indices
                )
        pg = java.result().create("pg_cooling", "PlotGroup3D")
        surface = pg.feature().create("surf1", "Surface")
        surface.set("expr", "T")
        surface.set("unit", "degC")
        pg.run()
        image = java.result().export().create("img_cooling", "Image")
        image.set("plotgroup", "pg_cooling")
        image_path = folder / "temperature_final.png"
        image.set("filename", str(image_path))
        image.set("imagetype", "png")
        image.run()
        model_path = folder / "block_cooling.mph"
        model.save(str(model_path))
        validation_path = folder / "validation.json"
        validation_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
        return {
            "success": True,
            "command_success": True,
            "model_state_verified": all(build.values()),
            "scientific_validation_success": all(checks.values()),
            "comsol_version": str(client.version),
            "code_revision": source_revision(),
            "validation": validation,
            "build_readback": {
                "checks": build,
                "geometry_size_m": actual_size,
                "faces": faces,
                "parameters": parameter_readback,
                "time_solver": {
                    "rtol": str(time_solver.getString("rtol")),
                    "consistent": str(time_solver.getString("consistent")),
                },
            },
            "artifacts": {
                "model": str(model_path),
                "temperature_image": str(image_path),
                "cooling_history": str(csv_path),
                "cooling_validation_history": str(audit_path),
                "validation": str(validation_path),
            },
            "model_state": {
                "geometry": [f"3D block {w:g} × {h:g} × {l:g} m"],
                "materials": [str(material.label())],
                "physics": ["Heat Transfer in Solids"],
                "parameters": plan.model_dump(mode="json")["parameters"],
                "boundary_conditions": [
                    "All six faces: reviewed convection and radiation"
                ],
                "mesh": [str(meshes)],
                "study": ["Time Dependent"],
                "solver": ["COMSOL transient solver"],
                "results": [f"Final mean temperature {average[-1] - 273.15:.3f} °C"],
            },
        }
    finally:
        client.clear()


def main(path):
    try:
        request = json.loads(path.read_text(encoding="utf-8-sig"))
        plan = ScientificPlan.model_validate(request["plan"])
        require_solver_inputs(plan)
        precheck = CoolingValidator().precheck(plan)
        if not precheck.passed:
            raise ValueError(
                "Cooling precheck rejected: "
                + str([c.name for c in precheck.checks if c.status == "failed"])
            )
        payload = solve(plan, Path(request["workspace"]).resolve())
        payload["run_id"] = request["run_id"]
        result(payload)
        return 0
    except Exception as error:
        result(
            {
                "success": False,
                "command_success": False,
                "model_state_verified": False,
                "scientific_validation_success": False,
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-8000:],
            }
        )
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().request))
