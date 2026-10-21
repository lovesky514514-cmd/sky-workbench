"""Native diagnostics must remain a bounded CLI check, never a scientific API bypass."""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.diagnostics.native_selftest import probe_native, run
import desktop_runtime


class NativeSelfTestContract(unittest.TestCase):
    def test_cli_returns_before_runtime_environment_and_server_start(self):
        with patch.object(sys, "argv", ["runtime", "--native-self-test-report", "probe.json"]), \
             patch("app.diagnostics.native_selftest.run", return_value=0) as check, \
             patch.object(desktop_runtime, "_apply_environment", side_effect=AssertionError("must not configure server")), \
             patch.object(desktop_runtime, "_configure_logging", side_effect=AssertionError("must not open runtime logs")):
            self.assertEqual(desktop_runtime.main(), 0)
            check.assert_called_once_with(Path("probe.json"))

    def test_cannot_mix_diagnostic_with_scientific_worker(self):
        with patch.object(sys, "argv", ["runtime", "--worker", "heat_worker", "--native-self-test-report", "probe.json"]):
            with self.assertRaises(SystemExit) as result:
                desktop_runtime.main()
        self.assertEqual(result.exception.code, 2)

    def test_cannot_mix_diagnostic_with_desktop_server(self):
        with patch.object(sys, "argv", ["runtime", "--desktop", "--native-self-test-report", "probe.json"]):
            with self.assertRaises(SystemExit) as result:
                desktop_runtime.main()
        self.assertEqual(result.exception.code, 2)

    def test_import_error_does_not_emit_raw_exception_or_private_path(self):
        original = importlib.import_module
        def controlled(name):
            if name == "_jpype":
                raise ImportError("synthetic-sensitive-value-do-not-emit")
            return original(name)
        with patch("app.diagnostics.native_selftest.importlib.import_module", side_effect=controlled):
            report = probe_native()
        encoded = json.dumps(report)
        self.assertNotIn("synthetic-sensitive-value-do-not-emit", encoded)
        self.assertEqual(report["modules"]["_jpype"]["error_class"], "ImportError")
        self.assertFalse(report["passed"])

    def test_diagnostic_never_overwrites_evidence(self):
        isolated = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=isolated) as temporary:
            path = Path(temporary) / "existing.json"
            path.write_text("preserved", encoding="utf-8")
            self.assertEqual(run(path), 2)
            self.assertEqual(path.read_text("utf-8"), "preserved")


if __name__ == "__main__":
    unittest.main()
