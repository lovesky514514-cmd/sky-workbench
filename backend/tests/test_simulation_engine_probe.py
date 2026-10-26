"""Optional engines may prove a local handshake, never scientific execution."""

import asyncio
from pathlib import Path

import pytest

from app import simulation_adapters as adapters


def test_probe_command_is_fixed_and_local():
    matlab = adapters.probe_command("MATLAB", "C:/MATLAB/bin/matlab.exe")
    wolfram = adapters.probe_command("Wolfram", "C:/Wolfram/wolframscript.exe")
    assert matlab == ["C:/MATLAB/bin/matlab.exe", "-batch", "disp('SWB_ENGINE_PROBE_OK')"]
    assert wolfram == ["C:/Wolfram/wolframscript.exe", "-local", "-code", 'Print["SWB_ENGINE_PROBE_OK"]']
    with pytest.raises(ValueError):
        adapters.probe_command("Materials Studio", "C:/fake/MaterialsStudio.exe")


def test_probe_output_is_classified_without_disclosing_vendor_text():
    assert adapters.classify_handshake(0, b"SWB_ENGINE_PROBE_OK\r\n") == "HANDSHAKE_PASSED"
    assert adapters.classify_handshake(0, b"not the marker") == "HANDSHAKE_FAILED"
    assert adapters.classify_handshake(1, b"License activation is required for private-user@example.test") == "LICENSE_OR_ACTIVATION_REQUIRED"
    assert adapters.classify_handshake(1, b"Failure at C:/Users/private/work") == "HANDSHAKE_FAILED"


def test_discovery_does_not_claim_execution(monkeypatch):
    monkeypatch.setattr(adapters, "discover_comsol_java", lambda: type("E", (), {
        "found": False, "java": "", "compatibility": "unverified",
    })())
    monkeypatch.setattr(adapters.shutil, "which", lambda name: str(Path("C:/fake") / name))
    records = {item["engine"]: item for item in adapters.capabilities()}
    for engine in ("MATLAB", "Wolfram", "Materials Studio"):
        assert records[engine]["execute"] == "UNAVAILABLE"
        assert records[engine]["readback"] == "UNVERIFIED"
    assert records["MATLAB"]["probe"] == "AVAILABLE_ON_REQUEST"
    assert records["Wolfram"]["probe"] == "AVAILABLE_ON_REQUEST"
    assert records["Materials Studio"]["probe"] == "UNAVAILABLE"


def test_handshake_never_certifies_science(monkeypatch):
    monkeypatch.setattr(adapters, "capabilities", lambda: [{
        "engine": "Wolfram", "installed": True, "executable": "C:/fake/wolframscript.exe",
        "readback": "UNVERIFIED", "execute": "UNAVAILABLE", "probe": "AVAILABLE_ON_REQUEST",
    }])

    async def fake_handshake(command):
        assert command[1:] == ["-local", "-code", 'Print["SWB_ENGINE_PROBE_OK"]']
        return "HANDSHAKE_PASSED", 17

    monkeypatch.setattr(adapters, "_handshake", fake_handshake)
    result = asyncio.run(adapters.probe_engine("wolfram"))
    assert result["status"] == "HANDSHAKE_PASSED"
    assert result["readback"] == "UNVERIFIED"
    assert result["execute"] == "UNAVAILABLE"
    assert result["elapsed_ms"] == 17


def test_unknown_or_unavailable_engine_never_launches(monkeypatch):
    monkeypatch.setattr(adapters, "capabilities", lambda: [{
        "engine": "MATLAB", "installed": False, "executable": "", "readback": "UNVERIFIED",
        "execute": "UNAVAILABLE", "probe": "UNAVAILABLE",
    }])

    async def never_launch(command):
        raise AssertionError("must not launch")

    monkeypatch.setattr(adapters, "_handshake", never_launch)
    assert asyncio.run(adapters.probe_engine("matlab"))["status"] == "UNAVAILABLE"
    with pytest.raises(ValueError):
        asyncio.run(adapters.probe_engine("not-a-simulator"))
