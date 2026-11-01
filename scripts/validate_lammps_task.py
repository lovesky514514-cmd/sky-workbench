"""Exercise the optional adapter against a user-installed LAMMPS executable."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.engines.manager import EngineTaskManager
from app.engines.models import EngineTaskRequest, LammpsInput


async def validate(executable: Path, output: Path, project_id: str, steps: int = 1005) -> int:
    manager = EngineTaskManager(output, lambda engine: str(executable))
    connection = await manager.connect("LAMMPS")
    if connection["status"] != "HANDSHAKE_PASSED":
        raise RuntimeError("LAMMPS handshake failed before validation")
    request = EngineTaskRequest(title="Local adapter validation · LJ NVT", project_id=project_id,
                                inputs=LammpsInput(steps=steps), confirmed=True, timeout_seconds=120)
    started = await manager.start(request)
    await manager.tasks[started["run_id"]]
    record = manager.status(started["run_id"])
    result = record.get("result") or {}
    evidence = {key: record[key] for key in ("run_id", "state", "engine", "command_success",
                "model_state_verified", "readback_available", "verification_status", "scientific_validation",
                "code_revision", "input_hashes", "engine_version", "elapsed_ms", "artifacts")}
    evidence.update(checks=result.get("checks"), thermo_rows=len(result.get("series", [])),
                    trajectory_frames=len(result.get("frames", [])), cleanup=record.get("cleanup"),
                    requested_steps=steps, final_step=(result.get("series") or [[None]])[-1][0],
                    validation_records=result.get("validation_records"))
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    (output / "validation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if (record["state"] == "COLLECTED" and record["verification_status"] == "EXERCISED"
                 and all(result.get("checks", {}).values()) and record.get("cleanup", {}).get("reaped")) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-id", default="adapter-validation")
    parser.add_argument("--steps", type=int, default=1005)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(validate(args.executable.resolve(), args.output.resolve(), args.project_id, args.steps)))
