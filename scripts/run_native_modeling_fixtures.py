"""Exercise typed fixtures for paired cases 20/23/24; do not claim NLP compilation."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def curve_svg(path, series):
    colors = ("#2563eb", "#dc2626", "#059669")
    values = [y for row in series for _, y in row]
    low, high = min(values), max(values)
    span = max(high - low, 1e-8)
    lines = []
    for index, row in enumerate(series):
        points = " ".join(
            f"{40 + 540 * x / row[-1][0]:.2f},{260 - 220 * (y - low) / span:.2f}"
            for x, y in row
        )
        lines.append(
            f'<polyline fill="none" stroke="{colors[index % 3]}" stroke-width="2" points="{points}"/>'
        )
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="620" height="300" viewBox="0 0 620 300"><rect width="620" height="300" fill="white"/><path d="M40 30 V260 H590" fill="none" stroke="black"/>'
        + "".join(lines)
        + "</svg>",
        encoding="utf-8",
    )


async def exercise(args):
    from app.engines.manager import EngineTaskManager
    from app.engines.models import EngineTaskRequest, LammpsInput
    from app.script_workspace import ScriptRunManager, executable_for
    from app.simulation_adapters import capabilities
    from app.source_revision import source_revision

    output = args.out.resolve()
    before = source_revision()
    records = []
    installed = capabilities()
    for case, engine in [
        (16, "VASP"),
        (17, "VASP"),
        (18, "Materials Studio"),
        (19, "VASP"),
        (21, "LAMMPS"),
        (22, "LAMMPS"),
        (25, "MATLAB"),
    ]:
        found = next(item for item in installed if item["engine"] == engine)
        records.append(
            dict(
                id=case,
                engine=engine,
                status="ADAPTER_OR_INPUT_REQUIRED"
                if found["installed"]
                else "SKIPPED_SOFTWARE_UNAVAILABLE",
                scientific_validation="NOT_EVALUATED",
            )
        )

    lammps = (
        str(args.lammps.resolve())
        if args.lammps
        else next(
            item["executable"] for item in installed if item["engine"] == "LAMMPS"
        )
    )
    if lammps and Path(lammps).is_file():
        manager = EngineTaskManager(output / "lammps", lambda engine: lammps)
        try:
            connection = await manager.connect("LAMMPS")
            if connection["status"] != "HANDSHAKE_PASSED":
                raise RuntimeError("LAMMPS handshake failed")
            request = EngineTaskRequest(
                title="Case 20 explicit typed LJ fixture",
                project_id="isolated-native-20",
                confirmed=True,
                inputs=LammpsInput(
                    cells=3,
                    density=0.75,
                    temperature=0.8,
                    timestep=0.002,
                    equilibration_steps=1000,
                    steps=2000,
                    seed=12345,
                ),
                timeout_seconds=120,
            )
            started = await manager.start(request)
            await manager.tasks[started["run_id"]]
            result = manager.status(started["run_id"])
            passed = (
                result["state"] == "COLLECTED"
                and result.get("cleanup", {}).get("reaped")
                and all(result["result"]["checks"].values())
            )
            write_json(output / "lammps_20.json", result)
            records.append(
                dict(
                    id=20,
                    engine="LAMMPS",
                    status=result["state"],
                    passed=bool(passed),
                    scientific_validation=result["scientific_validation"],
                    evidence="lammps_20.json",
                )
            )
        finally:
            await manager.shutdown()
    else:
        records.append(
            dict(id=20, engine="LAMMPS", status="SKIPPED_SOFTWARE_UNAVAILABLE")
        )

    interpreter = (
        str(args.mathics_python.resolve())
        if args.mathics_python
        else executable_for("Mathics3")
    )
    if interpreter and Path(interpreter).is_file():
        manager = ScriptRunManager(output / "scripts", lambda engine: interpreter)
        # Exact underdamped solution for m=1, c=1/5, k=4. Check the ODE,
        # dissipation identity and both initial conditions before sampling it.
        x = "(Exp[-t/10]*(Cos[Sqrt[399]*t/10]+Sin[Sqrt[399]*t/10]/Sqrt[399])/100)"
        source23 = (
            "{"
            + f"Simplify[D[{x},{{t,2}}]+D[{x},t]/5+4*{x}],Simplify[D[(D[{x},t])^2/2+2*({x})^2,t]+(D[{x},t])^2/5],Limit[{x},t->0]-1/100,Limit[D[{x},t],t->0],Table[N[{x}],{{t,0,20,1/5}}]"
            + "}"
        )
        source24 = "Table[N[a*Exp[-3*t/10]+1/2],{a,1,3},{t,0,10}]"
        try:
            for case, source in [(23, source23), (24, source24)]:
                start = await manager.start("Mathics3", source)
                await manager.wait(start["run_id"])
                record = manager.status(start["run_id"])
                (output / f"mathics_{case}.wl").write_text(source, encoding="utf-8")
                result = record.get("mathics_result") or {}
                text = result.get("text", "")
                try:
                    numeric = json.loads(
                        text.replace("{", "[").replace("}", "]").replace("*^", "e")
                    )
                    if case == 23:
                        initial_checks = numeric[:4]
                        rows = [[i / 5, value] for i, value in enumerate(numeric[4])]
                        expected = [
                            0.01
                            * math.exp(-t / 10)
                            * (
                                math.cos(math.sqrt(399) * t / 10)
                                + math.sin(math.sqrt(399) * t / 10) / math.sqrt(399)
                            )
                            for t, _ in rows
                        ]
                        error = max(
                            abs(y - expect)
                            for (_, y), expect in zip(rows, expected, strict=True)
                        )
                        passed = (
                            len(rows) == 101
                            and initial_checks == [0, 0, 0, 0]
                            and error < 1e-7
                        )
                        series = [rows]
                    else:
                        series = [
                            [[i, value] for i, value in enumerate(row)]
                            for row in numeric
                        ]
                        error = max(
                            abs(value - (a * math.exp(-0.3 * t) + 0.5))
                            for a, row in enumerate(series, 1)
                            for t, value in row
                        )
                        passed = (
                            len(series) == 3
                            and all(len(row) == 11 for row in series)
                            and error < 1e-5
                        )
                    for index, row in enumerate(series, 1):
                        with (output / f"case_{case}_series_{index}.csv").open(
                            "w", newline="", encoding="utf-8"
                        ) as stream:
                            writer = csv.writer(stream)
                            writer.writerow(
                                [
                                    "t_s" if case == 23 else "t_dimensionless",
                                    "x_m" if case == 23 else "y_dimensionless",
                                ]
                            )
                            writer.writerows(row)
                    curve_svg(output / f"case_{case}.svg", series)
                except (ValueError, TypeError, IndexError, KeyError):
                    passed, error = False, None
                passed = bool(passed and record["state"] == "COMPLETED")
                write_json(
                    output / f"mathics_{case}.json",
                    dict(
                        record=record,
                        fixture_passed=passed,
                        max_sample_error=error,
                        scope="Manually transcribed fixture; exact identities and numeric samples only, not natural-language or experimental certification",
                    ),
                )
                records.append(
                    dict(
                        id=case,
                        engine="Mathics3",
                        status=record["state"],
                        passed=passed,
                        evidence=f"mathics_{case}.json",
                    )
                )
        finally:
            await manager.shutdown()
    else:
        records.extend(
            dict(id=case, engine="Mathics3", status="SKIPPED_SOFTWARE_UNAVAILABLE")
            for case in (23, 24)
        )
    after = source_revision()
    write_json(
        output / "report.json",
        dict(
            source_before=before,
            source_after=after,
            source_unchanged=before == after,
            compilation="Typed fixtures transcribed from explicit cases; no language-to-native-input claim",
            records=records,
        ),
    )
    print(json.dumps(records, ensure_ascii=False))
    return (
        0
        if before == after and all(record.get("passed", True) for record in records)
        else 1
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lammps", type=Path)
    parser.add_argument("--mathics-python", type=Path)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        COMSOL_WORKBENCH_WORKSPACE_DIR=str(args.out / "workspace"),
        COMSOL_WORKBENCH_APP_DATA_DIR=str(args.out / "appdata"),
        COMSOL_WORKBENCH_KB_DIR=str(args.out / "empty_kb"),
    )
    if args.lammps:
        os.environ["SKY_LAMMPS_EXECUTABLE"] = str(args.lammps.resolve())
    sys.path.insert(0, str(ROOT / "backend"))
    raise SystemExit(asyncio.run(exercise(args)))
