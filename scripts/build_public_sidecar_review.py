"""Build an internal-only sidecar from this source without inherited secrets.

This is not an installer or release approval. The output directory must be new.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EPOCH = "1792368000"


def source_digest() -> tuple[str, int]:
    """Bind the review build to the exact source, frontend and license bytes."""
    roots = (
        ROOT / "backend" / "app",
        ROOT / "backend" / "desktop_runtime.py",
        ROOT / "frontend" / "dist",
        ROOT / "java_bridge" / "src",
        ROOT / "vnext" / "3d" / "minimal_3d_contracts.json",
        ROOT / "frontend" / "src-tauri" / "resources" / "licenses",
        ROOT / "packaging" / "scientific-runtime.spec",
        ROOT / "packaging" / "vendor" / "python-3.12.14" / "ssl.py",
        ROOT / "packaging" / "vendor" / "python-3.12.14" / "LICENSE",
        ROOT.parent / "libffi-3.4.4-source.tar.gz",
        ROOT.parent / "libffi-build" / ".libs" / "libffi-8.dll",
        ROOT.parent / "openssl-3.5.9.tar.gz",
        ROOT.parent / "openssl-sanitized-build" / "openssl-3.5.9" / "libcrypto-3-x64.dll",
        ROOT.parent / "openssl-sanitized-build" / "openssl-3.5.9" / "libssl-3-x64.dll",
    )
    files = sorted(
        (path for entry in roots for path in (entry.rglob("*") if entry.is_dir() else (entry,))
         if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"),
        key=lambda path: (path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT)
                          else "external/" + path.name).casefold(),
    )
    digest = hashlib.sha256()
    for path in files:
        relative = (path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT)
                    else "external/" + path.name).encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest().upper(), len(files)


def child_environment(output: Path, python: Path) -> dict[str, str]:
    """Pass only named OS/build settings; never inherit API keys or signing data."""
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return {
        "SystemRoot": str(windows), "WINDIR": str(windows),
        "COMSPEC": str(windows / "System32" / "cmd.exe"),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD", "PROCESSOR_ARCHITECTURE": "AMD64",
        "PATH": os.pathsep.join((str(python.parent), str(windows / "System32"),
                                 str(windows), str(windows / "System32" / "Wbem"))),
        "USERPROFILE": str(output / "home"), "HOME": str(output / "home"),
        "APPDATA": str(output / "home" / "Roaming"),
        "LOCALAPPDATA": str(output / "home" / "Local"),
        "TEMP": str(output / "temp"), "TMP": str(output / "temp"),
        "PYINSTALLER_CONFIG_DIR": str(output / "pyinstaller-config"),
        "SOURCE_DATE_EPOCH": EPOCH, "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8", "WORKBENCH_BUILD_PROFILE": "public",
    }


def sanitized_log(value: str) -> str:
    """Keep build diagnostics useful without retaining local identity or secrets."""
    value = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s]+", "<USER_HOME>", value)
    value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<EMAIL>", value)
    return re.sub(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}", "<SECRET>", value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    python, output = args.python.resolve(), args.out.resolve()
    if not python.is_file() or output.exists() or not output.is_relative_to(ROOT):
        raise SystemExit("Python missing, output exists, or output escapes the review workspace")
    for parent in (output, *output.parents):
        if parent == ROOT.parent:
            break
        if parent.exists() and (parent.is_symlink() or parent.is_junction()):
            raise SystemExit("Refusing a reparse-point output parent")
    bridge = ROOT / "java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java"
    bundled_bridge = ROOT / "frontend/src-tauri/resources/java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java"
    if bridge.read_bytes() != bundled_bridge.read_bytes():
        raise SystemExit("Java Bridge source copies differ")
    before, count = source_digest()
    for relative in ("dist", "work", "home/Roaming", "home/Local", "temp", "pyinstaller-config"):
        (output / relative).mkdir(parents=True, exist_ok=False)
    command = [str(python), "-m", "PyInstaller", "--noconfirm", "--distpath",
               str(output / "dist"), "--workpath", str(output / "work"),
               str(ROOT / "packaging/scientific-runtime.spec")]
    started = time.monotonic()
    run = subprocess.run(command, cwd=ROOT, env=child_environment(output, python),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", check=False)
    (output / "build.redacted.log").write_text(sanitized_log(run.stdout), encoding="utf-8")
    after, _ = source_digest()
    exe = output / "dist/scientific-runtime.exe"
    result = {
        "status": "INTERNAL_REVIEW_ONLY", "exit_code": run.returncode,
        "input_sha256_before": before, "input_sha256_after": after, "input_files": count,
        "source_date_epoch": EPOCH, "pythonhashseed": "0",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "exe_bytes": exe.stat().st_size if exe.is_file() else None,
        "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest().upper() if exe.is_file() else None,
    }
    (output / "build_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if run.returncode == 0 and exe.is_file() and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
