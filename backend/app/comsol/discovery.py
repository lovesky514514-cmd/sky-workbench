"""Discover an installed Windows COMSOL Java runtime without a fixed install path."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings_store


@dataclass(frozen=True, slots=True)
class ComsolJavaEnvironment:
    found: bool
    status: str
    detail: str
    root: str = ""
    version: str = ""
    api_jar: str = ""
    java: str = ""
    javac: str = ""
    mphserver: str = ""
    native_libraries: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["native_libraries"] = list(self.native_libraries)
        payload["classpath"] = f"{self.root}\\plugins\\*" if self.found else ""
        return payload


def discover_comsol_java() -> ComsolJavaEnvironment:
    for root in _candidate_roots():
        environment = _inspect_root(root)
        if environment:
            return environment
    return ComsolJavaEnvironment(
        found=False,
        status="REAL_COMSOL_JAVA_CLASSPATH_NOT_FOUND",
        detail="No COMSOL 6.x root with the official API jar, JVM and native libraries was found.",
    )


def _candidate_roots() -> list[Path]:
    settings = settings_store.load().get("comsol", {})
    configured = str(settings.get("root", "")) if isinstance(settings, dict) else ""
    raw: list[Path] = []
    for value in (
        configured,
        os.getenv("COMSOL_ROOT", ""),
        os.getenv("COMSOL_INSTALL_PATH", ""),
    ):
        if value:
            raw.append(Path(value))

    executable = shutil.which("comsol") or shutil.which("comsol.exe")
    if executable:
        candidate = Path(executable).resolve()
        raw.extend(candidate.parents)

    program_root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "COMSOL"
    raw.extend((program_root, program_root / "COMSOL64"))
    if program_root.is_dir():
        raw.extend(path for path in program_root.rglob("Multiphysics") if path.is_dir())

    # Portable/non-default installs are common in lab workstations. Probe only
    # the conventional top-level COMSOL folder on mounted drive letters.
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        raw.append(Path(f"{letter}:\\comsol"))

    result: list[Path] = []
    seen: set[str] = set()
    for path in raw:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _inspect_root(root: Path) -> ComsolJavaEnvironment | None:
    if not root.is_dir():
        return None
    jars = sorted((root / "plugins").glob("com.comsol.api_*.jar"))
    java = root / "java" / "win64" / "jre" / "bin" / "java.exe"
    javac = root / "java" / "win64" / "jre" / "bin" / "javac.exe"
    mphserver = root / "bin" / "win64" / "comsolmphserver.exe"
    native = (root / "bin" / "win64", root / "lib" / "win64")
    if not jars or not java.is_file() or not javac.is_file() or not all(path.is_dir() for path in native):
        return None
    version = "COMSOL 6.x"
    about = root / "about.txt"
    if about.is_file():
        try:
            match = re.search(r"COMSOL\s+(\d+\.\d+)", about.read_text(encoding="utf-8", errors="ignore"))
            if match:
                version = f"COMSOL {match.group(1)}"
        except OSError:
            pass
    return ComsolJavaEnvironment(
        found=True,
        status="ready",
        detail="Official COMSOL ModelUtil classpath, bundled JVM and native libraries detected.",
        root=str(root),
        version=version,
        api_jar=str(jars[-1]),
        java=str(java),
        javac=str(javac),
        mphserver=str(mphserver) if mphserver.is_file() else "",
        native_libraries=tuple(str(path) for path in native),
    )


comsol_java_environment = discover_comsol_java()
