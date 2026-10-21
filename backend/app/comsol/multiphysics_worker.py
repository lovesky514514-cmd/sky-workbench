"""Real one-way Heat -> temperature-dependent TDS coupling benchmark."""

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
    event_type = str(extra.pop("event_type", "model_change" if "validation" in normalized else "progress" if normalized in {"solve", "mesh", "export"} else "command"))
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


def one_edge(component: Any, tag: str, *, xmin: float, xmax: float, ymin: float, ymax: float) -> list[int]:
    selection = component.selection().create(tag, "Box")
    selection.geom("geom1", 1)
    selection.set("entitydim", "1")
    selection.set("condition", "inside")
    for key, value in {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}.items():
        selection.set(key, value)
    return [int(value) for value in selection.entities()]


def string(feature: Any, name: str) -> str:
    return str(feature.getString(name))


def mesh_elements(mesh: Any) -> int | None:
    for method in ("getNumElem", "getNumElements"):
        try:
            raw = getattr(mesh, method)()
            values = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
            return sum(int(value) for value in values)
        except Exception:
            continue
    return None


def main(request_path: Path) -> int:
    started = time.perf_counter()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan = request["plan"]
    run_id = str(request["run_id"])
    workspace = Path(request["workspace"]).resolve()
    models = workspace / "models"
    outputs = workspace / "outputs" / run_id
    reports = workspace / "reports" / run_id
    for directory in (models, outputs, reports):
        directory.mkdir(parents=True, exist_ok=True)

    length, length_unit = parameter(plan, "length")
    height, height_unit = parameter(plan, "height")
    diffusion, diffusion_unit = parameter(plan, "diffusion_coefficient")
    reference_temperature, reference_unit = parameter(plan, "reference_temperature")
    left_temperature, left_temperature_unit = parameter(plan, "left_temperature")
    right_temperature, right_temperature_unit = parameter(plan, "right_temperature")
    left_concentration, left_concentration_unit = parameter(plan, "left_concentration")
    right_concentration, right_concentration_unit = parameter(plan, "right_concentration")
    if {length_unit, height_unit} != {"m"} or diffusion_unit not in {"m^2/s", "m²/s"}:
        raise ValueError("Coupled benchmark requires metres and m^2/s")
    if {reference_unit, left_temperature_unit, right_temperature_unit} != {"K"}:
        raise ValueError("Coupled benchmark temperatures must use kelvin")
    if not {left_concentration_unit, right_concentration_unit}.issubset({"mol/m^3", "mol/m³"}):
        raise ValueError("Coupled benchmark concentrations must use mol/m^3")
    if min(length, height, diffusion, reference_temperature, left_temperature, right_temperature) <= 0:
        raise ValueError("Coupled benchmark parameters must be positive")
    if math.isclose(left_temperature, right_temperature):
        raise ValueError("Temperature-dependent coupling requires a nonzero temperature gradient")

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    model_path = models / f"{safe_name(f'heat_tds_coupling_{stamp}_{run_id[:8]}')}.mph"
    csv_path = outputs / "coupled_concentration_profile.csv"
    concentration_image = outputs / "coupled_concentration.png"
    temperature_image = outputs / "coupled_temperature.png"
    validation_path = reports / "validation.json"
    performance: dict[str, float] = {}
    client = None
    try:
        import mph
        import numpy as np

        emit("Build", "Starting clean COMSOL for Heat → TDS coupling")
        client = timed(performance, "comsol_session_ms", mph.Client)
        model = client.create(model_path.stem)
        java = model.java
        params = java.param()
        for name, expression in {
            "L": f"{length:.16g}[m]", "H": f"{height:.16g}[m]", "D0": f"{diffusion:.16g}[m^2/s]",
            "T0": f"{reference_temperature:.16g}[K]", "T_left": f"{left_temperature:.16g}[K]",
            "T_right": f"{right_temperature:.16g}[K]", "c_left": f"{left_concentration:.16g}[mol/m^3]",
            "c_right": f"{right_concentration:.16g}[mol/m^3]",
        }.items():
            params.set(name, expression)
        parameter_readback = {name: str(params.get(name)) for name in ("L", "H", "D0", "T0", "T_left", "T_right", "c_left", "c_right")}

        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 2)
        geometry.lengthUnit("m")
        rectangle = geometry.feature().create("r1", "Rectangle")
        rectangle.set("size", ["L", "H"])
        rectangle.set("pos", ["0", "0"])
        timed(performance, "geometry_ms", geometry.run)
        tolerance = min(length, height) * 1e-5
        edge_map = {
            "left": one_edge(component, "sel_left", xmin=-tolerance, xmax=tolerance, ymin=-tolerance, ymax=height + tolerance),
            "right": one_edge(component, "sel_right", xmin=length - tolerance, xmax=length + tolerance, ymin=-tolerance, ymax=height + tolerance),
            "bottom": one_edge(component, "sel_bottom", xmin=-tolerance, xmax=length + tolerance, ymin=-tolerance, ymax=tolerance),
            "top": one_edge(component, "sel_top", xmin=-tolerance, xmax=length + tolerance, ymin=height - tolerance, ymax=height + tolerance),
        }
        if any(len(value) != 1 for value in edge_map.values()) or len({value[0] for value in edge_map.values()}) != 4:
            raise RuntimeError(f"BUILD_VALIDATION FAILED: invalid edge map {edge_map}")

        emit("Build", "Creating Heat Transfer and TDS interfaces")
        heat = component.physics().create("ht", "HeatTransfer", "geom1")
        solid = heat.feature("solid1")
        solid.set("k_mat", "userdef"); solid.set("k", "1[W/(m*K)]")
        solid.set("rho_mat", "userdef"); solid.set("rho", "1000[kg/m^3]")
        solid.set("Cp_mat", "userdef"); solid.set("Cp", "1000[J/(kg*K)]")
        heat_left = heat.feature().create("temp_left", "TemperatureBoundary")
        heat_left.selection().set(edge_map["left"]); heat_left.set("T0", "T_left")
        heat_right = heat.feature().create("temp_right", "TemperatureBoundary")
        heat_right.selection().set(edge_map["right"]); heat_right.set("T0", "T_right")

        tds = component.physics().create("tds", "DilutedSpecies", "geom1")
        transport = tds.feature("cdm1")
        transport.set("D_c_mat", "userdef"); transport.set("D_c", "D0")
        transport.set("u_src", "userdef"); transport.set("u", ["0[m/s]", "0[m/s]", "0[m/s]"])
        concentration_left = tds.feature().create("conc_left", "Concentration")
        concentration_left.selection().set(edge_map["left"]); concentration_left.set("species", "1"); concentration_left.set("c0", "c_left")
        concentration_right = tds.feature().create("conc_right", "Concentration")
        concentration_right.selection().set(edge_map["right"]); concentration_right.set("species", "1"); concentration_right.set("c0", "c_right")
        mesh = component.mesh().create("mesh1")
        timed(performance, "mesh_ms", mesh.run)
        study = java.study().create("std1")
        study.label("Stationary Heat to TDS coupling")
        study.feature().create("stat", "Stationary")
        int_left = component.cpl().create("int_left", "Integration"); int_left.selection().geom("geom1", 1); int_left.selection().set(edge_map["left"])
        int_right = component.cpl().create("int_right", "Integration"); int_right.selection().geom("geom1", 1); int_right.selection().set(edge_map["right"])

        emit("Solve", "Solving coupling OFF reference with constant D")
        timed(performance, "solve_off_ms", study.run)
        x_off = np.real(np.asarray(model.evaluate("x"))).reshape(-1)
        c_off = np.real(np.asarray(model.evaluate("c"))).reshape(-1)
        flux_off = np.real(np.asarray(model.evaluate("tds.dflux_cx"))).reshape(-1)
        temperature_off = np.real(np.asarray(model.evaluate("T"))).reshape(-1)

        emit("Build", "Enabling one-way Heat temperature dependency in TDS")
        coupling_expression = "D0*T/T0"
        transport.set("D_c", coupling_expression)
        diffusion_readback = string(transport, "D_c")
        temperature_variable_probe = np.real(np.asarray(model.evaluate("T"))).reshape(-1)
        build_readback = {
            "heat_tag": str(heat.tag()), "heat_type": str(heat.getType()),
            "tds_tag": str(tds.tag()), "tds_type": str(tds.getType()),
            "heat_temperature_variable": "T", "temperature_probe_finite": bool(np.all(np.isfinite(temperature_variable_probe))),
            "diffusion_expression": diffusion_readback,
            "coupling": {"source": "ht/T", "target": "tds/cdm1/D_c", "expression": coupling_expression, "direction": "Heat -> TDS"},
            "physics_domains": {"heat": [int(value) for value in heat.selection().entities()], "tds": [int(value) for value in tds.selection().entities()]},
            "study_features": [str(value) for value in study.feature().tags()],
            "edge_map": edge_map, "parameters": parameter_readback,
        }
        build_checks = {
            "heat_interface": build_readback["heat_type"] == "HeatTransfer",
            "tds_interface": build_readback["tds_type"] == "DilutedSpecies",
            "shared_domain": bool(set(build_readback["physics_domains"]["heat"]) & set(build_readback["physics_domains"]["tds"])),
            "temperature_variable_real": build_readback["temperature_probe_finite"],
            "dependency_readback": diffusion_readback == coupling_expression,
            "stationary_study": build_readback["study_features"] == ["stat"],
            "parameters_readback": all(parameter_readback.values()),
        }
        build_readback["checks"] = build_checks
        if not all(build_checks.values()):
            raise RuntimeError(f"BUILD_VALIDATION FAILED: {build_checks}")
        emit("Build Validation", "Heat → TDS dependency read back and verified", "success", output_summary=json.dumps(build_checks))

        emit("Solve", "Solving coupling ON with D(T)=D0*T/T0")
        timed(performance, "solve_on_ms", study.run)
        x = np.real(np.asarray(model.evaluate("x"))).reshape(-1)
        y = np.real(np.asarray(model.evaluate("y"))).reshape(-1)
        concentration = np.real(np.asarray(model.evaluate("c"))).reshape(-1)
        temperature = np.real(np.asarray(model.evaluate("T"))).reshape(-1)
        flux = np.real(np.asarray(model.evaluate("tds.dflux_cx"))).reshape(-1)
        if not (x.size == y.size == concentration.size == temperature.size == flux.size) or not all(np.all(np.isfinite(value)) for value in (concentration, temperature, flux)):
            raise RuntimeError("SOLUTION_VALIDATION FAILED: coupled fields are empty, nonfinite or mismatched")

        expected_temperature = left_temperature + (right_temperature - left_temperature) * x / length
        temperature_rmse = float(np.sqrt(np.mean((temperature - expected_temperature) ** 2)))
        log_ratio = math.log(right_temperature / left_temperature)
        expected_concentration = left_concentration + (right_concentration - left_concentration) * np.log(expected_temperature / left_temperature) / log_ratio
        concentration_rmse = float(np.sqrt(np.mean((concentration - expected_concentration) ** 2)))
        expected_flux_on = -diffusion * (right_temperature - left_temperature) * (right_concentration - left_concentration) / (reference_temperature * length * log_ratio)
        expected_flux_off = -diffusion * (right_concentration - left_concentration) / length
        flux_mean = float(np.mean(flux)); flux_off_mean = float(np.mean(flux_off))
        flux_error = abs(flux_mean - expected_flux_on) / max(abs(expected_flux_on), 1e-30)
        flux_off_error = abs(flux_off_mean - expected_flux_off) / max(abs(expected_flux_off), 1e-30)
        left_outward = float(np.real(np.asarray(model.evaluate("int_left(tds.ndflux_c)")).reshape(-1)[0]))
        right_outward = float(np.real(np.asarray(model.evaluate("int_right(tds.ndflux_c)")).reshape(-1)[0]))
        balance_error = abs(left_outward + right_outward) / max(abs(left_outward), abs(right_outward), 1e-30)
        off_on_difference = float(np.sqrt(np.mean((np.interp(x, x_off, c_off) - concentration) ** 2)))
        off_on_flux_ratio = abs(flux_mean) / max(abs(flux_off_mean), 1e-30)
        concentration_tolerance = max(abs(left_concentration - right_concentration) * 2e-4, 2e-5)
        solution_checks = {
            "solver_off_completed": True, "solver_on_completed": True,
            "finite_fields": True,
            "heat_analytical": temperature_rmse <= 0.05,
            "variable_diffusion_analytical": concentration_rmse <= concentration_tolerance,
            "constant_diffusion_flux": flux_off_error <= 2e-4,
            "variable_diffusion_flux": flux_error <= 2e-4,
            "mass_balance": balance_error <= 1e-6,
            "off_on_solutions_differ": off_on_difference > concentration_tolerance,
            "physical_direction": off_on_flux_ratio > 1.0 if right_temperature > reference_temperature else off_on_flux_ratio < 1.0,
            "coupling_dependency_verified": build_checks["dependency_readback"],
        }
        validation = {
            "passed": all(solution_checks.values()), "checks": solution_checks,
            "node_count": int(concentration.size), "element_count": mesh_elements(mesh), "dof": int(concentration.size * 2),
            "temperature_min_K": float(np.min(temperature)), "temperature_max_K": float(np.max(temperature)),
            "temperature_analytical_rmse_K": temperature_rmse,
            "concentration_min_mol_per_m3": float(np.min(concentration)), "concentration_max_mol_per_m3": float(np.max(concentration)),
            "variable_diffusion_analytical_rmse_mol_per_m3": concentration_rmse,
            "coupling_off_flux_mol_per_m2_s": flux_off_mean, "coupling_on_flux_mol_per_m2_s": flux_mean,
            "expected_off_flux_mol_per_m2_s": expected_flux_off, "expected_on_flux_mol_per_m2_s": expected_flux_on,
            "off_flux_relative_error": flux_off_error, "on_flux_relative_error": flux_error,
            "off_on_concentration_rmse_mol_per_m3": off_on_difference, "off_on_flux_ratio": off_on_flux_ratio,
            "mass_balance_relative_error": balance_error,
        }
        if not validation["passed"]:
            raise RuntimeError(f"SOLUTION_VALIDATION FAILED: {validation}")
        emit("Solution Validation", "Scientific and coupling validation passed", "success", output_summary=json.dumps(validation))

        emit("Export", "Exporting coupled results and MPH")
        result_root = java.result()
        for tag, label, expression, unit, filename in (
            ("pg_c", "Coupled concentration", "c", "mol/m^3", concentration_image),
            ("pg_t", "Temperature driving field", "T", "K", temperature_image),
        ):
            plot = result_root.create(tag, "PlotGroup2D"); plot.label(label)
            surface = plot.feature().create("surf1", "Surface"); surface.set("expr", expression); surface.set("unit", unit); plot.run()
            image = result_root.export().create(f"img_{tag}", "Image"); image.set("plotgroup", tag); image.set("filename", str(filename)); image.set("imagetype", "png"); image.run()
        order = np.argsort(x)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle); writer.writerow(["x_m", "y_m", "temperature_K", "concentration_on_mol_per_m3", "flux_on_mol_per_m2_s"])
            writer.writerows((float(x[i]), float(y[i]), float(temperature[i]), float(concentration[i]), float(flux[i])) for i in order)
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        model.save(str(model_path))
        performance["worker_total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result({
            "success": True, "status": "VERIFIED BASELINE", "command_success": True,
            "model_state_verified": True, "scientific_validation_success": True, "coupling_validation_success": True,
            "run_id": run_id, "comsol_version": str(client.version),
            "artifacts": {"model": str(model_path), "concentration_image": str(concentration_image), "temperature_image": str(temperature_image), "profile": str(csv_path), "validation": str(validation_path), "report_directory": str(reports)},
            "validation": validation, "build_readback": build_readback, "performance": performance,
            "model_state": {
                "geometry": [f"2D rectangle {length:g} × {height:g} m"], "materials": ["User-defined constant thermal properties"],
                "physics": ["Heat Transfer in Solids (ht)", "Transport of Diluted Species (tds)", "One-way D(T)=D0*T/T0"],
                "parameters": plan["parameters"], "selections": [str(edge_map)],
                "boundary_conditions": [f"T={left_temperature:g}/{right_temperature:g} K", f"c={left_concentration:g}/{right_concentration:g} mol/m^3"],
                "mesh": [f"Physics-controlled; {validation['element_count']} elements; {validation['node_count']} nodes"],
                "study": ["Stationary OFF/ON comparison"], "solver": ["COMSOL stationary coupled solver; both cases completed"],
                "results": [f"OFF flux {flux_off_mean:.6g}", f"ON flux {flux_mean:.6g}", f"ON/OFF ratio {off_on_flux_ratio:.6g}"],
                "coupling": build_readback["coupling"],
            },
        })
        client.clear()
        return 0
    except Exception as exc:
        result({"success": False, "status": "FAILED", "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "coupling_validation_success": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-12000:]})
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--request", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().request))
