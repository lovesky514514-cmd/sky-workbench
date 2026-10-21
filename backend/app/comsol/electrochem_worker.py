"""Real primary-current-distribution benchmark for the Electrochemistry Module."""

from __future__ import annotations

import argparse
import csv
import json
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
    print("WORKBENCH_EVENT:" + json.dumps({"event_type": event_type, "phase": phase, "label": label, "status": status, "command_success": status == "success" and event_type == "command", "model_state_verified": status == "success" and event_type == "model_change", "scientific_validation_passed": status == "success" and "solution validation" in normalized, **extra}, ensure_ascii=False), flush=True)


def result(payload: dict[str, Any]) -> None:
    print("WORKBENCH_RESULT:" + json.dumps(payload, ensure_ascii=False), flush=True)


def param(plan: dict[str, Any], name: str) -> tuple[float, str]:
    item = plan["parameters"][name]
    return float(item["value"]), str(item["unit"])


def timed(performance: dict[str, float], name: str, action: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return action()
    finally:
        performance[name] = round((time.perf_counter() - started) * 1000, 3)


def one_edge(component: Any, tag: str, *, xmin: float, xmax: float, ymin: float, ymax: float) -> list[int]:
    selection = component.selection().create(tag, "Box")
    selection.geom("geom1", 1)
    selection.set("entitydim", "1")
    selection.set("condition", "inside")
    for name, value in {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}.items():
        selection.set(name, value)
    return [int(value) for value in selection.entities()]


def main(request_path: Path) -> int:
    started = time.perf_counter()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan, run_id = request["plan"], str(request["run_id"])
    workspace = Path(request["workspace"]).resolve()
    models = workspace / "models"
    outputs = workspace / "outputs" / run_id
    reports = workspace / "reports" / run_id
    for directory in (models, outputs, reports):
        directory.mkdir(parents=True, exist_ok=True)
    length, lu = param(plan, "length")
    height, hu = param(plan, "height")
    conductivity, cu = param(plan, "electrolyte_conductivity")
    left_potential, lpu = param(plan, "left_potential")
    right_potential, rpu = param(plan, "right_potential")
    if (lu, hu, cu, lpu, rpu) != ("m", "m", "S/m", "V", "V") or min(length, height, conductivity) <= 0:
        raise ValueError("Primary-current benchmark requires m, S/m and V SI units with positive geometry/conductivity")
    name = re.sub(r"[^A-Za-z0-9_]", "_", f"primary_current_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{run_id[:8]}")
    model_path = models / f"{name}.mph"
    image_path = outputs / "electrolyte_potential.png"
    csv_path = outputs / "electrolyte_profile.csv"
    validation_path = reports / "validation.json"
    performance: dict[str, float] = {}
    client = None
    try:
        import jpype
        import mph
        import numpy as np

        emit("Build", "Starting real COMSOL for Electrochemistry license and API verification")
        client = timed(performance, "comsol_session_ms", lambda: mph.Client())
        model_util = jpype.JClass("com.comsol.model.util.ModelUtil")
        if not bool(model_util.hasProduct("ELECTROCHEMISTRY")):
            result({"success": False, "status": "MODULE_MISSING", "error": "Electrochemistry Module license is unavailable", "command_success": False, "model_state_verified": False, "scientific_validation_success": False})
            return 2
        model = client.create(name)
        java = model.java
        for key, expression in {"L": f"{length}[m]", "H": f"{height}[m]", "sigma_l": f"{conductivity}[S/m]", "phi_left": f"{left_potential}[V]", "phi_right": f"{right_potential}[V]"}.items():
            java.param().set(key, expression)
        parameter_readback = {key: str(java.param().get(key)) for key in ("L", "H", "sigma_l", "phi_left", "phi_right")}
        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 2)
        rectangle = geometry.feature().create("r1", "Rectangle")
        rectangle.set("size", ["L", "H"])
        geometry.run()
        tolerance = min(length, height) * 1e-5
        left = one_edge(component, "sel_left", xmin=-tolerance, xmax=tolerance, ymin=-tolerance, ymax=height + tolerance)
        right = one_edge(component, "sel_right", xmin=length - tolerance, xmax=length + tolerance, ymin=-tolerance, ymax=height + tolerance)
        if len(left) != 1 or len(right) != 1 or left == right:
            raise RuntimeError(f"BUILD_VALIDATION FAILED: invalid electrode edges {left}/{right}")
        physics = component.physics().create("cd", "PrimaryCurrentDistribution", "geom1")
        electrolyte = physics.feature("ice1")
        electrolyte.set("sigmal_mat", "userdef")
        electrolyte.set("sigmal", "sigma_l")
        left_bc = physics.feature().create("phil_left", "ElectrolytePotential", 1)
        left_bc.selection().set(left)
        left_bc.set("philbnd", "phi_left")
        right_bc = physics.feature().create("phil_right", "ElectrolytePotential", 1)
        right_bc.selection().set(right)
        right_bc.set("philbnd", "phi_right")
        mesh = component.mesh().create("mesh1")
        emit("Mesh", "Building primary-current mesh", progress_source="mesh.run")
        timed(performance, "mesh_ms", mesh.run)
        emit("Mesh", "Mesh built", "success", progress_source="mesh.run")
        study = java.study().create("std1")
        study.feature().create("stat", "Stationary")
        build_readback = {
            "physics_type": str(physics.getType()), "physics_selection": [int(item) for item in physics.selection().entities()],
            "electrolyte_type": str(electrolyte.getType()), "conductivity_mode": str(electrolyte.getString("sigmal_mat")), "conductivity": str(electrolyte.getString("sigmal")),
            "left": {"type": str(left_bc.getType()), "selection": [int(item) for item in left_bc.selection().entities()], "potential": str(left_bc.getString("philbnd"))},
            "right": {"type": str(right_bc.getType()), "selection": [int(item) for item in right_bc.selection().entities()], "potential": str(right_bc.getString("philbnd"))},
            "study": [str(item) for item in study.feature().tags()], "parameters": parameter_readback,
        }
        checks = {
            "primary_interface": build_readback["physics_type"] == "PrimaryCurrentDistribution",
            "domain_nonempty": bool(build_readback["physics_selection"]),
            "electrolyte": build_readback["electrolyte_type"] == "Electrolyte",
            "conductivity": build_readback["conductivity_mode"] == "userdef" and build_readback["conductivity"] == "sigma_l",
            "left_potential": build_readback["left"]["type"] == "ElectrolytePotential" and build_readback["left"]["selection"] == left and build_readback["left"]["potential"] == "phi_left",
            "right_potential": build_readback["right"]["type"] == "ElectrolytePotential" and build_readback["right"]["selection"] == right and build_readback["right"]["potential"] == "phi_right",
            "stationary": build_readback["study"] == ["stat"], "parameter_readback": all(parameter_readback.values()),
        }
        build_readback["checks"] = checks
        if not all(checks.values()):
            raise RuntimeError(f"BUILD_VALIDATION FAILED: {checks}")
        emit("Build Validation", "Electrochemistry write/readback/compare passed", "success", output_summary=json.dumps(checks))
        emit("Solve", "Primary Current Distribution study started", progress_source="study.run")
        timed(performance, "solve_ms", study.run)
        emit("Solve", "Primary Current Distribution study finished", "success", progress_source="study.run")
        x = np.real(np.asarray(model.evaluate("x"))).reshape(-1)
        y = np.real(np.asarray(model.evaluate("y"))).reshape(-1)
        potential = np.real(np.asarray(model.evaluate("phil"))).reshape(-1)
        current = np.real(np.asarray(model.evaluate("cd.IlMag"))).reshape(-1)
        if not (x.size == y.size == potential.size == current.size) or not all(np.all(np.isfinite(value)) for value in (x, y, potential, current)):
            raise RuntimeError("SOLUTION_VALIDATION FAILED: empty/nonfinite electrochemistry fields")
        expected_potential = left_potential + (right_potential - left_potential) * x / length
        expected_current = conductivity * abs(right_potential - left_potential) / length
        rmse = float(np.sqrt(np.mean((potential - expected_potential) ** 2)))
        current_mean = float(np.mean(current))
        scale = max(abs(right_potential - left_potential), 1e-12)
        solution_checks = {"solver_completed": True, "finite_fields": True, "potential_linear": rmse <= scale * 1e-5, "current_density": abs(current_mean - expected_current) / max(expected_current, 1e-30) <= 1e-4, "potential_bounds": float(np.min(potential)) >= min(left_potential, right_potential) - scale * 1e-6 and float(np.max(potential)) <= max(left_potential, right_potential) + scale * 1e-6}
        validation = {"passed": all(solution_checks.values()), "checks": solution_checks, "node_count": int(x.size), "dof": int(x.size), "potential_min_V": float(np.min(potential)), "potential_max_V": float(np.max(potential)), "potential_rmse_V": rmse, "current_density_mean_A_per_m2": current_mean, "expected_current_density_A_per_m2": expected_current, "current_density_relative_error": abs(current_mean - expected_current) / max(expected_current, 1e-30)}
        if not validation["passed"]:
            raise RuntimeError(f"SOLUTION_VALIDATION FAILED: {validation}")
        emit("Solution Validation", "Electrochemistry analytical validation passed", "success", output_summary=json.dumps(validation))
        results = java.result()
        plot = results.create("pg_phi", "PlotGroup2D")
        surface = plot.feature().create("surf1", "Surface")
        surface.set("expr", "phil")
        surface.set("unit", "V")
        plot.run()
        image = results.export().create("img_phi", "Image")
        image.set("plotgroup", "pg_phi")
        image.set("filename", str(image_path))
        image.set("imagetype", "png")
        image.run()
        order = np.argsort(x)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle); writer.writerow(["x_m", "y_m", "phil_V", "cd_IlMag_A_per_m2"]); writer.writerows((float(x[i]), float(y[i]), float(potential[i]), float(current[i])) for i in order)
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        model.save(str(model_path))
        performance["worker_total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result({"success": True, "status": "VERIFIED", "command_success": True, "model_state_verified": True, "scientific_validation_success": True, "run_id": run_id, "comsol_version": str(client.version), "artifacts": {"model": str(model_path), "potential_image": str(image_path), "electrolyte_profile": str(csv_path), "validation": str(validation_path), "report_directory": str(reports)}, "validation": validation, "build_readback": build_readback, "performance": performance, "model_state": {"geometry": [f"2D electrolyte rectangle {length:g} × {height:g} m"], "materials": ["Electrolyte conductivity (user-defined)"], "physics": ["Primary Current Distribution (cd)"], "parameters": plan["parameters"], "selections": [f"Left electrode {left}", f"Right electrode {right}"], "boundary_conditions": [f"Left electrolyte potential {left_potential:g} V", f"Right electrolyte potential {right_potential:g} V", "Remaining boundaries: Insulation"], "mesh": [f"Physics-controlled mesh; {x.size} solution nodes"], "study": ["Stationary (std1/stat)"], "solver": ["COMSOL primary-current stationary solver; completed"], "results": [f"Potential {float(np.min(potential)):.6g} to {float(np.max(potential)):.6g} V", f"Electrolyte current density {current_mean:.6g} A/m^2"]}})
        client.clear()
        return 0
    except Exception as exc:
        result({"success": False, "status": "FAILED", "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-12000:]})
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--request", type=Path, required=True)
    sys.exit(main(parser.parse_args().request))
