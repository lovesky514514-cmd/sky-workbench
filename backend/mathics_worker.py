"""Run a reviewed local expression in a separately installed Mathics interpreter."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import re


SAFE_HEADS = set(
    "Plus Times Power Rational Complex List N Equal Unequal Less Greater LessEqual GreaterEqual "
    "And Or Not Rule D Integrate NIntegrate Sum Product Limit Series Normal Simplify FullSimplify Expand Factor "
    "Together Apart Solve NSolve DSolve NDSolve Abs Sin Cos Tan ArcSin ArcCos ArcTan Sinh Cosh Tanh Exp Log Sqrt "
    "Re Im Conjugate Min Max Floor Ceiling Round Mod Sign Piecewise If Function Slot Plot Plot3D ParametricPlot "
    "ParametricPlot3D ListPlot ListLinePlot Range Table Derivative Subscript".split()
)
SAFE_SYMBOLS = set(
    "Pi E I Infinity ComplexInfinity Indeterminate True False Automatic All None Reals Integers "
    "PlotRange AxesLabel PlotLabel WorkingPrecision AccuracyGoal PrecisionGoal".split()
)


def validate_formula(expression) -> None:
    """Allow mathematical AST nodes, not assignments, I/O or host commands."""
    pending = [(expression, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 96:
            raise ValueError("Formula syntax exceeds its structural limit")
        elements = getattr(node, "elements", None)
        if elements is not None:
            head = node.get_head_name()
            if not head.startswith("System`") or head.split("`")[-1] not in SAFE_HEADS:
                raise ValueError(
                    "Formula operation is outside the controlled mathematical vocabulary"
                )
            pending.extend((child, depth + 1) for child in elements)
        elif type(node).__name__ == "Symbol":
            name = node.get_name()
            if name.startswith("System`") and name.split("`")[-1] in SAFE_SYMBOLS:
                continue
            if not re.fullmatch(r"Global`[^\W\d_]\w*", name):
                raise ValueError(
                    "Formula symbol is not an ordinary mathematical variable"
                )
        elif type(node).__name__ not in {
            "Integer",
            "Real",
            "MachineReal",
            "PrecisionReal",
            "Rational",
            "Complex",
        }:
            raise ValueError("Formula literals must be numeric")


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
        session = MathicsSession()
        parsed = session.parse(source)
        validate_formula(parsed)
        result = session.evaluate_as_in_cli(
            source, form={"text": "text", "latex": "latex", "mathml": "xml"}
        )
        value = result.result if isinstance(result.result, dict) else {}
        evaluated = result.last_eval
        messages = [str(item) for item in result.out]
        payload = {
            "status": "failed" if result.result is None and messages else "success",
            "text": str(value.get("text") or ""),
            "latex": str(value.get("latex") or ""),
            "mathml": str(value.get("mathml") or ""),
            "messages": messages,
            "verification": "not_requested",
            "evaluation_kind": "numeric"
            if type(evaluated).__name__
            in {
                "Integer",
                "Real",
                "MachineReal",
                "PrecisionReal",
                "Rational",
                "Complex",
            }
            else "symbolic_or_unevaluated",
            "execution_policy": "controlled_formula_ast",
        }
    except Exception as exc:
        # Do not send tracebacks or host paths to the UI. The exception type
        # distinguishes a worker failure from a valid mathematical result.
        payload = {
            "status": "failed",
            "text": "",
            "latex": "",
            "mathml": "",
            "messages": [type(exc).__name__],
            "verification": "not_requested",
        }

    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 65_536:
        return 3
    temporary = workspace / "mathics_result.json.tmp"
    temporary.write_bytes(encoded)
    os.replace(temporary, workspace / "mathics_result.json")
    return 0 if payload["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
