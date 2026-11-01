from __future__ import annotations

import math
import re
from pathlib import Path

from app.execution.files import bounded_read


MAX_RESULT_BYTES = 24_000_000
MAX_PREVIEW_ATOMS = 500
MAX_PREVIEW_FRAMES = 12


def read_text(root: Path, name: str) -> str:
    path = root / name
    if not path.exists():
        return ""
    return bounded_read(root, name, MAX_RESULT_BYTES).decode("utf-8", errors="replace")


def lammps_result(root: Path, expected_steps: int, *, settings=None) -> dict:
    from app.engines.md_validation import sampling_report

    text = read_text(root, "log.lammps")
    columns: list[str] = []
    series: list[list[float]] = []
    for line in text.splitlines():
        if "SKY_PHASE PRODUCTION" in line:
            series.clear()
            columns.clear()
            continue
        fields = line.split()
        if fields[:2] == ["Step", "Temp"]:
            columns = fields
            continue
        if not columns or len(fields) != len(columns):
            continue
        try:
            values = [float(value) for value in fields]
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in values):
            raise ValueError("LAMMPS produced a non-finite thermodynamic value")
        series.append(values)
    # Plot a bounded sample, but check completion against the complete parsed log.
    checks = {"thermo_present": len(series) >= 2,
              "requested_steps_reached": bool(series and series[-1][0] == expected_steps),
              "no_engine_error": "ERROR:" not in text}
    trajectory = read_text(root, "trajectory.dump")
    frames = trajectory_frames(trajectory)
    checks["trajectory_present"] = bool(frames)
    return {"kind": "molecular_dynamics", "units": "Lennard–Jones reduced units",
            "engine_version": next((line.strip()[:120] for line in text.splitlines() if line.strip()), "UNVERIFIED"),
            "columns": columns, "series": series[::max(1, len(series) // 1000)],
            "frames": frames, "checks": checks,
            "preview": {"total_frames": trajectory.count("ITEM: TIMESTEP"), "shown_frames": len(frames),
                        "max_atoms_per_frame": MAX_PREVIEW_ATOMS, "max_frames": MAX_PREVIEW_FRAMES,
                        "full_data": "trajectory.dump"},
            "scientific_validation": "NEEDS_REVIEW",
            "validation_records": sampling_report(columns, series, settings),
            "limitations": "Finite thermo values and completed steps do not establish equilibration or a real material's properties."}


def trajectory_frames(text: str) -> list[dict]:
    lines = iter(text.splitlines())
    frames = []
    for line in lines:
        if line != "ITEM: TIMESTEP":
            continue
        try:
            step = int(next(lines))
            if next(lines) != "ITEM: NUMBER OF ATOMS":
                break
            count = int(next(lines))
            if not 0 < count <= 10000 or not next(lines).startswith("ITEM: BOX BOUNDS"):
                break
            box = [[float(value) for value in next(lines).split()[:2]] for _ in range(3)]
            if next(lines).split() != ["ITEM:", "ATOMS", "id", "type", "x", "y", "z"]:
                break
            atoms = []
            for _ in range(count):
                row = next(lines).split()
                point = [float(value) for value in row[2:5]]
                if len(point) != 3 or not all(math.isfinite(value) for value in point):
                    raise ValueError("Invalid atom coordinate")
                if len(atoms) < MAX_PREVIEW_ATOMS:
                    atoms.append({"id": int(row[0]), "element": row[1], "position": point})
            frame = {"step": step, "box": box, "atoms": atoms, "total_atoms": count,
                     "preview_truncated": count > MAX_PREVIEW_ATOMS}
            if len(frames) < MAX_PREVIEW_FRAMES:
                frames.append(frame)
            else:
                frames[-1] = frame
        except (StopIteration, IndexError):
            break
    return frames


def poscar_structure(text: str, species: list[str] | None = None) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        scale = float(lines[1])
        lattice = [[float(value) * scale for value in line.split()[:3]] for line in lines[2:5]]
        if scale <= 0 or any(len(row) != 3 for row in lattice):
            raise ValueError("This adapter requires a positive POSCAR scale")
        vasp4 = all(token.isdigit() for token in lines[5].split())
        if vasp4 and not species:
            raise ValueError("VASP4 POSCAR has no element symbols; supply species from the licensed POTCAR")
        elements = species if vasp4 else lines[5].split()
        counts = [int(value) for value in lines[5 if vasp4 else 6].split()]
        if len(elements) != len(counts) or any(count < 0 for count in counts) or not 0 < sum(counts) <= 10000:
            raise ValueError("Unsupported POSCAR species/counts")
        offset = 6 if vasp4 else 7
        offset += int(lines[offset].lower().startswith("s"))
        direct = lines[offset].lower().startswith("d")
        if not direct and not lines[offset].lower().startswith(("c", "k")):
            raise ValueError("POSCAR requires Direct or Cartesian coordinates")
        atoms = []
        for index, element in enumerate(element for element, count in zip(elements, counts) for _ in range(count)):
            values = [float(value) for value in lines[offset + 1 + index].split()[:3]]
            if len(values) != 3 or not all(math.isfinite(value) for value in values):
                raise ValueError("Invalid POSCAR coordinate")
            position = [sum(values[j] * lattice[j][i] for j in range(3)) for i in range(3)] if direct else [value * scale for value in values]
            if len(atoms) < MAX_PREVIEW_ATOMS:
                atoms.append({"id": index + 1, "element": element, "position": position})
        if not all(math.isfinite(value) for row in lattice for value in row):
            raise ValueError("Invalid POSCAR lattice")
        return {"lattice": lattice, "atoms": atoms, "units": "angstrom", "total_atoms": sum(counts),
                "preview_truncated": sum(counts) > MAX_PREVIEW_ATOMS}
    except (IndexError, TypeError) as exc:
        raise ValueError("Incomplete POSCAR") from exc


def vasp_result(root: Path) -> dict:
    from app.engines.vasp import incar_tags, numeric_tag

    outcar, oszicar = read_text(root, "OUTCAR"), read_text(root, "OSZICAR")
    tags = incar_tags(read_text(root, "INCAR"))
    nsw, ibrion = int(numeric_tag(tags, "NSW", 0)), int(numeric_tag(tags, "IBRION", -1))
    ionic_applicable = nsw > 0 and ibrion in {1, 2, 3}
    # A positive OUTCAR convergence marker is evidence; timing/exit status is not.
    energies = [float(value) for value in re.findall(r"\bF=\s*([-+\d.Ee]+)", oszicar)]
    scf = (bool(energies) and outcar.count("aborting loop because EDIFF is reached") >= len(energies)
           and "EDIFF was not reached" not in outcar and numeric_tag(tags, "EDIFF", 1e-4) > 0)
    if not all(math.isfinite(value) for value in energies):
        raise ValueError("Non-finite VASP energy")
    structure_text = read_text(root, "CONTCAR")
    return {"kind": "first_principles", "units": "eV", "columns": ["Ionic step", "Free energy"],
            "series": [[i + 1, value] for i, value in enumerate(energies)],
            "structure": poscar_structure(structure_text) if structure_text else None,
            "task_type": "ionic_relaxation" if ionic_applicable else "molecular_dynamics" if nsw > 0 and ibrion == 0 else "single_point",
            "files_collected": bool(outcar and oszicar), "scf_converged": scf,
            "ionic_converged": "reached required accuracy" in outcar if ionic_applicable else None,
            "ionic_convergence_applicable": ionic_applicable,
            "engine_version": next((line.strip()[:120] for line in outcar.splitlines()
                                    if re.search(r"\bvasp\.\d", line, re.I)), "UNVERIFIED"),
            "checks": {"energy_present": bool(energies),
                       "normal_termination": "General timing and accounting" in outcar,
                       "scf_convergence_reported": scf,
                       "ionic_accuracy_reported": "reached required accuracy" in outcar},
            "scientific_validation": "NEEDS_REVIEW",
            "limitations": "Review electronic convergence, ionic convergence and task-specific tolerances; termination alone is insufficient."}


def materials_studio_result(root: Path) -> dict:
    text = read_text(root, "energy.txt").strip()
    energy = float(text) if text else None
    if energy is not None and not math.isfinite(energy):
        raise ValueError("Non-finite Forcite energy")
    return {"kind": "molecular_energy", "units": "Forcite native energy units; verify installed unit settings",
            "energy": energy, "checks": {"energy_present": energy is not None,
                "document_saved": bool(read_text(root, "model.xsd"))},
            "scientific_validation": "NEEDS_REVIEW",
            "limitations": "Forcite single-point energy only; force-field assignment and physical suitability require review."}
