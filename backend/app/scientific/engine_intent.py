"""Preserve a named engine as a constraint, independently of installation state."""

import re


ENGINE_NAMES = {
    "COMSOL": r"(?<![A-Za-z0-9_])COMSOL(?![A-Za-z0-9_])",
    "MATLAB": r"(?<![A-Za-z0-9_])MATLAB(?![A-Za-z0-9_])",
    "LAMMPS": r"(?<![A-Za-z0-9_])LAMMPS(?![A-Za-z0-9_])",
    "VASP": r"(?<![A-Za-z0-9_])VASP(?![A-Za-z0-9_])",
    "Materials Studio": r"(?<![A-Za-z0-9_])Materials\s+Studio(?![A-Za-z0-9_])",
    "Mathics3": r"(?<![A-Za-z0-9_])Mathics3?(?![A-Za-z0-9_])",
}


def requested_engines(text: str) -> list[str]:
    from app.scientific.request_analysis import positive_mentions

    return [
        name
        for name, pattern in ENGINE_NAMES.items()
        if any(
            positive_mentions(text, (match.group(),))[0]
            for match in re.finditer(pattern, text, re.I)
        )
    ]


def workflow_engine_issue(workflow: str | None, text: str) -> str | None:
    # All currently registered ScientificPlan executors use COMSOL. Native tasks
    # use their own typed inputs; a similar PDE cannot silently change engines.
    selected = requested_engines(text)
    if workflow and selected and "COMSOL" not in selected:
        return "Requested engine has no binding to this COMSOL workflow: " + ", ".join(
            selected
        )
    return None
