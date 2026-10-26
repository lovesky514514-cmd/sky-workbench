"""Read-only detection and progress contracts for optional desktop software."""

import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from app.comsol.discovery import ComsolJavaEnvironment, scan_comsol_java
from app.environment import InstallationScan
from app.services.model_manager import LocalModelManager, ollama_executable, pull_percent
from app.providers.manager import ProviderManager
from app.simulation_adapters import capabilities as adapter_capabilities


class DiscoveryTests(unittest.TestCase):
    def test_optional_engines_report_only_measured_presence(self):
        observed = []
        comsol = ComsolJavaEnvironment(True, "ready", "test", version="COMSOL 6.4")
        with patch("app.simulation_adapters._find_executable", side_effect=["", "wolframscript.exe", ""]):
            records = adapter_capabilities(comsol, lambda engine, state: observed.append((engine, state)))
        self.assertEqual([(item["engine"], item["installed"]) for item in records],
                         [("COMSOL", True), ("MATLAB", False), ("Wolfram", True), ("Materials Studio", False)])
        self.assertEqual(observed, [("MATLAB", "running"), ("MATLAB", "unavailable"),
                                    ("Wolfram", "running"), ("Wolfram", "detected"),
                                    ("Materials Studio", "running"), ("Materials Studio", "unavailable")])
        self.assertTrue(all(item["execute"] != "VERIFIED" for item in records))
    def test_scan_reports_inspected_candidates_not_invented_disk_percent(self):
        progress = []
        found = ComsolJavaEnvironment(True, "ready", "test installation", root="test")
        with patch("app.comsol.discovery._candidate_roots", return_value=[Path("one"), Path("two")]), patch(
            "app.comsol.discovery._inspect_root", side_effect=[None, found]
        ):
            result = scan_comsol_java(lambda checked, total: progress.append((checked, total)))
        self.assertEqual(progress, [(1, 2), (2, 2)])
        self.assertTrue(result.found)

    def test_missing_ollama_executable_is_explicit(self):
        with patch("app.services.model_manager.shutil.which", return_value=None), patch(
            "app.services.model_manager.os.name", "posix"
        ):
            self.assertIsNone(ollama_executable())

    def test_download_percent_requires_actual_service_byte_counts(self):
        self.assertEqual(pull_percent({"completed": 42, "total": 100}), 42)
        self.assertIsNone(pull_percent({"status": "pulling"}))
        self.assertEqual(pull_percent({"completed": 110, "total": 100}), 100)

    def test_scan_reports_each_candidate_before_and_after_inspection(self):
        observations = []
        with patch("app.comsol.discovery._candidate_roots", return_value=[Path("one"), Path("two")]), patch(
            "app.comsol.discovery._inspect_root", return_value=None
        ):
            scan_comsol_java(
                lambda checked, total: observations.append(("done", checked, total)),
                lambda current, total: observations.append(("checking", current, total)),
            )
        self.assertEqual(observations, [("checking", 1, 2), ("done", 1, 2), ("checking", 2, 2), ("done", 2, 2)])


class InstallationScanTests(unittest.TestCase):
    def test_steps_follow_real_scan_and_do_not_claim_bridge_verified(self):
        scan = InstallationScan()
        scan._state = scan._initial_state("running")
        detected = ComsolJavaEnvironment(True, "ready", "test", root="test")

        def inspect(progress, before_candidate):
            before_candidate(1, 2)
            self.assertEqual(scan.status()["steps"][0]["state"], "running")
            self.assertEqual(scan.status()["steps"][1]["state"], "pending")
            progress(1, 2)
            before_candidate(2, 2)
            progress(2, 2)
            return detected

        async def direct(callback, *args):
            return callback(*args)

        async def service_status():
            return {"state": "Not Installed"}

        def adapters(comsol, progress):
            for engine in ("MATLAB", "Wolfram", "Materials Studio"):
                progress(engine, "running")
                progress(engine, "unavailable")
            return [{"engine": "COMSOL", "installed": True, "execute": "UNVERIFIED"}]

        with patch("app.environment.scan_comsol_java", side_effect=inspect), patch(
            "app.environment.ollama_executable", return_value=None
        ), patch("app.environment.adapter_capabilities", side_effect=adapters
        ), patch("app.environment.asyncio.to_thread", side_effect=direct), patch(
            "app.environment.local_model_manager.status", side_effect=service_status
        ):
            # Every mocked await completes synchronously, so the coroutine can
            # be driven without opening an asyncio loopback socket in CI.
            with self.assertRaises(StopIteration):
                scan._run().send(None)
        result = scan.status()
        self.assertEqual(result["state"], "completed")
        self.assertEqual((result["checked"], result["total"]), (2, 2))
        self.assertEqual([step["id"] for step in result["steps"]], ["comsol", "matlab", "wolfram", "materials_studio", "ollama_executable", "ollama_service"])
        self.assertEqual([step["state"] for step in result["steps"]], ["detected", "unavailable", "unavailable", "unavailable", "unavailable", "unavailable"])
        self.assertEqual(result["adapters"][0]["engine"], "COMSOL")
        self.assertNotIn("executable", result["adapters"][0])
        self.assertNotIn("verified", str(result["steps"]).lower())
        self.assertNotIn("scientific_validation_passed", result)

    def test_failed_candidate_check_never_leaves_running_or_claims_completion(self):
        scan = InstallationScan()
        scan._state = scan._initial_state("running")

        async def direct(callback, *args):
            return callback(*args)

        with patch("app.environment.scan_comsol_java", side_effect=OSError("private path")), patch(
            "app.environment.asyncio.to_thread", side_effect=direct
        ):
            with self.assertRaises(StopIteration):
                scan._run().send(None)
        result = scan.status()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["steps"][0]["state"], "failed")
        self.assertNotIn("private path", str(result))

    def test_ollama_service_failure_keeps_completed_installation_evidence(self):
        scan = InstallationScan()
        scan._state = scan._initial_state("running")
        found = ComsolJavaEnvironment(True, "ready", "detected", version="COMSOL 6.4")

        async def direct(callback, *args):
            return callback(*args)

        def adapters(comsol, progress):
            for engine in ("MATLAB", "Wolfram", "Materials Studio"):
                progress(engine, "running")
                progress(engine, "unavailable")
            return [{"engine": "COMSOL", "installed": True, "execute": "UNVERIFIED", "executable": "private path"}]

        async def broken_service():
            raise FileNotFoundError("private path")

        with patch("app.environment.scan_comsol_java", return_value=found), patch(
            "app.environment.adapter_capabilities", side_effect=adapters
        ), patch("app.environment.ollama_executable", return_value=None), patch(
            "app.environment.asyncio.to_thread", side_effect=direct
        ), patch("app.environment.local_model_manager.status", side_effect=broken_service):
            with self.assertRaises(StopIteration):
                scan._run().send(None)
        result = scan.status()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["comsol"]["version"], "COMSOL 6.4")
        self.assertEqual(result["steps"][-1]["state"], "failed")
        self.assertNotIn("private path", str(result["adapters"]))
        self.assertNotIn("private path", str(result.get("partial_failure")))

    def test_missing_certificate_override_does_not_crash_local_provider_detection(self):
        manager = ProviderManager()
        with patch.object(manager, "_client", side_effect=FileNotFoundError("missing CA bundle")), patch(
            "app.providers.manager.ollama_executable", return_value=None
        ):
            with self.assertRaises(StopIteration) as completed:
                manager._detect_one("ollama", {"enabled": True, "base_url": "http://127.0.0.1:11434", "model": "qwen3:8b"}).send(None)
        self.assertEqual(completed.exception.value.status, "Not Installed")

    def test_missing_certificate_override_does_not_crash_model_status(self):
        model_manager = LocalModelManager()
        with patch("app.services.model_manager.httpx.AsyncClient", side_effect=FileNotFoundError("missing CA bundle")), patch(
            "app.services.model_manager.ollama_executable", return_value=None
        ), patch("app.services.model_manager._gpu", return_value={"name": "Not detected", "vram_bytes": 0}), patch(
            "app.services.model_manager._ram_bytes", return_value=0
        ), patch("app.services.model_manager.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaises(StopIteration) as completed:
                model_manager.status().send(None)
        self.assertEqual(completed.exception.value["state"], "Not Installed")


if __name__ == "__main__":
    unittest.main()
