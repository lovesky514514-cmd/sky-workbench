"""Real COMSOL 6.3 Lithium-Ion Battery minimal discharge benchmark."""

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
        "event_type": event_type, "phase": phase, "label": label, "status": status,
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


def expression(plan: dict[str, Any], name: str, allowed_units: set[str]) -> str:
    value, unit = parameter(plan, name)
    if unit not in allowed_units:
        raise ValueError(f"Battery parameter {name} requires one of {sorted(allowed_units)}, observed {unit}")
    return f"{value:.16g}" if unit == "1" else f"{value:.16g}[{unit}]"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)[:72]


def timed(performance: dict[str, float], name: str, action: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return action()
    finally:
        performance[name] = round((time.perf_counter() - started) * 1000, 3)


def read(feature: Any, name: str) -> str:
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


def configure_electrolyte(feature: Any) -> None:
    feature.set("sigmal_mat", "userdef"); feature.set("sigmal", "sigma_l")
    feature.set("Dl_mat", "userdef"); feature.set("Dl", "D_l")
    feature.set("transpNum_mat", "userdef"); feature.set("transpNum", "t_plus")
    feature.set("fcl_mat", "userdef"); feature.set("fcl", "0")
    feature.set("IonicCorrModel", "NoCorr")
    feature.set("DiffusionCorrModel", "NoCorr")


def configure_electrode(feature: Any, *, prefix: str, equilibrium: str) -> None:
    configure_electrolyte(feature)
    feature.set("sigma", [f"sigma_s_{prefix}", "0", "0", "0", f"sigma_s_{prefix}", "0", "0", "0", f"sigma_s_{prefix}"])
    feature.set("epsl", f"epsl_{prefix}")
    feature.set("epss", f"epss_{prefix}")
    particle = feature.feature("pin1")
    particle.set("csinit", f"cs0_{prefix}")
    particle.set("cEeqref_mat", "userdef"); particle.set("cEeqref", f"csmax_{prefix}")
    particle.set("Ds_mat", "userdef"); particle.set("Ds", f"Ds_{prefix}")
    particle.set("rp", f"rp_{prefix}")
    particle.set("socmin_mat", "userdef"); particle.set("socmin", "0")
    particle.set("socmax_mat", "userdef"); particle.set("socmax", "1")
    reaction = feature.feature("per1")
    reaction.set("Eeq_mat", "userdef"); reaction.set("Eeq", equilibrium)
    reaction.set("i0refType", "FromRateConstant"); reaction.set("k", f"k_{prefix}"); reaction.set("cl_ref", "cl_ref")
    reaction.set("dEeqdT_mat", "userdef"); reaction.set("dEeqdT", "0[V/K]")


def main(request_path: Path) -> int:
    started = time.perf_counter()
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    plan = request["plan"]
    run_id = str(request["run_id"])
    workspace = Path(request["workspace"]).resolve()
    models = workspace / "models"; outputs = workspace / "outputs" / run_id; reports = workspace / "reports" / run_id
    for directory in (models, outputs, reports):
        directory.mkdir(parents=True, exist_ok=True)

    current, current_unit = parameter(plan, "applied_current_density")
    duration, duration_unit = parameter(plan, "duration")
    initial_concentration, concentration_unit = parameter(plan, "initial_electrolyte_concentration")
    provenance_required = {
        "applied_current_density", "duration", "initial_electrolyte_concentration",
        "negative_electrode_thickness", "separator_thickness", "positive_electrode_thickness",
        "battery_temperature", "electrolyte_conductivity", "electrolyte_diffusivity",
        "cation_transference_number", "negative_electrolyte_volume_fraction", "negative_solid_volume_fraction",
        "positive_electrolyte_volume_fraction", "positive_solid_volume_fraction",
        "negative_solid_conductivity", "positive_solid_conductivity",
        "negative_max_solid_concentration", "positive_max_solid_concentration",
        "negative_initial_solid_concentration", "positive_initial_solid_concentration",
        "negative_solid_diffusivity", "positive_solid_diffusivity",
        "negative_particle_radius", "positive_particle_radius", "negative_rate_constant", "positive_rate_constant",
    }
    provenance = plan.get("battery_cell", {}).get("parameters", {})
    provenance_valid = provenance_required.issubset(provenance) and all(
        str(provenance[name].get("status", "")) == "Benchmark-defined" and bool(provenance[name].get("source"))
        for name in provenance_required
    )
    if current_unit != "A/m^2" or duration_unit != "s" or concentration_unit not in {"mol/m^3", "mol/m³"}:
        raise ValueError("Battery baseline requires A/m^2, s and mol/m^3")
    if min(current, duration, initial_concentration) <= 0:
        raise ValueError("Battery baseline parameters must be positive")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    model_path = models / f"{safe_name(f'lithium_ion_battery_baseline_{stamp}_{run_id[:8]}')}.mph"
    csv_path = outputs / "battery_discharge.csv"
    image_path = outputs / "battery_electrolyte_concentration.png"
    validation_path = reports / "validation.json"
    performance: dict[str, float] = {}
    client = None
    try:
        import jpype
        import mph
        import numpy as np

        emit("Build", "Starting clean COMSOL Lithium-Ion Battery benchmark")
        client = timed(performance, "comsol_session_ms", mph.Client)
        model_util = jpype.JClass("com.comsol.model.util.ModelUtil")
        battery_license = bool(model_util.hasProduct("BATTERYDESIGN"))
        if not battery_license:
            result({"success": False, "status": "MODULE_MISSING", "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "error": "COMSOL Battery Design Module is unavailable"})
            client.clear(); return 2

        model = client.create(model_path.stem); java = model.java; params = java.param()
        parameter_expressions = {
            "L_neg": expression(plan, "negative_electrode_thickness", {"um"}),
            "L_sep": expression(plan, "separator_thickness", {"um"}),
            "L_pos": expression(plan, "positive_electrode_thickness", {"um"}),
            "T_bat": expression(plan, "battery_temperature", {"K"}),
            "i_app": f"{current:.16g}[A/m^2]", "cl_0": f"{initial_concentration:.16g}[mol/m^3]", "cl_ref": f"{initial_concentration:.16g}[mol/m^3]",
            "sigma_l": expression(plan, "electrolyte_conductivity", {"S/m"}),
            "D_l": expression(plan, "electrolyte_diffusivity", {"m^2/s", "m²/s"}),
            "t_plus": expression(plan, "cation_transference_number", {"1"}),
            "epsl_neg": expression(plan, "negative_electrolyte_volume_fraction", {"1"}),
            "epss_neg": expression(plan, "negative_solid_volume_fraction", {"1"}),
            "epsl_pos": expression(plan, "positive_electrolyte_volume_fraction", {"1"}),
            "epss_pos": expression(plan, "positive_solid_volume_fraction", {"1"}),
            "sigma_s_neg": expression(plan, "negative_solid_conductivity", {"S/m"}),
            "sigma_s_pos": expression(plan, "positive_solid_conductivity", {"S/m"}),
            "csmax_neg": expression(plan, "negative_max_solid_concentration", {"mol/m^3", "mol/m³"}),
            "csmax_pos": expression(plan, "positive_max_solid_concentration", {"mol/m^3", "mol/m³"}),
            "cs0_neg": expression(plan, "negative_initial_solid_concentration", {"mol/m^3", "mol/m³"}),
            "cs0_pos": expression(plan, "positive_initial_solid_concentration", {"mol/m^3", "mol/m³"}),
            "Ds_neg": expression(plan, "negative_solid_diffusivity", {"m^2/s", "m²/s"}),
            "Ds_pos": expression(plan, "positive_solid_diffusivity", {"m^2/s", "m²/s"}),
            "rp_neg": expression(plan, "negative_particle_radius", {"um"}),
            "rp_pos": expression(plan, "positive_particle_radius", {"um"}),
            "k_neg": expression(plan, "negative_rate_constant", {"m/s"}),
            "k_pos": expression(plan, "positive_rate_constant", {"m/s"}),
        }
        for name, parameter_expression in parameter_expressions.items():
            params.set(name, parameter_expression)
        parameter_readback = {name: str(params.get(name)) for name in parameter_expressions}

        component = java.component().create("comp1", True)
        geometry = component.geom().create("geom1", 1)
        interval = geometry.feature().create("i1", "Interval")
        interval.set("specify", "len")
        interval.setIndex("len", "L_neg", 0); interval.setIndex("len", "L_sep", 1); interval.setIndex("len", "L_pos", 2)
        timed(performance, "geometry_ms", geometry.run)
        mesh = component.mesh().create("mesh1")

        emit("Build", "Creating Battery Design Module LithiumIonBatteryMPH interface")
        battery = component.physics().create("liion", "LithiumIonBatteryMPH", "geom1")
        separator = battery.feature("sep1"); configure_electrolyte(separator); separator.set("epsl", "1")
        negative = battery.feature().create("pce1", "PorousElectrode", 1); negative.selection().set([1]); configure_electrode(negative, prefix="neg", equilibrium="0.1[V]+0.5[V]*(1-liion.cs_surface/csmax_neg)")
        positive = battery.feature().create("pce2", "PorousElectrode", 1); positive.selection().set([3]); configure_electrode(positive, prefix="pos", equilibrium="4.2[V]-0.5[V]*(liion.cs_surface/csmax_pos)")
        ground = battery.feature().create("egnd1", "ElectricGround", 0); ground.selection().set([1])
        load = battery.feature().create("ecd1", "ElectrodeNormalCurrentDensity", 0); load.selection().set([4]); load.set("nis", "-i_app")
        battery.feature("init1").set("cl", "cl_0")
        java.common("cminpt").set("modified", [["temperature", "T_bat"]])

        timed(performance, "mesh_ms", mesh.run)
        study = java.study().create("std1")
        initialization = study.feature().create("cdi", "CurrentDistributionInitialization"); initialization.setSolveFor("/physics/liion", True)
        transient = study.feature().create("time", "Transient"); transient.set("tlist", f"range(0,{max(duration / 10, 0.1):.16g},{duration:.16g})"); transient.setSolveFor("/physics/liion", True)
        point_positive = component.cpl().create("positive_cc", "Integration"); point_positive.selection().geom("geom1", 0); point_positive.selection().set([4])

        build_readback = {
            "license": {"product": "BATTERYDESIGN", "available": battery_license},
            "physics_tag": str(battery.tag()), "physics_type": str(battery.getType()),
            "geometry_domains": int(geometry.getNObjects()) if hasattr(geometry, "getNObjects") else 3,
            "separator": {"type": str(separator.getType()), "selection": [int(value) for value in separator.selection().entities()]},
            "negative": {"type": str(negative.getType()), "selection": [int(value) for value in negative.selection().entities()], "Eeq": read(negative.feature("per1"), "Eeq")},
            "positive": {"type": str(positive.getType()), "selection": [int(value) for value in positive.selection().entities()], "Eeq": read(positive.feature("per1"), "Eeq")},
            "ground": {"type": str(ground.getType()), "selection": [int(value) for value in ground.selection().entities()]},
            "load": {"type": str(load.getType()), "selection": [int(value) for value in load.selection().entities()], "current": read(load, "nis")},
            "initial_concentration": read(battery.feature("init1"), "cl"),
            "study_features": [str(value) for value in study.feature().tags()], "parameters": parameter_readback,
        }
        build_checks = {
            "battery_license": battery_license,
            "battery_specific_interface": build_readback["physics_type"] == "LithiumIonBatteryMPH",
            "separator_domain": build_readback["separator"] == {"type": "Separator", "selection": [2]},
            "negative_electrode_domain": build_readback["negative"]["type"] == "PorousElectrode" and build_readback["negative"]["selection"] == [1],
            "positive_electrode_domain": build_readback["positive"]["type"] == "PorousElectrode" and build_readback["positive"]["selection"] == [3],
            "ground_boundary": build_readback["ground"]["selection"] == [1],
            "current_boundary": build_readback["load"]["selection"] == [4] and build_readback["load"]["current"] == "-i_app",
            "initial_concentration": build_readback["initial_concentration"] == "cl_0",
            "time_dependent_study": build_readback["study_features"] == ["cdi", "time"],
            "parameter_readback": all(parameter_readback.values()),
        }
        build_readback["checks"] = build_checks
        if not all(build_checks.values()):
            raise RuntimeError(f"BUILD_VALIDATION FAILED: {build_checks}")
        emit("Build Validation", "Battery write → real readback → compare passed", "success", output_summary=json.dumps(build_checks))

        emit("Solve", "Running short real lithium-ion discharge")
        timed(performance, "solve_ms", study.run)
        emit("Solve", "Battery solver completed", "success")

        x = np.real(np.asarray(model.evaluate("x")))
        concentration = np.real(np.asarray(model.evaluate("cl")))
        electrolyte_potential = np.real(np.asarray(model.evaluate("phil")))
        solid_potential = np.real(np.asarray(model.evaluate("phis")))
        voltage = np.real(np.asarray(model.evaluate("positive_cc(phis)"))).reshape(-1)
        soc_negative = np.real(np.asarray(model.evaluate("liion.soc_average_pce1"))).reshape(-1)
        soc_positive = np.real(np.asarray(model.evaluate("liion.soc_average_pce2"))).reshape(-1)
        total_current = np.real(np.asarray(model.evaluate("liion.Ilx+liion.Isx")))
        total_current_finite = total_current[np.isfinite(total_current)]
        arrays = (concentration, electrolyte_potential, solid_potential, voltage, soc_negative, soc_positive, total_current)
        finite = bool(
            all(value.size > 0 and np.all(np.isfinite(value)) for value in (concentration, electrolyte_potential, voltage, soc_negative, soc_positive))
            and total_current_finite.size > 0
            and np.any(np.isfinite(solid_potential))
        )
        voltage_initial, voltage_final = float(voltage.reshape(-1)[0]), float(voltage.reshape(-1)[-1])
        concentration_min, concentration_max = float(np.min(concentration)), float(np.max(concentration))
        current_mean = float(np.mean(total_current_finite)); current_spread = float(np.max(total_current_finite) - np.min(total_current_finite))
        current_error = abs(abs(current_mean) - current) / current
        current_uniformity = current_spread / current
        soc_negative_change = float(soc_negative[-1] - soc_negative[0])
        soc_positive_change = float(soc_positive[-1] - soc_positive[0])
        charge_areal = current * duration
        solution_checks = {
            "solver_completed": True, "finite_fields": finite,
            "positive_concentration": concentration_min > 0,
            "physical_cell_voltage": 2.0 <= min(voltage_initial, voltage_final) and max(voltage_initial, voltage_final) <= 5.0,
            "discharge_voltage_direction": voltage_final < voltage_initial,
            "boundary_current_matches": current_error <= 0.02,
            "current_conservation": current_uniformity <= 0.02,
            "electrode_soc_changes": abs(soc_negative_change) > 1e-8 and abs(soc_positive_change) > 1e-8,
            "opposite_electrode_inventory_direction": math.copysign(1, soc_negative_change) != math.copysign(1, soc_positive_change),
            "unit_consistency": True,
            "parameter_provenance": provenance_valid,
        }
        validation = {
            "passed": all(solution_checks.values()), "checks": solution_checks,
            "node_count": int(concentration.size), "element_count": mesh_elements(mesh), "dof": int(sum(value.size for value in arrays[:4])),
            "concentration_min_mol_per_m3": concentration_min, "concentration_max_mol_per_m3": concentration_max,
            "voltage_initial_V": voltage_initial, "voltage_final_V": voltage_final, "voltage_change_V": voltage_final - voltage_initial,
            "current_density_mean_A_per_m2": current_mean, "applied_current_density_A_per_m2": current,
            "current_relative_error": current_error, "current_uniformity_relative": current_uniformity,
            "negative_soc_change": soc_negative_change, "positive_soc_change": soc_positive_change,
            "applied_areal_charge_C_per_m2": charge_areal,
        }
        if not validation["passed"]:
            raise RuntimeError(f"SOLUTION_VALIDATION FAILED: {validation}")
        emit("Solution Validation", "Battery scientific validation passed", "success", output_summary=json.dumps(validation))

        emit("Export", "Exporting battery artifacts")
        results = java.result(); plot = results.create("pg_battery", "PlotGroup1D"); plot.label("Electrolyte concentration")
        line = plot.feature().create("lngr1", "LineGraph"); line.set("expr", "cl"); line.set("unit", "mol/m^3"); plot.run()
        image = results.export().create("img_battery", "Image"); image.set("plotgroup", "pg_battery"); image.set("filename", str(image_path)); image.set("imagetype", "png"); image.run()
        flat_x = np.asarray(x).reshape(-1); flat_c = np.asarray(concentration).reshape(-1)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle); writer.writerow(["sample", "x_m", "electrolyte_concentration_mol_per_m3"])
            writer.writerows((index, float(flat_x[index % flat_x.size]), float(value)) for index, value in enumerate(flat_c))
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        model.save(str(model_path)); performance["worker_total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result({
            "success": True, "status": "VERIFIED BASELINE", "command_success": True, "model_state_verified": True,
            "scientific_validation_success": True, "run_id": run_id, "comsol_version": str(client.version),
            "artifacts": {"model": str(model_path), "concentration_image": str(image_path), "discharge_csv": str(csv_path), "validation": str(validation_path), "report_directory": str(reports)},
            "validation": validation, "build_readback": build_readback, "performance": performance,
            "model_state": {
                "geometry": ["1D negative electrode / separator / positive electrode"],
                "materials": ["Explicit user-provided minimal benchmark properties; no commercial chemistry claim"],
                "physics": ["Lithium-Ion Battery (LithiumIonBatteryMPH)"], "parameters": plan["parameters"],
                "selections": ["negative domain 1", "separator domain 2", "positive domain 3"],
                "boundary_conditions": ["negative current collector ground", f"positive collector discharge {current:g} A/m^2"],
                "mesh": [f"Physics-controlled; {validation['element_count']} elements"], "study": [f"Current distribution initialization + transient 0..{duration:g} s"],
                "solver": ["COMSOL Battery Design Module transient solver; completed"],
                "results": [f"Voltage {voltage_initial:.6g} → {voltage_final:.6g} V", f"Electrolyte concentration {concentration_min:.6g}..{concentration_max:.6g} mol/m^3"],
            },
        })
        client.clear(); return 0
    except Exception as exc:
        result({"success": False, "status": "FAILED", "command_success": False, "model_state_verified": False, "scientific_validation_success": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-12000:]})
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--request", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().request))
