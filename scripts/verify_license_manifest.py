"""Verify that every hash-pinned notice is present with its original bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "frontend/src-tauri/resources/licenses"
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


visit(manifest)
print(json.dumps({"checked": checked, "failures": failures, "manifest_status": manifest.get("status")}, indent=2))
raise SystemExit(1 if failures else 0)
