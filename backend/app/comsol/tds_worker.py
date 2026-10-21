"""Isolated real COMSOL 6.3 worker for stationary 2D pure diffusion.

The worker owns no planning policy.  It consumes a validated ScientificPlan,
performs coordinate-derived boundary selection, and treats every COMSOL write
as unverified until the real model object has been read back and compared.
"""

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
    event_type = str(
        extra.pop(
            "event_type",
            "model_change"
            if "build validation" in normalized
            else "progress"
            if normalized in {"solve", "mesh", "export"}
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


def one_edge(
    component: Any,
    tag: str,
    *,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> list[int]:
    selection = component.selection().create(tag, "Box")
    selection.geom("geom1", 1)
    selection.set("entitydim", "1")
    selection.set("condition", "inside")
    selection.set("xmin", xmin)
    selection.set("xmax", xmax)
    selection.set("ymin", ymin)
    selection.set("ymax", ymax)
    return [int(item) for item in selection.entities()]


def readback(feature: Any, name: str) -> str:
    return str(feature.getString(name))


def mesh_statistics(mesh: Any, node_count: int) -> dict[str, Any]:
    report: dict[str, Any] = {
        "settings": "COMSOL physics-controlled default 2D mesh",
        "node_count": node_count,
        "element_count": None,
        "readback_method": "solution-node count",
    }
    try:
        for method_name in ("getNumElem", "getNumElements"):
            try:
                raw = getattr(mesh, method_name)()
                values = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
                count = sum(int(value) for value in values)
                report.update({"element_count": count, "readback_method": f"mesh.{method_name}()"})
                break
            except Exception:
                continue
        for method_name in ("getNumVertex", "getNumVertices"):
            try:
                report["mesh_vertex_count"] = int(getattr(mesh, method_name)())
                break
            except Exception:
                continue
    except Exception as exc:
        report["stats_warning"] = f"{type(exc).__name__}: {exc}"
    return report


def main(request_path: Path) -> int:
    total_started = time.perf_counter()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan = request["plan"]
    run_id = str(request["run_id"])
    fault_injection = str(request.get("fault_injection", ""))
    workspace = Path(request["workspace"]).resolve()
    models_dir = workspace / "models"
    outputs_dir = workspace / "outputs" / run_id
    reports_dir = workspace / "reports" / run_id
    for directory in (models_dir, outputs_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    length, length_unit = parameter(plan, "length")
    height, height_unit = parameter(plan, "height")
    diffusion, diffusion_unit = parameter(plan, "diffusion_coefficient")
    c_left, concentration_unit = parameter(plan, "left_concentration")
    c_right, right_concentration_unit = parameter(plan, "right_concentration")
    if (length_unit, height_unit) != ("m", "m"):
        raise ValueError("TDS executable geometry must be normalized to metres")
    if diffusion_unit not in {"m^2/s", "m²/s"}:
        raise ValueError("Diffusion coefficient must use m^2/s")
    if concentration_unit not in {"mol/m^3", "mol/m³"} or right_concentration_unit not in {"mol/m^3", "mol/m³"}:
        raise ValueError("Boundary concentrations must use mol/m^3")
    if length <= 0 or height <= 0 or diffusion <= 0:
        raise ValueError("Length, height and diffusion coefficient must be positive")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    model_name = safe_name(f"tds_stationary_diffusion_{timestamp}_{run_id[:8]}")
    model_path = models_dir / f"{model_name}.mph"
    image_path = outputs_dir / "concentration.png"
    csv_path = outputs_dir / "concentration_profile.csv"
    validation_path = reports_dir / "validation.json"
    performance: dict[str, float] = {}
    client = None

    try:
        import mph
        import numpy as np

        emit("Build", "Starting a clean real COMSOL/MPh process", input_summary={"run_id": run_id})
        client = timed(performance, "comsol_session_ms", lambda: mph.Client())
        emit("Build", "COMSOL session ready", "success", output_summary=f"COMSOL {client.version}")
        model = client.create(model_name)
        java_model = model.java

        def configure_parameters() -> None:
            params = java_model.param()
            params.set("L", f"{length:.16g}[m]")
            params.set("H", f"{height:.16g}[m]")
            params.set("D", f"{diffusion:.16g}[m^2/s]")
            params.set("c_left", f"{c_left:.16g}[mol/m^3]")
            params.set("c_right", f"{c_right:.16g}[mol/m^3]")

        timed(performance, "parameter_write_ms", configure_parameters)
        parameter_readback = {
            name: str(java_model.param().get(name)) for name in ("L", "H", "D", "c_left", "c_right")
        }

        emit("Build", "Creating the parameterized 2D rectangle")

        def build_geometry() -> tuple[Any, Any, Any]:
            component_value = java_model.component().create("comp1", True)
            geometry_value = component_value.geom().create("geom1", 2)
            geometry_value.lengthUnit("m")
            rectangle_value = geometry_value.feature().create("r1", "Rectangle")
            rectangle_value.set("size", ["L", "H"])
            rectangle_value.set("pos", ["0", "0"])
            geometry_value.run()
            return component_value, geometry_value, rectangle_value

        component, geometry, rectangle = timed(performance, "comsol_build_geometry_ms", build_geometry)
        tolerance = min(length, height) * 1e-5
        edge_map = timed(
            performance,
            "boundary_selection_ms",
            lambda: {
                "left": one_edge(component, "sel_left", xmin=-tolerance, xmax=tolerance, ymin=-tolerance, ymax=height + tolerance),
                "right": one_edge(component, "sel_right", xmin=length - tolerance, xmax=length + tolerance, ymin=-tolerance, ymax=height + tolerance),
                "bottom": one_edge(component, "sel_bottom", xmin=-tolerance, xmax=length + tolerance, ymin=-tolerance, ymax=tolerance),
                "top": one_edge(component, "sel_top", xmin=-tolerance, xmax=length + tolerance, ymin=height - tolerance, ymax=height + tolerance),
            },
        )
        if any(len(edges) != 1 for edges in edge_map.values()) or len({edges[0] for edges in edge_map.values()}) != 4:
            raise RuntimeError(f"BUILD_VALIDATION FAILED: coordinate-derived boundary map is invalid: {edge_map}")
        emit("Build", "Geometry and coordinate selections created", "success", output_summary=json.dumps(edge_map))

        emit("Build", "Creating Transport of Diluted Species")
        physics = timed(performance, "tds_interface_ms", lambda: component.physics().create("tds", "DilutedSpecies", "geom1"))
        emit("Build", "TDS created", "success", output_summary=f"tag={physics.tag()}, type={physics.getType()}")

        emit("Build", "Configuring pure diffusion and zero convection")
        transport = physics.feature("cdm1")

        def configure_diffusion() -> None:
            transport.set("D_c_mat", "userdef")
            transport.set("D_c", "D")
            transport.set("u_src", "userdef")
            transport.set("u", ["0[m/s]", "0[m/s]", "0[m/s]"])

        timed(performance, "diffusion_configuration_ms", configure_diffusion)
        emit("Build", "D configured", "success", output_summary="D_c=D; u=(0,0,0) m/s")

        emit("Build", "Applying left/right concentration boundaries")
        conc_left = physics.feature().create("conc_left", "Concentration")
        conc_left.selection().set(edge_map["left"])
        conc_left.set("species", "1")
        conc_left.set("c0", "c_left")
        conc_right = physics.feature().create("conc_right", "Concentration")
        conc_right.selection().set(edge_map["right"])
        conc_right.set("species", "1")
        conc_right.set("c0", "c_right")
        if fault_injection == "empty_left_selection":
            conc_left.selection().set([])
        no_flux = physics.feature("nflx1")
        emit("Build", "BC configured", "success", output_summary="left/right Concentration; top/bottom default No Flux")

        emit("Mesh", "Building stable physics-controlled mesh", progress_source="mesh.run")
        mesh = component.mesh().create("mesh1")
        timed(performance, "mesh_ms", mesh.run)
        emit("Mesh", "Mesh built", "success", progress_source="mesh.run")

        study = java_model.study().create("std1")
        study.label("Stationary pure diffusion")
        study.feature().create("stat", "Stationary")
        integration_left = component.cpl().create("int_left", "Integration")
        integration_left.selection().geom("geom1", 1)
        integration_left.selection().set(edge_map["left"])
        integration_right = component.cpl().create("int_right", "Integration")
        integration_right.selection().geom("geom1", 1)
        integration_right.selection().set(edge_map["right"])

        build_validation_started = time.perf_counter()
        build_readback = {
            "component_tag": str(component.tag()),
            "geometry_tag": str(geometry.tag()),
            "geometry_edges": int(geometry.getNEdges()),
            "rectangle_size": [str(value) for value in rectangle.getStringArray("size")],
            "physics_tag": str(physics.tag()),
            "physics_type": str(physics.getType()),
            "physics_domain_selection": [int(value) for value in physics.selection().entities()],
            "transport_type": str(transport.getType()),
            "diffusion_material_mode": readback(transport, "D_c_mat"),
            "diffusion_expression": readback(transport, "D_c"),
            "velocity": [str(value) for value in transport.getStringArray("u")],
            "left_bc": {
                "tag": str(conc_left.tag()),
                "type": str(conc_left.getType()),
                "selection": [int(value) for value in conc_left.selection().entities()],
                "species": readback(conc_left, "species"),
                "expression": readback(conc_left, "c0"),
            },
            "right_bc": {
                "tag": str(conc_right.tag()),
                "type": str(conc_right.getType()),
                "selection": [int(value) for value in conc_right.selection().entities()],
                "species": readback(conc_right, "species"),
                "expression": readback(conc_right, "c0"),
            },
            "no_flux": {
                "tag": str(no_flux.tag()),
                "type": str(no_flux.getType()),
                "selection": [int(value) for value in no_flux.selection().entities()],
                "include_convection": readback(no_flux, "IncludeConvection"),
            },
            "edge_map": edge_map,
            "study_tags": [str(value) for value in java_model.study().tags()],
            "study_feature_tags": [str(value) for value in study.feature().tags()],
            "parameter_expressions": parameter_readback,
        }
        expected_no_flux = sorted(edge_map["top"] + edge_map["bottom"])
        build_checks = {
            "interface_exists": build_readback["physics_tag"] == "tds",
            "interface_type": build_readback["physics_type"] == "DilutedSpecies",
            "domain_selection": bool(build_readback["physics_domain_selection"]),
            "diffusion_feature_type": build_readback["transport_type"] == "Fluid",
            "diffusion_mode": build_readback["diffusion_material_mode"] == "userdef",
            "diffusion_expression": build_readback["diffusion_expression"] == "D",
            "zero_velocity": all(value.replace(" ", "") in {"0", "0[m/s]"} for value in build_readback["velocity"]),
            "left_bc_type": build_readback["left_bc"]["type"] == "Concentration",
            "left_bc_selection": build_readback["left_bc"]["selection"] == edge_map["left"],
            "left_bc_species": build_readback["left_bc"]["species"] == "1",
            "left_bc_expression": build_readback["left_bc"]["expression"] == "c_left",
            "right_bc_type": build_readback["right_bc"]["type"] == "Concentration",
            "right_bc_selection": build_readback["right_bc"]["selection"] == edge_map["right"],
            "right_bc_species": build_readback["right_bc"]["species"] == "1",
            "right_bc_expression": build_readback["right_bc"]["expression"] == "c_right",
            "no_flux_type": build_readback["no_flux"]["type"] == "NoFlux",
            "no_flux_selection": sorted(build_readback["no_flux"]["selection"]) == expected_no_flux,
            "stationary_study": build_readback["study_feature_tags"] == ["stat"],
            "parameter_readback": all(parameter_readback.values()),
        }
        build_readback["checks"] = build_checks
        if not all(build_checks.values()):
            raise RuntimeError(f"BUILD_VALIDATION FAILED: real COMSOL readback mismatch: {build_checks}")
        performance["build_validation_ms"] = round((time.perf_counter() - build_validation_started) * 1000, 3)
        performance["comsol_build_ms"] = round(
            sum(
                performance.get(name, 0.0)
                for name in (
                    "parameter_write_ms",
                    "comsol_build_geometry_ms",
                    "boundary_selection_ms",
                    "tds_interface_ms",
                    "diffusion_configuration_ms",
                )
            ),
            3,
        )
        emit(
            "Build Validation",
            "WRITE → real readback → compare → verify passed",
            "success",
            output_summary=json.dumps(build_checks, ensure_ascii=False),
        )

        emit("Solve", "Study started", progress_source="study.run")
        timed(performance, "solve_ms", study.run)
        emit("Solve", "Study finished", "success", progress_source="study.run", output_summary="COMSOL stationary solver returned")

        emit("Solution Validation", "Extracting concentration and COMSOL flux fields")
        validation_started = time.perf_counter()
        x_values = np.real(np.asarray(model.evaluate("x"))).reshape(-1)
        y_values = np.real(np.asarray(model.evaluate("y"))).reshape(-1)
        c_values = np.real(np.asarray(model.evaluate("c"))).reshape(-1)
        flux_values = np.real(np.asarray(model.evaluate("tds.dflux_cx"))).reshape(-1)
        if not (x_values.size == y_values.size == c_values.size == flux_values.size) or not np.all(np.isfinite(c_values)):
            raise RuntimeError("SOLUTION_VALIDATION FAILED: extracted fields are empty, nonfinite or size-mismatched")

        boundary_tolerance = max(abs(c_left - c_right) * 1e-6, 1e-7)
        solution_tolerance = max(abs(c_left - c_right) * 1e-5, 1e-6)
        coordinate_tolerance = max(min(length, height) * 1e-5, 1e-12)
        left_mask = np.abs(x_values) <= coordinate_tolerance
        right_mask = np.abs(x_values - length) <= coordinate_tolerance
        left_median = float(np.median(c_values[left_mask]))
        right_median = float(np.median(c_values[right_mask]))
        exact = c_left + (c_right - c_left) * x_values / length
        errors = c_values - exact
        rmse = float(np.sqrt(np.mean(errors**2)))
        mae = float(np.mean(np.abs(errors)))
        max_abs_error = float(np.max(np.abs(errors)))
        low, high = sorted((c_left, c_right))
        c_min, c_max = float(np.min(c_values)), float(np.max(c_values))

        bins = np.round(x_values / coordinate_tolerance).astype(np.int64)
        profile: list[tuple[float, float]] = []
        maximum_y_spread = 0.0
        for key in np.unique(bins):
            mask = bins == key
            profile.append((float(np.mean(x_values[mask])), float(np.mean(c_values[mask]))))
            maximum_y_spread = max(maximum_y_spread, float(np.max(c_values[mask]) - np.min(c_values[mask])))
        profile.sort()
        monotonic_violations = sum(
            1 for (_, first), (_, second) in zip(profile, profile[1:]) if second - first > solution_tolerance
        )

        expected_flux = -diffusion * (c_right - c_left) / length
        flux_mean = float(np.mean(flux_values))
        flux_relative_error = abs(flux_mean - expected_flux) / max(abs(expected_flux), 1e-30)
        left_outward = float(np.real(np.asarray(model.evaluate("int_left(tds.ndflux_c)")).reshape(-1)[0]))
        right_outward = float(np.real(np.asarray(model.evaluate("int_right(tds.ndflux_c)")).reshape(-1)[0]))
        mass_balance_absolute = abs(left_outward + right_outward)
        mass_balance_relative = mass_balance_absolute / max(abs(left_outward), abs(right_outward), 1e-30)
        mesh_report = mesh_statistics(mesh, int(c_values.size))

        solution_checks = {
            "solver_completed": True,
            "nonzero_field": bool(c_max > 0),
            "left_boundary_concentration": abs(left_median - c_left) <= boundary_tolerance,
            "right_boundary_concentration": abs(right_median - c_right) <= boundary_tolerance,
            "physical_range": c_min >= low - solution_tolerance and c_max <= high + solution_tolerance,
            "analytical_rmse": rmse <= solution_tolerance,
            "analytical_mae": mae <= solution_tolerance,
            "analytical_max_abs_error": max_abs_error <= solution_tolerance * 10,
            "monotonicity": monotonic_violations == 0,
            "y_uniformity": maximum_y_spread <= solution_tolerance,
            "flux_sign": math.copysign(1.0, flux_mean) == math.copysign(1.0, expected_flux),
            "flux_relative_error": flux_relative_error <= 1e-4,
            "mass_balance": mass_balance_relative <= 1e-6,
        }
        validation = {
            "passed": all(solution_checks.values()),
            "checks": solution_checks,
            "node_count": int(c_values.size),
            "dof": int(c_values.size),
            "element_count": mesh_report.get("element_count"),
            "mesh": mesh_report,
            "concentration_min_mol_per_m3": c_min,
            "concentration_max_mol_per_m3": c_max,
            "left_boundary_median_mol_per_m3": left_median,
            "right_boundary_median_mol_per_m3": right_median,
            "analytical_rmse_mol_per_m3": rmse,
            "analytical_mae_mol_per_m3": mae,
            "analytical_max_abs_error_mol_per_m3": max_abs_error,
            "monotonic_violations": monotonic_violations,
            "maximum_y_spread_mol_per_m3": maximum_y_spread,
            "flux_expression": "tds.dflux_cx",
            "flux_expected_mol_per_m2_s": expected_flux,
            "flux_mean_mol_per_m2_s": flux_mean,
            "flux_relative_error": flux_relative_error,
            "normal_flux_expression": "tds.ndflux_c",
            "left_outward_flux_integral_mol_per_m_s": left_outward,
            "right_outward_flux_integral_mol_per_m_s": right_outward,
            "mass_balance_absolute_error_mol_per_m_s": mass_balance_absolute,
            "mass_balance_relative_error": mass_balance_relative,
        }
        performance["solution_validation_ms"] = round((time.perf_counter() - validation_started) * 1000, 3)
        if not validation["passed"]:
            raise RuntimeError(f"SOLUTION_VALIDATION FAILED: {solution_checks}")
        emit(
            "Solution Validation",
            "Scientific validation passed",
            "success",
            output_summary=f"RMSE={rmse:.6g} mol/m^3; Jx={flux_mean:.9g} mol/(m^2*s); mass={mass_balance_relative:.3g}",
        )

        emit("Export", "Exporting MPH, concentration image, CSV and validation JSON", progress_source="COMSOL result export")
        export_started = time.perf_counter()
        results = java_model.result()
        plot_group = results.create("pg_concentration", "PlotGroup2D")
        plot_group.label("Concentration")
        surface = plot_group.feature().create("surf1", "Surface")
        surface.set("expr", "c")
        surface.set("unit", "mol/m^3")
        plot_group.run()
        image_export = results.export().create("img_concentration", "Image")
        image_export.set("plotgroup", "pg_concentration")
        image_export.set("filename", str(image_path))
        image_export.set("imagetype", "png")
        image_export.run()
        model.save(str(model_path))
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["x_m", "y_m", "concentration_mol_per_m3", "analytical_mol_per_m3", "error_mol_per_m3", "diffusive_flux_x_mol_per_m2_s"])
            for row in sorted(zip(x_values, y_values, c_values, exact, errors, flux_values), key=lambda values: (values[0], values[1])):
                writer.writerow([f"{float(value):.17g}" for value in row])
        performance["export_ms"] = round((time.perf_counter() - export_started) * 1000, 3)
        performance["total_worker_ms"] = round((time.perf_counter() - total_started) * 1000, 3)

        payload = {
            "success": True,
            "command_success": True,
            "model_state_verified": True,
            "scientific_validation_success": True,
            "run_id": run_id,
            "comsol_version": str(client.version),
            "artifacts": {
                "model": str(model_path),
                "concentration_image": str(image_path),
                "concentration_profile": str(csv_path),
                "validation": str(validation_path),
                "report_directory": str(reports_dir),
            },
            "validation": validation,
            "build_readback": build_readback,
            "boundary_map": edge_map,
            "performance": performance,
            "model_state": {
                "geometry": [f"2D rectangle {length:g} m × {height:g} m; coordinate-derived edges {edge_map}"],
                "materials": ["No material database values; benchmark-defined D"],
                "physics": ["Transport of Diluted Species (tds)", "Pure diffusion; zero convection; no migration; no reactions"],
                "parameters": plan["parameters"],
                "boundary_conditions": [
                    f"Left Concentration: {c_left:g} mol/m^3 (edge {edge_map['left'][0]})",
                    f"Right Concentration: {c_right:g} mol/m^3 (edge {edge_map['right'][0]})",
                    f"Top/bottom No Flux: edges {edge_map['top'][0]}/{edge_map['bottom'][0]}",
                ],
                "selections": [f"{side}: {edges}" for side, edges in edge_map.items()],
                "mesh": [f"{mesh_report['settings']}; nodes={mesh_report['node_count']}; elements={mesh_report.get('element_count')}"] ,
                "study": ["Stationary study (std1/stat)"],
                "solver": ["COMSOL stationary solver; converged"],
                "results": [f"Concentration {c_min:.6g} to {c_max:.6g} mol/m^3", f"Jx={flux_mean:.9g} mol/(m^2*s)"],
            },
        }
        validation_path.write_text(json.dumps({"validation": validation, "build_readback": build_readback, "performance": performance}, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("Export", "Artifacts exported", "success", output_summary=", ".join(path.name for path in (model_path, image_path, csv_path, validation_path)))
        result(payload)
        try:
            client.clear()
        except Exception as cleanup_exc:
            emit("System", "COMSOL cleanup warning", "warning", output_summary=str(cleanup_exc))
        return 0
    except Exception as exc:
        performance["total_worker_ms"] = round((time.perf_counter() - total_started) * 1000, 3)
        trace = traceback.format_exc()
        phase = "Build Validation" if "BUILD_VALIDATION FAILED" in str(exc) else "Solution Validation" if "SOLUTION_VALIDATION FAILED" in str(exc) else "Repair"
        emit(phase, "TDS workflow failed safely", "error", output_summary=str(exc), fallback=True)
        result(
            {
                "success": False,
                "command_success": False,
                "model_state_verified": False,
                "scientific_validation_success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "performance": performance,
                "traceback": trace[-12000:],
            }
        )
        if client is not None:
            try:
                client.clear()
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    arguments = parser.parse_args()
    sys.exit(main(arguments.request))
