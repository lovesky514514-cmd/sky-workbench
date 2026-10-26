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
    # MATLAB and Wolfram provide a bounded, explicit local handshake. Their
    # executable presence alone must not imply an active license or solver.
    for engine, names, connector in (
        ("MATLAB", ("matlab.exe", "matlab"), "matlab_batch"),
        ("Wolfram", ("wolframscript.exe", "wolframscript", "WolframKernel.exe", "MathKernel.exe"), "wolframscript_local"),
        ("Materials Studio", ("MaterialsStudio.exe",), "none"),
    ):
        if on_optional is not None:
            on_optional(engine, "running")
        executable = _find_executable(engine, names)
        probe_available = bool(executable) and connector != "none" and (
            engine != "Wolfram" or "wolframscript" in Path(executable).name.lower()
        )
        detail = (
            "Materials Studio detected; select its installed version and licensed MaterialsScript interface before connection"
            if engine == "Materials Studio" and executable else
            "Wolfram kernel detected, but WolframScript is required for the isolated local handshake"
            if engine == "Wolfram" and executable and not probe_available else
            "Executable detected; license, model readback and execution remain unverified"
            if executable else "No executable detected in PATH or common installation directories"
        )
        result.append(AdapterCapability(
            engine, bool(executable), executable, "UNVERIFIED", "UNAVAILABLE",
            detail,
            connector if probe_available else "none",
            "AVAILABLE_ON_REQUEST" if probe_available else "UNAVAILABLE",
        ))
        if on_optional is not None:
            on_optional(engine, "detected" if executable else "unavailable")
    return [item.as_dict() for item in result]


def _find_executable(engine: str, names: tuple[str, ...]) -> str:
    """Search PATH first, then vendor folders only; never crawl user files."""
    for name in names:
        if found := shutil.which(name):
            return str(Path(found))
    roots = [Path(value) for key in ("ProgramFiles", "ProgramFiles(x86)")
             if (value := os.environ.get(key))]
    patterns = {
        "MATLAB": ("MATLAB/R*/bin/matlab.exe",),
        "Wolfram": ("Wolfram Research/WolframScript/wolframscript.exe",),
        "Materials Studio": ("BIOVIA/Materials Studio*/MaterialsStudio.exe",),
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
    if engine == "Wolfram" and "wolframscript" in Path(executable).name.lower():
        # -local is mandatory: WolframScript must not fall back to the cloud.
        return [executable, "-local", "-code", f'Print["{PROBE_MARKER}"]']
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
            owned = await OwnedProcess.spawn(command, cwd=workspace)
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


async def probe_engine(engine: str) -> dict[str, object]:
    """Run only an explicit local handshake; never upgrade scientific flags."""
    names = {"matlab": "MATLAB", "wolfram": "Wolfram", "materials_studio": "Materials Studio", "comsol": "COMSOL"}
    selected = names.get(engine.lower())
    if selected is None:
        raise ValueError("Unknown scientific engine")
    record = next(item for item in capabilities() if item["engine"] == selected)
    status, elapsed = "UNAVAILABLE", 0
    if selected == "COMSOL":
        status = "USE_EXISTING_JAVA_BRIDGE" if record["installed"] else "UNAVAILABLE"
    elif record["probe"] == "AVAILABLE_ON_REQUEST":
        status, elapsed = await _handshake(probe_command(selected, str(record["executable"])))
    elif record["installed"]:
        status = "CONNECTOR_UNAVAILABLE"
    return {
        "engine": selected, "status": status, "elapsed_ms": elapsed,
        "readback": "UNVERIFIED", "execute": "UNAVAILABLE",
        "detail": "Local engine handshake only; no model was opened or certified" if status == "HANDSHAKE_PASSED"
                  else "Engine connection unavailable or unverified; no scientific action was executed",
    }
