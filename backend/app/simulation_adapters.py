"""Small, fail-closed contract for locally installed scientific engines.

Discovery does not start a licensed product. A user-requested handshake runs a
fixed, harmless expression in an owned child process; it never certifies model
readback or scientific execution. COMSOL keeps its existing Java Bridge path.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from app.comsol.discovery import ComsolJavaEnvironment, discover_comsol_java
from app.comsol.owned_process import OwnedProcess
from app.config import WORKSPACE_ROOT
from app.engines.capabilities import CapabilityRegistry, executable_hash


PROBE_MARKER = "SWB_ENGINE_PROBE_OK"


@dataclass(frozen=True, slots=True)
class AdapterCapability:
    engine: str
    installed: bool
    executable: str
    readback: str
    execute: str
    detail: str
    connector: str = "none"
    probe: str = "UNAVAILABLE"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def capabilities(
    comsol: ComsolJavaEnvironment | None = None,
    on_optional: Callable[[str, str], None] | None = None,
) -> list[dict[str, object]]:
    """Inspect bounded install locations without starting engines or reading projects."""
    comsol = comsol if comsol is not None else discover_comsol_java()
    result = [AdapterCapability(
        "COMSOL", comsol.found, comsol.java if comsol.found else "",
        "REQUIRES_LIVE_BRIDGE" if comsol.found else "UNAVAILABLE",
        "REQUIRES_VERSION_SPECIFIC_SCIENTIFIC_VALIDATION" if comsol.found else "UNAVAILABLE",
        comsol.compatibility if comsol.found else "COMSOL Model API installation not detected",
    )]
    # Executable discovery never implies a license or a verified calculation.
    for engine, names, connector in (
        ("MATLAB", ("matlab.exe", "matlab"), "matlab_batch"),
        ("LAMMPS", ("lmp.exe", "lmp", "lmp_serial.exe", "lmp_serial"), "lammps_local"),
        ("VASP", ("vasp_std", "vasp_std.exe"), "vasp_local"),
        ("Materials Studio", ("RunMatScript.bat", "RunMatScript.exe", "MaterialsStudio.exe"), "materialsscript_local"),
    ):
        if on_optional is not None:
            on_optional(engine, "running")
        executable = _find_executable(engine, names)
        probe_available = bool(executable) and engine == "MATLAB"
        detail = (
            "Materials Studio detected; select its installed version and licensed MaterialsScript interface before connection"
            if engine == "Materials Studio" and executable else
            "Executable detected; license, model readback and execution remain unverified"
            if executable else "No executable detected in PATH or common installation directories"
        )
        result.append(AdapterCapability(
            engine, bool(executable), executable, "UNVERIFIED", "UNAVAILABLE",
            detail,
            connector,
            "AVAILABLE_ON_REQUEST" if probe_available else "UNAVAILABLE",
        ))
        if on_optional is not None:
            on_optional(engine, "detected" if executable else "unavailable")
    return [item.as_dict() for item in result]


def _find_executable(engine: str, names: tuple[str, ...]) -> str:
    """Search PATH first, then vendor folders only; never crawl user files."""
    configured = os.environ.get({"MATLAB": "SKY_MATLAB_EXECUTABLE", "LAMMPS": "SKY_LAMMPS_EXECUTABLE",
                                 "VASP": "SKY_VASP_EXECUTABLE", "Materials Studio": "SKY_MS_EXECUTABLE"}[engine])
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    for name in names:
        if found := shutil.which(name):
            return str(Path(found))
    roots = [Path(value) for key in ("ProgramFiles", "ProgramFiles(x86)")
             if (value := os.environ.get(key))]
    patterns = {
        "MATLAB": ("MATLAB/R*/bin/matlab.exe",),
        "LAMMPS": ("LAMMPS*/bin/lmp.exe",),
        "VASP": (),
        "Materials Studio": ("BIOVIA/Materials Studio*/etc/Scripting/RunMatScript.bat", "BIOVIA/Materials Studio*/MaterialsStudio.exe"),
    }[engine]
    for root in roots:
        for pattern in patterns:
            for path in sorted(root.glob(pattern), reverse=True):
                if path.is_file():
                    return str(path)
    return ""


def probe_command(engine: str, executable: str) -> list[str]:
    """Never place a user prompt or user path into an engine command."""
    if engine == "MATLAB":
        return [executable, "-batch", f"disp('{PROBE_MARKER}')"]
    raise ValueError("No safe local handshake is defined for this engine")


def classify_handshake(returncode: int, output: bytes) -> str:
    """Return a small status vocabulary, never the vendor's raw diagnostics."""
    if returncode == 0 and PROBE_MARKER.encode() in output.splitlines():
        return "HANDSHAKE_PASSED"
    if returncode != 0 and re.search(rb"\b(?:licen[sc]e|activation|authenticate|credential)\b", output, re.I):
        return "LICENSE_OR_ACTIVATION_REQUIRED"
    return "HANDSHAKE_FAILED"


async def _handshake(command: list[str]) -> tuple[str, int]:
    """Bounded, owned-process probe; discard vendor output to avoid path leaks."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="swb-engine-probe-") as workspace:
        try:
            from app.script_workspace import child_environment

            owned = await OwnedProcess.spawn(command, cwd=workspace, env=child_environment())
        except (OSError, RuntimeError):
            return "START_FAILED", round((time.perf_counter() - started) * 1000)
        try:
            try:
                output, _ = await asyncio.wait_for(owned.process.communicate(), timeout=90)
                status = classify_handshake(owned.process.returncode, output)
            except asyncio.TimeoutError:
                status = "TIMED_OUT"
        finally:
            cleanup = await owned.terminate()
        if not cleanup.get("reaped"):
            status = "PROCESS_CLEANUP_UNVERIFIED"
    return status, round((time.perf_counter() - started) * 1000)


async def probe_engine(engine: str, *, registry: CapabilityRegistry | None = None) -> dict[str, object]:
    """Run only an explicit local handshake; never upgrade scientific flags."""
    names = {"matlab": "MATLAB", "lammps": "LAMMPS", "vasp": "VASP", "materials_studio": "Materials Studio", "comsol": "COMSOL"}
    selected = names.get(engine.lower())
    if selected is None:
        raise ValueError("Unknown scientific engine")
    record = next(item for item in capabilities() if item["engine"] == selected)
    status, elapsed = "UNAVAILABLE", 0
    if selected == "COMSOL":
        status = "USE_EXISTING_JAVA_BRIDGE" if record["installed"] else "UNAVAILABLE"
    elif record["probe"] == "AVAILABLE_ON_REQUEST":
        executable = str(record["executable"])
        try:
            before = await asyncio.to_thread(executable_hash, executable)
            status, elapsed = await _handshake(probe_command(selected, executable))
            # A successful marker belongs only to the exact binary that was tested.
            if status == "HANDSHAKE_PASSED":
                if before != await asyncio.to_thread(executable_hash, executable):
                    status = "EXECUTABLE_CHANGED"
                else:
                    connections = registry or CapabilityRegistry(WORKSPACE_ROOT / "engine_tasks")
                    await asyncio.to_thread(connections.connected, selected, executable)
        except OSError:
            status = "EXECUTABLE_UNAVAILABLE"
    elif record["installed"]:
        status = "CONNECTOR_UNAVAILABLE"
    return {
        "engine": selected, "status": status, "elapsed_ms": elapsed,
        "readback": "UNVERIFIED", "execute": "UNAVAILABLE",
        "detail": "Local engine handshake only; no model was opened or certified" if status == "HANDSHAKE_PASSED"
                  else "Engine connection unavailable or unverified; no scientific action was executed",
    }
