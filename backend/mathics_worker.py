"""Run a reviewed local expression in a separately installed Mathics interpreter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    # The desktop Runtime supplies a fixed filename in its private scratch area.
    # This connector is first-party code; it does not ship Mathics or import it
    # into the scientific Runtime process.
    if len(sys.argv) != 2 or sys.argv[1] != "main.wl":
        return 2
    workspace = Path.cwd().resolve()
    try:
        from mathics.session import MathicsSession

        source = (workspace / "main.wl").read_text(encoding="utf-8")
        result = MathicsSession().evaluate_as_in_cli(
            source, form={"text": "text", "latex": "latex", "mathml": "xml"}
        )
        value = result.result if isinstance(result.result, dict) else {}
        messages = [str(item) for item in result.out]
        payload = {
            "status": "failed" if result.result is None and messages else "success",
            "text": str(value.get("text") or ""),
            "latex": str(value.get("latex") or ""),
            "mathml": str(value.get("mathml") or ""),
            "messages": messages,
            "verification": "not_requested",
        }
    except Exception as exc:
        # Do not send tracebacks or host paths to the UI. The exception type
        # distinguishes a worker failure from a valid mathematical result.
        payload = {"status": "failed", "text": "", "latex": "", "mathml": "", "messages": [type(exc).__name__],
                   "verification": "not_requested"}

    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 65_536:
        return 3
    temporary = workspace / "mathics_result.json.tmp"
    temporary.write_bytes(encoded)
    os.replace(temporary, workspace / "mathics_result.json")
    return 0 if payload["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
