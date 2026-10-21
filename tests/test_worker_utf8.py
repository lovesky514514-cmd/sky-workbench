"""Reproduce the Windows ANSI-codepage failure without launching COMSOL."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import desktop_runtime  # noqa: E402
from app.comsol import heat_worker  # noqa: E402


class WorkerEncodingTest(unittest.TestCase):
    def test_frozen_worker_json_protocol_is_utf8_even_on_gbk_host(self) -> None:
        # m³ cannot be encoded by the Windows GBK stdout observed in the
        # original failed scientific RUN. The worker must emit valid UTF-8.
        sink = io.BytesIO()
        stdout = io.TextIOWrapper(sink, encoding="gbk", write_through=True)

        def emit_result(_request: Path) -> int:
            heat_worker.result({"unit": "m³", "success": True})
            return 0

        with patch.object(sys, "stdout", stdout), patch.object(heat_worker, "main", emit_result):
            self.assertEqual(desktop_runtime._run_worker("heat_worker", Path("synthetic")), 0)
            stdout.flush()
        payload = sink.getvalue().decode("utf-8")
        self.assertIn('"unit": "m³"', payload)
        self.assertIn("WORKBENCH_RESULT:", payload)


if __name__ == "__main__":
    unittest.main()
