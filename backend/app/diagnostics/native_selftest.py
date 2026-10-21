"""Import fixed native modules and packaged timezone data without starting COMSOL."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path


NATIVE_MODULES = (
    "_ctypes", "_jpype", "_ssl", "_sqlite3", "_bz2", "numpy._core._multiarray_umath",
    "pydantic_core._pydantic_core", "cryptography.hazmat.bindings._rust",
)


def probe_native() -> dict:
    """Only this fixed import list is executable; no arbitrary module/command input."""
    frozen = bool(getattr(sys, "frozen", False))
    bundle = Path(getattr(sys, "_MEIPASS", ".")).resolve()
    modules = {}
    for name in NATIVE_MODULES:
        try:
            module = importlib.import_module(name)
            source = Path(module.__file__).resolve()
            modules[name] = {
                "import_passed": True, "file": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest().upper(),
                "inside_frozen_bundle": frozen and source.is_relative_to(bundle),
            }
            if name == "_jpype":
                modules[name]["jvm_started"] = bool(module.isStarted())
        except Exception as exc:
            # Never include loader error text: it may contain user directories or environment data.
            modules[name] = {"import_passed": False, "error_class": type(exc).__name__}
    # Import alone does not exercise libffi's call/closure ABI. Keep this
    # deterministic and independent of any COMSOL or user-supplied library.
    try:
        import ctypes
        callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda value: value + 1)
        ctypes_callback_passed = callback(41) == 42
    except Exception:
        ctypes_callback_passed = False
    zones = {}
    try:
        import zoneinfo
        previous_path = tuple(zoneinfo.TZPATH)
        try:
            # Force the shipped tzdata fallback instead of relying on the host's timezone database.
            zoneinfo.reset_tzpath(())
            for key, month, expected in (("Asia/Shanghai", 1, 480), ("America/New_York", 1, -300),
                                         ("America/New_York", 7, -240), ("UTC", 1, 0)):
                zone = zoneinfo.ZoneInfo.no_cache(key)
                minutes = int(datetime(2026, month, 15, 12, tzinfo=zone).utcoffset().total_seconds() / 60)
                zones[f"{key}:{month}"] = {"offset_minutes": minutes, "expected_minutes": expected,
                                           "passed": minutes == expected}
        finally:
            zoneinfo.reset_tzpath(previous_path)
        timezone_version = metadata.version("tzdata")
    except Exception as exc:
        zones["error"] = {"passed": False, "error_class": type(exc).__name__}
        timezone_version = None
    native_versions = {"openssl": getattr(sys.modules.get("_ssl"), "OPENSSL_VERSION", None),
                       "sqlite": getattr(sys.modules.get("_sqlite3"), "sqlite_version", None)}
    try:
        from cryptography.hazmat.bindings.openssl.binding import Binding
        binding = Binding()
        native_versions["cryptography_openssl"] = binding.ffi.string(binding.lib.OpenSSL_version(0)).decode("ascii")
    except Exception as exc:
        native_versions["cryptography_openssl_error_class"] = type(exc).__name__
    # HTTPX creates an SSL context even for the local Java Bridge client. A
    # missing frozen CA bundle must fail diagnostics before model attachment.
    certifi_check = {"ca_file_exists": False, "httpx_client_constructed": False}
    try:
        import certifi
        ca_file = Path(certifi.where()).resolve()
        certifi_check.update({
            "ca_file_exists": ca_file.is_file(),
            "inside_frozen_bundle": frozen and ca_file.is_relative_to(bundle),
            "bundle_candidate_exists": (bundle / "certifi" / "cacert.pem").is_file(),
            "ca_file_sha256": hashlib.sha256(ca_file.read_bytes()).hexdigest().upper() if ca_file.is_file() else None,
        })
        import httpx
        with httpx.Client(trust_env=False):
            pass
        certifi_check["httpx_client_constructed"] = True
    except Exception as exc:
        certifi_check["error_class"] = type(exc).__name__
    app_loaded = "app.main" in sys.modules
    scientific_modules = [name for name in sys.modules if name.startswith("app.comsol.") and name.endswith("_worker")]
    passed = (ctypes_callback_passed
              and all(row.get("import_passed") for row in modules.values())
              and all(row.get("passed") for row in zones.values())
              and modules.get("_jpype", {}).get("jvm_started") is False
              and all(native_versions.get(key) for key in ("openssl", "sqlite", "cryptography_openssl"))
              and certifi_check.get("ca_file_exists") and certifi_check.get("httpx_client_constructed")
              and (not frozen or certifi_check.get("inside_frozen_bundle"))
              and not app_loaded and not scientific_modules
              and (not frozen or all(row.get("inside_frozen_bundle") for row in modules.values())))
    return {"schema": "swb-native-selftest-v1", "frozen": frozen,
            "execution_kind": "frozen" if frozen else "source", "modules": modules,
            "ctypes_callback_passed": ctypes_callback_passed,
            "native_versions": native_versions,
            "certifi_check": certifi_check,
            "timezone_checks": zones, "tzdata_version": timezone_version,
            "application_server_imported": app_loaded, "scientific_worker_import_count": len(scientific_modules),
            "scientific_execution_performed": False, "passed": passed}


def run(output: Path) -> int:
    """Write one new diagnostic report; never replace existing evidence or start the API."""
    if output.exists() or not output.parent.is_dir():
        return 2
    for parent in (output.parent, *output.parent.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            return 2
    report = probe_native()
    try:
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, indent=2)
            destination.write("\n")
    except OSError:
        return 2
    return 0 if report["passed"] else 1
