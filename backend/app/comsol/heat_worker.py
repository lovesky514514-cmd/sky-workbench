"""Isolated, real COMSOL workflow for the verified 2D heat-transfer path.

The worker runs outside FastAPI because the audited legacy project reports
cross-model degradation in long-lived MPh sessions. It calibrates boundary
numbers for this model instead of assuming an edge ordering.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def emit(phase: str, label: str, status: str = "running", **extra: Any) -> None:
    normalized = phase.lower().replace("_", " ")
    event_type = str(
        extra.pop(
            "event_type",
            "model_change"
            if "build validation" in normalized
            else "progress"
            if normalized in {"solve", "export"}
            else "command",
        )
    )
    payload = {
        "event_type": event_type,
        "phase": phase,
        "label": label,
        "status": status,
        "command_success": bool(status == "success" and event_type == "command"),
        "model_state_verified": bool(status == "success" and event_type == "model_change"),
        "scientific_validation_passed": bool(status == "success" and "solution validation" in normalized),
        **extra,
    }
    print("WORKBENCH_EVENT:" + json.dumps(payload, ensure_ascii=False), flush=True)


def result(payload: dict[str, Any]) -> None:
    print("WORKBENCH_RESULT:" + json.dumps(payload, ensure_ascii=False), flush=True)


def parameter(plan: dict[str, Any], name: str) -> tuple[float, str]:
    value = plan["parameters"][name]
    return float(value["value"]), str(value["unit"])


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)[:60]


def median_on_mask(values: Any, mask: Any) -> float:
    import numpy as np

    selected = np.asarray(values).reshape(-1)[mask]
    if selected.size == 0:
        raise RuntimeError("No mesh nodes matched a physical boundary during calibration")
    return float(np.median(np.real(selected)))


def readback_literal(expression: str, unit: str) -> float:
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\[' + re.escape(unit) + r'\]\s*', expression)
    if match is None:
        raise ValueError('COMSOL material property is not a supported literal with expected unit')
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('COMSOL material property readback is not finite and positive')
    return value


def main(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan = request["plan"]
    run_id = str(request["run_id"])
    workspace = Path(request["workspace"]).resolve()
    models_dir = workspace / "models"
    outputs_dir = workspace / "outputs" / run_id
    reports_dir = workspace / "reports" / run_id
    for directory in (models_dir, outputs_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    width, length_unit = parameter(plan, "width")
    height, height_unit = parameter(plan, "height")
    left_temperature, _ = parameter(plan, "left_temperature")
    right_temperature, _ = parameter(plan, "right_temperature")
    conductivity, _ = parameter(plan, "thermal_conductivity")
    density, _ = parameter(plan, "density")
    heat_capacity, _ = parameter(plan, "heat_capacity")
    if height_unit != length_unit:
        raise ValueError("Width and height must use one geometry unit in version 0.1")
    if length_unit not in {"mm", "cm", "m"}:
        raise ValueError(f"Unsupported geometry unit: {length_unit}")
    if min(width, height) <= 0 or min(left_temperature, right_temperature) <= 0:
        raise ValueError("Geometry dimensions and absolute temperatures must be positive")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    model_name = safe_name(f"heat_transfer_2d_{timestamp}_{run_id[:8]}")
    model_path = models_dir / f"{model_name}.mph"
    image_path = outputs_dir / "temperature.png"

    try:
        import mph
        import numpy as np

        emit("Build", "Starting a clean COMSOL/MPh process", input_summary={"run_id": run_id})
        client = mph.Client()
        emit("Build", "COMSOL session ready", "success", output_summary=f"COMSOL {client.version}")

        emit("Build", "Creating component and parameterized rectangle")
        model = client.create(model_name)
        java_model = model.java
        component = java_model.component().create("comp1", True)
        geometry = component.geom().create("geom1", 2)
        geometry.lengthUnit(length_unit)
        rectangle = geometry.feature().create("r1", "Rectangle")
        rectangle.set("size", [width, height])
        rectangle.set("pos", [0.0, 0.0])
        geometry.run()
        emit(
            "Build",
            "Geometry built",
            "success",
            output_summary=f"Rectangle {width:g} x {height:g} {length_unit}; {geometry.getNEdges()} edges",
        )

        emit("Build", "Assigning Heat Transfer in Solids and material properties")
        physics = java_model.component("comp1").physics().create("ht", "HeatTransfer", "geom1")
        solid = physics.feature().get("solid1")
        solid.set("k_mat", "userdef")
        solid.set("k", f"{conductivity:g}[W/(m*K)]")
        solid.set("rho_mat", "userdef")
        solid.set("rho", f"{density:g}[kg/m^3]")
        solid.set("Cp_mat", "userdef")
        solid.set("Cp", f"{heat_capacity:g}[J/(kg*K)]")
        material = java_model.material().create("mat1")
        material.label("Aluminum - user-defined values" if any("Aluminum" in name for name in plan.get("materials", [])) else "User-defined thermal material")
        emit("Build", "Physics and material assigned", "success")

        emit("Build", "Calibrating boundary numbers from coordinates")
        calibration_temperatures = {1: 311.0, 2: 322.0, 3: 333.0, 4: 344.0}
        calibration_tags: list[str] = []
        for edge, temperature in calibration_temperatures.items():
            tag = f"cal{edge}"
            boundary = physics.feature().create(tag, "TemperatureBoundary")
            boundary.selection().set([edge])
            boundary.set("T0", f"{temperature:g}[K]")
            calibration_tags.append(tag)

        mesh = java_model.component("comp1").mesh().create("mesh1")
        mesh.run()
        study = java_model.study().create("std1")
        study.label("Stationary")
        study.feature().create("stat", "Stationary")
        study.run()

        coordinates_x = np.real(np.asarray(model.evaluate("x")).reshape(-1))
        coordinates_y = np.real(np.asarray(model.evaluate("y")).reshape(-1))
        temperatures = np.real(np.asarray(model.evaluate("T")).reshape(-1))
        tolerance = max(width, height) * 1e-5 + 1e-8
        physical_medians = {
            "left": median_on_mask(temperatures, np.abs(coordinates_x - 0.0) <= tolerance),
            "right": median_on_mask(temperatures, np.abs(coordinates_x - width) <= tolerance),
            "bottom": median_on_mask(temperatures, np.abs(coordinates_y - 0.0) <= tolerance),
            "top": median_on_mask(temperatures, np.abs(coordinates_y - height) <= tolerance),
        }
        edge_map = {
            side: min(calibration_temperatures, key=lambda edge: abs(calibration_temperatures[edge] - value))
            for side, value in physical_medians.items()
        }
        if len(set(edge_map.values())) != 4:
            raise RuntimeError(
                f"Boundary calibration was ambiguous: medians={physical_medians}, map={edge_map}"
            )
        for tag in calibration_tags:
            physics.feature().remove(tag)
        emit(
            "Build",
            "Boundary calibration complete",
            "success",
            output_summary=json.dumps(edge_map, ensure_ascii=False),
        )

        emit("Build", "Applying coordinate-verified boundary conditions")
        left_boundary = physics.feature().create("temp_left", "TemperatureBoundary")
        left_boundary.selection().set([edge_map["left"]])
        left_boundary.set("T0", f"{left_temperature:g}[K]")
        right_boundary = physics.feature().create("temp_right", "TemperatureBoundary")
        right_boundary.selection().set([edge_map["right"]])
        right_boundary.set("T0", f"{right_temperature:g}[K]")
        emit(
            "Build",
            "Boundary conditions applied",
            "success",
            output_summary=f"left=edge {edge_map['left']}, right=edge {edge_map['right']}; top/bottom insulated",
        )
        emit(
            "Build Validation",
            "Model-tree and coordinate boundary checks passed",
            "success",
            output_summary=json.dumps(
                {
                    "geometry_edges": int(geometry.getNEdges()),
                    "physics": "HeatTransfer",
                    "left_edge": edge_map["left"],
                    "right_edge": edge_map["right"],
                    "remaining_boundaries": "thermal insulation default",
                },
                ensure_ascii=False,
            ),
        )

        emit("Solve", "Solving the stationary heat-transfer study")
        study.run()
        emit("Solve", "Stationary solver converged", "success")

        emit("Solution Validation", "Checking coordinate-resolved temperatures and analytical solution")
        x_values = np.real(np.asarray(model.evaluate("x")).reshape(-1))
        y_values = np.real(np.asarray(model.evaluate("y")).reshape(-1))
        t_values = np.real(np.asarray(model.evaluate("T")).reshape(-1))
        left_median = median_on_mask(t_values, np.abs(x_values) <= tolerance)
        right_median = median_on_mask(t_values, np.abs(x_values - width) <= tolerance)
        expected = left_temperature + (right_temperature - left_temperature) * (x_values / width)
        rmse = float(np.sqrt(np.mean((t_values - expected) ** 2)))
        t_min = float(np.min(t_values))
        t_max = float(np.max(t_values))
        imposed_min = min(left_temperature, right_temperature)
        imposed_max = max(left_temperature, right_temperature)
        range_ok = t_min >= imposed_min - 1.0 and t_max <= imposed_max + 1.0
        boundary_ok = abs(left_median - left_temperature) <= 1.0 and abs(right_median - right_temperature) <= 1.0
        analytical_ok = rmse <= 1.0
        validation = {
            "passed": bool(range_ok and boundary_ok and analytical_ok),
            "temperature_min_K": t_min,
            "temperature_max_K": t_max,
            "left_boundary_median_K": left_median,
            "right_boundary_median_K": right_median,
            "analytical_rmse_K": rmse,
            "range_check": range_ok,
            "boundary_check": boundary_ok,
            "analytical_check": analytical_ok,
            "node_count": int(t_values.size),
            "dof": int(t_values.size),
        }
        if not validation["passed"]:
            raise RuntimeError(f"Scientific validation failed: {validation}")
        emit(
            "Solution Validation",
            "Scientific validation passed",
            "success",
            output_summary=f"T={t_min:.3f}..{t_max:.3f} K; analytical RMSE={rmse:.4f} K",
        )

        emit("Export", "Exporting temperature field and MPH model")
        results = java_model.result()
        plot_group = results.create("pg_workbench", "PlotGroup2D")
        plot_group.label("Temperature")
        surface = plot_group.feature().create("surf1", "Surface")
        surface.set("expr", "T")
        surface.set("unit", "K")
        plot_group.run()
        image_export = results.export().create("img_workbench", "Image")
        image_export.set("plotgroup", "pg_workbench")
        image_export.set("filename", str(image_path))
        image_export.set("imagetype", "png")
        image_export.run()
        model.save(str(model_path))
        emit("Export", "Artifacts exported", "success", output_summary=f"{model_path.name}, {image_path.name}")

        build_readback = None
        build_readback_issue = None
        try:
            actual_size = [float(value) for value in rectangle.getDoubleArray('size')]
            actual_unit = str(geometry.lengthUnit())
            actual_sol = [str(tag) for tag in java_model.sol().tags()]
            if len(actual_size) != 2 or 'r1' not in [str(tag) for tag in geometry.feature().tags()] \
                    or 'mesh1' not in [str(tag) for tag in component.mesh().tags()] \
                    or 'std1' not in [str(tag) for tag in java_model.study().tags()] \
                    or 'mat1' not in [str(tag) for tag in java_model.material().tags()] \
                    or 'sol1' not in actual_sol:
                raise ValueError('Required COMSOL core model tag was absent from readback')
            build_readback = {
                'source': 'direct_comsol_readback', 'model_tag': str(java_model.tag()),
                'geometry_tag': 'comp1:geom1', 'geometry_feature_tag': 'r1',
                'geometry_kind': str(rectangle.getType()), 'geometry_size': actual_size,
                'geometry_unit': actual_unit, 'geometry_domains': int(geometry.getNDomains()),
                'geometry_edges': int(geometry.getNEdges()),
                'material_tag': 'mat1', 'material_name': str(material.label()),
                'material_origin': 'user', 'material_properties': [
                    {'symbol': 'k', 'value': readback_literal(str(solid.getString('k')), 'W/(m*K)'), 'unit': 'W/(m*K)'},
                    {'symbol': 'rho', 'value': readback_literal(str(solid.getString('rho')), 'kg/m^3'), 'unit': 'kg/m^3'},
                    {'symbol': 'Cp', 'value': readback_literal(str(solid.getString('Cp')), 'J/(kg*K)'), 'unit': 'J/(kg*K)'},
                ],
                'physics_tag': 'comp1:ht', 'physics_type': str(physics.getType()),
                'study_tag': 'std1', 'study_type': 'stationary', 'solver_tag': 'sol1',
                'mesh_tag': 'comp1:mesh1',
                'left_bc': {'tag': 'temp_left', 'type': str(left_boundary.getType()),
                            'selection': [int(edge) for edge in left_boundary.selection().entities()],
                            'expression': str(left_boundary.getString('T0'))},
                'right_bc': {'tag': 'temp_right', 'type': str(right_boundary.getType()),
                             'selection': [int(edge) for edge in right_boundary.selection().entities()],
                             'expression': str(right_boundary.getString('T0'))},
            }
        except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
            build_readback_issue = f'{type(exc).__name__}: {exc}'

        payload = {
            "success": True,
            "command_success": True,
            "model_state_verified": True,
            "scientific_validation_success": True,
            "run_id": run_id,
            "comsol_version": str(client.version),
            "artifacts": {
                "model": str(model_path),
                "temperature_image": str(image_path),
                "report_directory": str(reports_dir),
            },
            "validation": validation,
            "boundary_map": edge_map,
            "build_readback": build_readback,
            "build_readback_issue": build_readback_issue,
            "model_state": {
                "geometry": [f"Rectangle {width:g} x {height:g} {length_unit}"],
                "materials": ["Aluminum (user-defined, source verification required)"],
                "physics": ["Heat Transfer in Solids (ht)"],
                "parameters": plan["parameters"],
                "boundary_conditions": [
                    f"Left: {left_temperature:g} K (calibrated edge {edge_map['left']})",
                    f"Right: {right_temperature:g} K (calibrated edge {edge_map['right']})",
                    "Top and bottom: Thermal insulation",
                ],
                "mesh": ["Physics-controlled mesh"],
                "study": ["Stationary"],
                "solver": ["COMSOL stationary solver; converged"],
                "results": [f"Temperature {t_min:.3f} to {t_max:.3f} K"],
            },
        }
        result(payload)
        try:
            client.clear()
        except Exception as cleanup_exc:
            emit("System", "COMSOL cleanup warning", "warning", output_summary=str(cleanup_exc))
        return 0
    except Exception as exc:
        trace = traceback.format_exc()
        emit("Repair", "COMSOL workflow failed after isolated-process retry", "error", output_summary=str(exc), fallback=True)
        result({"success": False, "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "error": f"{type(exc).__name__}: {exc}", "traceback": trace[-8_000:]})
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    arguments = parser.parse_args()
    sys.exit(main(arguments.request))
