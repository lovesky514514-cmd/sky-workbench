"""Small input contracts; neither POTCAR content nor DFT accuracy is inferred."""

from __future__ import annotations

import math
import re


def incar_tags(text: str) -> dict[str, str]:
    tags = {}
    for line in text.splitlines():
        for entry in re.split(r";", re.split(r"[!#]", line, maxsplit=1)[0]):
            if "=" not in entry:
                continue
            name, value = (part.strip() for part in entry.split("=", 1))
            name = name.upper()
            if name in tags and tags[name] != value:
                raise ValueError(f"Conflicting INCAR values for {name}")
            tags[name] = value
    return tags


def numeric_tag(tags: dict[str, str], name: str, default: float) -> float:
    try:
        value = float(tags.get(name, str(default)).replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise ValueError(f"INCAR {name} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"INCAR {name} must be finite")
    return value


def validate_vasp_files(files: dict[str, str]) -> dict:
    tags = incar_tags(files["INCAR"])
    for name, default, minimum, integer in [("ENCUT", 0, 0, False), ("EDIFF", 1e-4, -1, False),
                                            ("NELM", 60, 0, True), ("NSW", 0, -1, True)]:
        value = numeric_tag(tags, name, default)
        if name == "ENCUT" and name not in tags:
            continue  # VASP can derive its default from the licensed POTCAR.
        if value <= minimum or (name == "EDIFF" and value < 0) or integer and value != int(value):
            raise ValueError(f"Invalid INCAR {name}")
    ibrion = numeric_tag(tags, "IBRION", -1)
    if ibrion != int(ibrion) or int(ibrion) not in {-1, 0, 1, 2, 3, 5, 6, 7, 8, 44}:
        raise ValueError("Unsupported or invalid INCAR IBRION")
    titles = re.findall(r"^\s*TITEL\s*=\s*(.+)$", files["POTCAR"], re.M)
    species = []
    for title in titles:
        tokens = title.split()
        if len(tokens) < 2 or not re.fullmatch(r"[A-Z][a-z]?(?:_\w+)?", tokens[1]):
            raise ValueError("POTCAR species identifiers are not readable")
        species.append(tokens[1].split("_", 1)[0])
    if not species:
        raise ValueError("POTCAR must contain readable TITEL species identifiers")
    lines = [line.strip() for line in files["POSCAR"].splitlines() if line.strip()]
    if len(lines) < 8:
        raise ValueError("Incomplete POSCAR")
    vasp4 = all(token.isdigit() for token in lines[5].split())
    elements = species if vasp4 else lines[5].split()
    if elements != species:
        raise ValueError("POSCAR element order does not match POTCAR TITEL order")
    counts = lines[5 if vasp4 else 6].split()
    if len(counts) != len(elements) or not all(token.isdigit() and int(token) > 0 for token in counts):
        raise ValueError("POSCAR requires positive species counts matching POTCAR")
    points = [line.strip() for line in files["KPOINTS"].splitlines() if line.strip()]
    if len(points) < 4:
        raise ValueError("Incomplete KPOINTS")
    try:
        count = int(points[1])
        if count < 0:
            raise ValueError
        if count == 0 and points[2].lower().startswith(("g", "m")):
            mesh = [int(value) for value in points[3].split()]
            if len(mesh) != 3 or any(value <= 0 for value in mesh):
                raise ValueError
    except ValueError as exc:
        raise ValueError("KPOINTS requires a valid count and positive automatic mesh") from exc
    nsw = int(numeric_tag(tags, "NSW", 0))
    task_type = "single_point" if nsw == 0 or int(ibrion) == -1 else "molecular_dynamics" if int(ibrion) == 0 else "ionic_relaxation" if int(ibrion) in {1, 2, 3} else "response"
    return {"species": species, "vasp4": vasp4, "tags": tags, "task_type": task_type,
            "ionic_convergence_applicable": task_type == "ionic_relaxation"}
