"""Isolated real COMSOL worker for a 3D uniaxial Solid Mechanics bar."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def emit(phase: str, label: str, status: str = "running", **extra: Any) -> None:
    normalized = phase.lower().replace("_", " ")
    event_type = str(extra.pop("event_type", "model_change" if "build validation" in normalized else "progress" if normalized in {"solve", "mesh", "export"} else "command"))
    print("WORKBENCH_EVENT:" + json.dumps({
        "event_type": event_type,
        "phase": phase,
        "label": label,
        "status": status,
        "command_success": bool(status == "success" and event_type == "command"),
        "model_state_verified": bool(status == "success" and event_type == "model_change"),
        "scientific_validation_passed": bool(status == "success" and "solution validation" in normalized),
        **extra,
    }, ensure_ascii=False), flush=True)


def result(payload: dict[str, Any]) -> None:
    print("WORKBENCH_RESULT:" + json.dumps(payload, ensure_ascii=False), flush=True)


def parameter(plan: dict[str, Any], name: str) -> tuple[float, str]:
    raw = plan["parameters"][name]
    return float(raw["value"]), str(raw["unit"])


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)[:72]


def timed(performance: dict[str, float], name: str, action: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return action()
    finally:
        performance[name] = round((time.perf_counter() - started) * 1000, 3)


def one_face(component: Any, tag: str, bounds: dict[str, float]) -> list[int]:
    selection = component.selection().create(tag, "Box")
    selection.geom("geom1", 2)
    selection.set("entitydim", "2")
    selection.set("condition", "inside")
    for name, value in bounds.items():
        selection.set(name, value)
    return [int(value) for value in selection.entities()]


def read(feature: Any, name: str) -> str:
    return str(feature.getString(name))


def mesh_statistics(mesh: Any, node_count: int) -> dict[str, Any]:
    report: dict[str, Any] = {"node_count": node_count, "element_count": None, "readback_method": "solution-node count"}
    for method_name in ("getNumElem", "getNumElements"):
        try:
            raw = getattr(mesh, method_name)()
            values = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
            report["element_count"] = sum(int(value) for value in values)
            report["readback_method"] = f"mesh.{method_name}()"
            break
        except Exception:
            continue
    return report


def main(request_path: Path) -> int:
    total_started = time.perf_counter()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan = request["plan"]
    run_id = str(request["run_id"])
    workspace = Path(request["workspace"]).resolve()
    models_dir = workspace / "models"
    outputs_dir = workspace / "outputs" / run_id
    reports_dir = workspace / "reports" / run_id
    for directory in (models_dir, outputs_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    length, length_unit = parameter(plan, "length")
    width, width_unit = parameter(plan, "width")
    height, height_unit = parameter(plan, "height")
    young, young_unit = parameter(plan, "youngs_modulus")
    poisson, poisson_unit = parameter(plan, "poissons_ratio")
    density, density_unit = parameter(plan, "density")
    traction, traction_unit = parameter(plan, "traction")
    if {length_unit, width_unit, height_unit} != {"m"}:
        raise ValueError("Solid benchmark geometry must use metres")
    if young_unit != "Pa" or traction_unit != "Pa" or poisson_unit not in {"1", ""} or density_unit != "kg/m^3":
        raise ValueError("Solid benchmark requires E/traction in Pa, dimensionless nu and density in kg/m^3")
    if min(length, width, height, young, density, traction) <= 0 or not 0 <= poisson < 0.5:
        raise ValueError("Solid benchmark parameters are outside the supported physical range")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    model_name = safe_name(f"solid_uniaxial_bar_{timestamp}_{run_id[:8]}")
    model_path = models_dir / f"{model_name}.mph"
    image_path = outputs_dir / "solid_mises.png"
    csv_path = outputs_dir / "displacement_profile.csv"
    validation_path = reports_dir / "validation.json"
    performance: dict[str, float] = {}
    client = None

    try:
        import mph
        import numpy as np

        emit("Build", "Starting a clean real COMSOL/MPh process", input_summary={"run_id": run_id})
        client = timed(performance, "comsol_session_ms", lambda: mph.Client())
        model = client.create(model_name)
        java = model.java

        def configure_parameters() -> None:
            params = java.param()
            for name, expression in {
                "L": f"{length:.16g}[m]", "W": f"{width:.16g}[m]", "H": f"{height:.16g}[m]",
                "E_bar": f"{young:.16g}[Pa]", "nu_bar": f"{poisson:.16g}",
                "rho_bar": f"{density:.16g}[kg/m^3]", "traction_bar": f"{traction:.16g}[Pa]",
            }.items():
                params.set(name, expression)

        timed(performance, "parameter_write_ms", configure_parameters)
        parameter_readback = {name: str(java.param().get(name)) for name in ("L", "W", "H", "E_bar", "nu_bar", "rho_bar", "traction_bar")}

        emit("Build", "Creating the 3D uniaxial bar")
        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 3)
        geometry.lengthUnit("m")
        block = geometry.feature().create("blk1", "Block")
        block.set("size", ["L", "W", "H"])
        block.set("pos", ["0", "0", "0"])
        timed(performance, "geometry_ms", geometry.run)
        tolerance = min(length, width, height) * 1e-5
        common = {"ymin": -tolerance, "ymax": width + tolerance, "zmin": -tolerance, "zmax": height + tolerance}
        left = one_face(component, "sel_left", {**common, "xmin": -tolerance, "xmax": tolerance})
        right = one_face(component, "sel_right", {**common, "xmin": length - tolerance, "xmax": length + tolerance})
        if len(left) != 1 or len(right) != 1 or left == right:
            raise RuntimeError(f"BUILD_VALIDATION FAILED: invalid face selections left={left}, right={right}")

        emit("Build", "Creating Solid Mechanics and benchmark material")
        solid = component.physics().create("solid", "SolidMechanics", "geom1")
        elastic = solid.feature("lemm1")
        elastic.set("E_mat", "userdef")
        elastic.set("E", "E_bar")
        elastic.set("nu_mat", "userdef")
        elastic.set("nu", "nu_bar")
        elastic.set("rho_mat", "userdef")
        elastic.set("rho", "rho_bar")
        fixed = solid.feature().create("fix1", "Fixed", 2)
        fixed.selection().set(left)
        load = solid.feature().create("bndl1", "BoundaryLoad", 2)
        load.selection().set(right)
        load.set("forceType", "ForceArea")
        load.set("force_src", "userdef")
        load.set("force", ["traction_bar", "0", "0"])
        load.set("F", ["traction_bar", "0", "0"])
        load.set("FperArea", ["traction_bar", "0", "0"])

        emit("Mesh", "Building physics-controlled tetrahedral mesh", progress_source="mesh.run")
        mesh = component.mesh().create("mesh1")
        timed(performance, "mesh_ms", mesh.run)
        emit("Mesh", "Mesh built", "success", progress_source="mesh.run")
        study = java.study().create("std1")
        study.label("Stationary uniaxial tension")
        study.feature().create("stat", "Stationary")
        int_right = component.cpl().create("int_right", "Integration")
        int_right.selection().geom("geom1", 2)
        int_right.selection().set(right)

        build_readback = {
            "component_tag": str(component.tag()), "geometry_tag": str(geometry.tag()),
            "block_size": [str(value) for value in block.getStringArray("size")],
            "physics_tag": str(solid.tag()), "physics_type": str(solid.getType()),
            "physics_selection": [int(value) for value in solid.selection().entities()],
            "elastic_type": str(elastic.getType()), "E_mode": read(elastic, "E_mat"), "E": read(elastic, "E"),
            "nu_mode": read(elastic, "nu_mat"), "nu": read(elastic, "nu"),
            "rho_mode": read(elastic, "rho_mat"), "rho": read(elastic, "rho"),
            "fixed": {"type": str(fixed.getType()), "selection": [int(value) for value in fixed.selection().entities()]},
            "load": {"type": str(load.getType()), "selection": [int(value) for value in load.selection().entities()], "force_type": read(load, "forceType"), "force_source": read(load, "force_src"), "force": [str(value) for value in load.getStringArray("force")], "F": [str(value) for value in load.getStringArray("F")], "FperArea": [str(value) for value in load.getStringArray("FperArea")]},
            "study_features": [str(value) for value in study.feature().tags()],
            "parameter_expressions": parameter_readback,
            "face_map": {"left": left, "right": right},
        }
        checks = {
            "solid_interface": build_readback["physics_tag"] == "solid" and build_readback["physics_type"] == "SolidMechanics",
            "domain_nonempty": bool(build_readback["physics_selection"]),
            "elastic_model": build_readback["elastic_type"] == "LinearElasticModel",
            "youngs_modulus": build_readback["E_mode"] == "userdef" and build_readback["E"] == "E_bar",
            "poissons_ratio": build_readback["nu_mode"] == "userdef" and build_readback["nu"] == "nu_bar",
            "density": build_readback["rho_mode"] == "userdef" and build_readback["rho"] == "rho_bar",
            "fixed_face": build_readback["fixed"]["type"] == "Fixed" and build_readback["fixed"]["selection"] == left,
            "loaded_face": build_readback["load"]["type"] == "BoundaryLoad" and build_readback["load"]["selection"] == right,
            "traction": build_readback["load"]["force_type"] == "ForceArea" and build_readback["load"]["FperArea"] == ["traction_bar", "0", "0"],
            "stationary_study": build_readback["study_features"] == ["stat"],
            "parameter_readback": all(parameter_readback.values()),
        }
        build_readback["checks"] = checks
        if not all(checks.values()):
            raise RuntimeError(f"BUILD_VALIDATION FAILED: real COMSOL readback mismatch: {checks}")
        emit("Build Validation", "WRITE → real readback → compare → verify passed", "success", output_summary=json.dumps(checks))

        emit("Solve", "Stationary Solid Mechanics study started", progress_source="study.run")
        timed(performance, "solve_ms", study.run)
        emit("Solve", "Stationary solver finished", "success", progress_source="study.run")

        emit("Solution Validation", "Extracting displacement, stress and force equilibrium")
        x = np.real(np.asarray(model.evaluate("x"))).reshape(-1)
        u = np.real(np.asarray(model.evaluate("u"))).reshape(-1)
        mises = np.real(np.asarray(model.evaluate("solid.mises"))).reshape(-1)
        sx = np.real(np.asarray(model.evaluate("solid.sx"))).reshape(-1)
        if not (x.size == u.size == mises.size == sx.size) or not all(np.all(np.isfinite(value)) for value in (x, u, mises, sx)):
            raise RuntimeError("SOLUTION_VALIDATION FAILED: extracted fields are empty, nonfinite or mismatched")
        coordinate_tolerance = max(tolerance, length * 1e-8)
        right_mask = np.abs(x - length) <= coordinate_tolerance
        interior_mask = x >= length * 0.4
        expected_displacement = traction * length / young
        expected_force = traction * width * height
        right_displacement = float(np.median(u[right_mask]))
        axial_stress = float(np.median(sx[interior_mask]))
        von_mises = float(np.median(mises[interior_mask]))
        internal_force = float(np.real(np.asarray(model.evaluate("int_right(solid.sx)")).reshape(-1)[0]))
        displacement_relative_error = abs(right_displacement - expected_displacement) / expected_displacement
        stress_relative_error = abs(axial_stress - traction) / traction
        force_relative_error = abs(internal_force - expected_force) / expected_force
        mesh_report = mesh_statistics(mesh, int(u.size))
        solution_checks = {
            "solver_completed": True,
            "finite_fields": True,
            "positive_displacement": right_displacement > 0,
            "displacement_analytical": displacement_relative_error <= 0.05,
            "axial_stress_analytical": stress_relative_error <= 0.05,
            "von_mises_analytical": abs(von_mises - traction) / traction <= 0.08,
            "force_equilibrium": force_relative_error <= 0.05,
        }
        validation = {
            "passed": all(solution_checks.values()), "checks": solution_checks,
            "node_count": int(u.size), "element_count": mesh_report.get("element_count"),
            "dof": int(u.size),
            "right_displacement_m": right_displacement, "expected_displacement_m": expected_displacement,
            "displacement_relative_error": displacement_relative_error,
            "axial_stress_Pa": axial_stress, "expected_axial_stress_Pa": traction,
            "stress_relative_error": stress_relative_error, "von_mises_stress_Pa": von_mises,
            "von_mises_min_Pa": float(np.min(mises)),
            "von_mises_max_Pa": float(np.max(mises)),
            "internal_force_N": internal_force, "expected_force_N": expected_force,
            "force_equilibrium_relative_error": force_relative_error,
        }
        if not validation["passed"]:
            raise RuntimeError(f"SOLUTION_VALIDATION FAILED: {validation}")
        emit("Solution Validation", "Scientific validation passed", "success", output_summary=json.dumps(validation))

        emit("Export", "Exporting stress field, displacement profile and MPH")
        results = java.result()
        plot = results.create("pg_solid", "PlotGroup3D")
        plot.label("von Mises stress")
        surface = plot.feature().create("surf1", "Surface")
        surface.set("expr", "solid.mises")
        surface.set("unit", "Pa")
        plot.run()
        image = results.export().create("img_solid", "Image")
        image.set("plotgroup", "pg_solid")
        image.set("filename", str(image_path))
        image.set("imagetype", "png")
        image.run()
        order = np.argsort(x)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["x_m", "u_m", "solid_sx_Pa", "solid_mises_Pa"])
            writer.writerows((float(x[i]), float(u[i]), float(sx[i]), float(mises[i])) for i in order)
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        model.save(str(model_path))
        performance["worker_total_ms"] = round((time.perf_counter() - total_started) * 1000, 3)
        emit("Export", "Artifacts exported", "success", output_summary=f"{model_path.name}, {image_path.name}, {csv_path.name}")
        result({
            "success": True, "command_success": True, "model_state_verified": True,
            "scientific_validation_success": True, "run_id": run_id, "comsol_version": str(client.version),
            "artifacts": {"model": str(model_path), "stress_image": str(image_path), "displacement_profile": str(csv_path), "validation": str(validation_path), "report_directory": str(reports_dir)},
            "validation": validation, "build_readback": build_readback, "performance": performance,
            "model_state": {
                "geometry": [f"3D block {length:g} × {width:g} × {height:g} m"],
                "materials": ["Isotropic linear elastic benchmark material (user-defined E, nu, rho)"],
                "physics": ["Solid Mechanics (solid)", "Linear Elastic Material (lemm1)"],
                "parameters": plan["parameters"], "selections": [f"Left face {left}", f"Right face {right}"],
                "boundary_conditions": [f"Left fixed {left}", f"Right traction {traction:g} Pa {right}"],
                "mesh": [f"Physics-controlled tetrahedral mesh; {mesh_report.get('element_count')} elements; {u.size} solution nodes"],
                "study": ["Stationary (std1/stat)"], "solver": ["COMSOL stationary Solid Mechanics solver; completed"],
                "results": [f"Right displacement {right_displacement:.6g} m", f"Axial stress {axial_stress:.6g} Pa", f"Force resultant {internal_force:.6g} N"],
            },
        })
        try:
            client.clear()
        except Exception as cleanup_exc:
            emit("System", "COMSOL cleanup warning", "warning", output_summary=str(cleanup_exc))
        return 0
    except Exception as exc:
        emit("Repair", "Solid Mechanics workflow failed safely", "error", output_summary=str(exc), fallback=False)
        result({"success": False, "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-12000:]})
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    arguments = parser.parse_args()
    sys.exit(main(arguments.request))
