"""核对许可与配套源码 / Verify notices and corresponding source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tomllib


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "frontend/src-tauri/resources/licenses"
manifest = json.loads((ROOT / "LICENSE_MANIFEST.json").read_text(encoding="utf-8"))
checked = 0
failures: list[str] = []


def visit(value: object) -> None:
    global checked
    if isinstance(value, dict):
        if all(key in value for key in ("file", "sha256", "bytes")):
            relative = Path(str(value["file"]))
            target = (ROOT / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(ROOT.resolve()):
                failures.append(f"unsafe path: {relative.name}")
            elif not target.is_file():
                failures.append(f"missing: {relative.as_posix()}")
            else:
                data = target.read_bytes()
                if len(data) != int(value["bytes"]) or hashlib.sha256(data).hexdigest().upper() != str(value["sha256"]).upper():
                    failures.append(f"hash mismatch: {relative.as_posix()}")
                checked += 1
        for nested in value.values():
            visit(nested)
    elif isinstance(value, list):
        for nested in value:
            visit(nested)


def verify_covered_sources() -> int:
    windows_lock = (REPO / "packaging/requirements-win-py312.lock").read_text(encoding="utf-8")
    python_versions = {
        (name.lower().replace("_", "-"), version)
        for name, version in re.findall(r"^([\w.-]+)==(\S+)\s+--hash=", windows_lock, re.MULTILINE)
    }
    cargo_lock = tomllib.loads((REPO / "frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    rust_versions = {(entry["name"], entry["version"]) for entry in cargo_lock["package"]}
    locked = {"python": python_versions, "rust": rust_versions}
    listing = (ROOT / "SOURCE_AVAILABILITY.md").read_text(encoding="utf-8")
    seen = set()
    archives = manifest.get("covered_source_archives", [])
    for entry in archives:
        name, version = entry["name"], entry["version"]
        identity = (entry["ecosystem"], name, version)
        if identity in seen or (name, version) not in locked.get(identity[0], ()):
            failures.append(f"duplicate or unlocked covered source: {name} {version}")
        seen.add(identity)

        relative = Path(entry["file"])
        target = (ROOT / relative).resolve()
        # 配套源码必须来自受控目录 / Limit corresponding source to its bundle directory.
        if relative.is_absolute() or not target.is_relative_to(ROOT / "covered_sources") or not target.is_file():
            failures.append(f"missing or unsafe covered source: {relative.name}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"].lower():
            failures.append(f"covered source hash mismatch: {relative.name}")
        expected = f"| {name} | {version} | {relative.as_posix()} | {entry['sha256']} |"
        if expected not in listing or entry["url"] not in listing:
            failures.append(f"covered source notice mismatch: {name} {version}")

    for package in manifest.get("python", []):
        if package["declared_license"] == "MPL-2.0":
            if ("python", package["name"], package["version"]) not in seen:
                failures.append(f"missing corresponding source: {package['name']}")
    return len(archives)


visit(manifest)
covered = verify_covered_sources()
print(json.dumps({"checked": checked, "covered_sources": covered, "failures": failures,
                  "manifest_status": manifest.get("status")}, indent=2))
raise SystemExit(1 if failures else 0)
