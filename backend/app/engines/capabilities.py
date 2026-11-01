"""Module capabilities are tied to the executable that actually handshook."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import uuid

from app.execution.files import bounded_read, reject_reparse


LEVELS = ("UNAVAILABLE", "DETECTED", "CONNECTED", "INPUT_SUPPORTED", "EXERCISED", "VALIDATED")
MODULES = {"LAMMPS": {"lammps_lj"}, "VASP": {"vasp_files"},
           "Materials Studio": {"ms_forcite_energy"}, "MATLAB": {"script"}}


def executable_hash(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CapabilityRegistry:
    def __init__(self, root: Path):
        self.root = root

    def records(self) -> dict:
        path = self.root / "connections.json"
        return json.loads(bounded_read(self.root, path.name, 65536)) if path.is_file() else {}

    def connected(self, engine: str, executable: str, version: str = "UNVERIFIED") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        reject_reparse(self.root)
        records = self.records()
        records[engine] = {"executable_sha256": executable_hash(executable),
                           "version": version or "UNVERIFIED", "connected_at": time.time()}
        self._write(records)

    def _write(self, records: dict) -> None:
        temporary = self.root / f"connections-{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(json.dumps(records).encode("utf-8"))
        temporary.replace(self.root / "connections.json")

    def exercised(self, engine: str, module: str, executable: str, run_id: str) -> None:
        records = self.records()
        record = records.get(engine, {})
        if record.get("executable_sha256") == executable_hash(executable):
            record.setdefault("modules", {})[module] = {"level": "EXERCISED", "run_id": run_id}
            self._write(records)

    def capability_of(self, engine: str, module: str, executable: str | None) -> dict:
        level = "UNAVAILABLE"
        record = {}
        if executable and Path(executable).is_file():
            level = "DETECTED"
            record = self.records().get(engine, {})
            if record and record.get("executable_sha256") == executable_hash(executable):
                level = "INPUT_SUPPORTED" if module in MODULES.get(engine, set()) else "CONNECTED"
                if module in MODULES.get(engine, set()) and record.get("modules", {}).get(module, {}).get("level") == "EXERCISED":
                    level = "EXERCISED"
        return {"engine": engine, "module": module, "level": level,
                "engine_version": record.get("version", "UNVERIFIED"),
                "scientific_validation": "UNVERIFIED"}

    def require(self, engine: str, module: str, executable: str) -> None:
        capability = self.capability_of(engine, module, executable)
        if LEVELS.index(capability["level"]) < LEVELS.index("INPUT_SUPPORTED"):
            raise ValueError(f"{engine} {module}: test the installed connector before executing this module")
