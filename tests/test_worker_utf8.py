"""Reproduce the Windows ANSI-codepage failure without launching COMSOL."""

from __future__ import annotations

import io
import json
import sys
from tempfile import TemporaryDirectory
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
            heat_worker.emit('Build', '单位 m³')
            heat_worker.result({"unit": "m³", "success": True})
            return 0

        with TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.json'
            request.write_text('{"run_id":"test-run","attempt_id":"test-attempt"}', encoding='utf-8')
            (root / 'data').mkdir()
            with patch.object(sys, "stdout", stdout), patch.object(heat_worker, "main", emit_result):
                self.assertEqual(desktop_runtime._run_worker("heat_worker", request), 0)
                stdout.flush()
            payload = sink.getvalue().decode("utf-8")
            self.assertIn('单位 m³', payload)
            self.assertIn("WORKBENCH_RESULT:", payload)
            self.assertNotIn('"unit"', payload)
            data = json.loads((root / 'data' / 'result.json').read_bytes())
            self.assertEqual(data['unit'], 'm³')


if __name__ == "__main__":
    unittest.main()
