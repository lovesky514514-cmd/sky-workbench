"""Check the retained Mathics3 connector in an independent interpreter."""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.script_workspace import ScriptRunManager
from app.source_revision import source_revision


async def validate(interpreter: Path, output: Path) -> int:
    manager = ScriptRunManager(output / "runs", lambda engine: str(interpreter))
    expression = "Simplify[D[Integrate[x^2, x], x] - x^2]"
    record = await manager.start("Mathics3", expression)
    run = manager.runs[str(record["run_id"])]
    await run.task
    evidence = run.public()
    output.mkdir(parents=True, exist_ok=True)
    (output / "identity.wl").write_bytes(expression.encode("utf-8"))
    evidence.update(code_revision=source_revision(), input_sha256=hashlib.sha256(expression.encode("utf-8")).hexdigest(),
                    check="D[Integrate[x^2,x],x]-x^2 == 0", expected="0",
                    validation_scope="This exact symbolic identity only; no general scientific certification")
    (output / "validation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    result = evidence.get("mathics_result") or {}
    passed = evidence["state"] == "COMPLETED" and str(result.get("text", "")).strip() == "0"
    print(json.dumps({"state": evidence["state"], "result": result.get("text"), "passed": passed}, ensure_ascii=False))
    await manager.shutdown()
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(validate(args.python.resolve(), args.output.resolve())))
