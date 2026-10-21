"""Readback-only COMSOL 6.3 Bridge authentication acceptance in an isolated workspace.

This script does not solve, export, modify the model, or serialize credentials.
It exercises the modified Python and Java source, not the frozen A/B sidecars.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "RC52_JAVA_BRIDGE_AUTH_SOURCE_TEST_20261019_FINAL"
FIXTURE = ROOT / (
    "packaging/real_rc51_final_52d83/workspace/models/attempts/"
    "174045c7-c31f-4e6b-ad96-7b8044edcf11/"
    "fd755c32-f4ee-4d38-a59f-e21eb7b3015f/models/"
    "heat_transfer_2d_20261017_114257_174045c7.mph"
)
FIXTURE_SHA = "E931DFB8574A5D54F08269DCC28A51C8A70B1545FA85CF5D02203E900F3580E2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def process_snapshot(pid: int) -> list[dict[str, object]]:
    """Query only this owned Java PID and its direct children; never terminate by name."""
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}'; "
        f"$c=Get-CimInstance Win32_Process -Filter 'ParentProcessId = {int(pid)}'; "
        "@($p)+@($c) | Where-Object { $_ } | Select-Object ProcessId,CreationDate,CommandLine "
        "| ConvertTo-Json -Compress"
    )
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                               capture_output=True, text=True, timeout=15, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    if not completed.stdout.strip():
        return []
    parsed = json.loads(completed.stdout)
    return parsed if isinstance(parsed, list) else [parsed]


def direct(port: int, method: str, path: str, *, bridge_token: str | None = None,
           body: dict[str, str] | None = None, duplicate: bool = False) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        connection.putrequest(method, path)
        if bridge_token is not None:
            connection.putheader("X-Workbench-Bridge-Token", bridge_token)
            if duplicate:
                connection.putheader("X-Workbench-Bridge-Token", bridge_token)
        if payload is not None:
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(payload)))
        connection.endheaders(payload)
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            parsed = {}
        return response.status, parsed if isinstance(parsed, dict) else {}
    finally:
        connection.close()


async def run() -> int:
    if EVIDENCE.exists():
        raise RuntimeError("Refusing to overwrite prior authentication test evidence")
    if sha256(FIXTURE) != FIXTURE_SHA:
        raise RuntimeError("Safe model fixture identity mismatch")
    EVIDENCE.mkdir()
    for name in ("appdata", "workspace/models"):
        (EVIDENCE / name).mkdir(parents=True)
    model = EVIDENCE / "workspace/models/isolated_heat_fixture.mph"
    shutil.copy2(FIXTURE, model)
    if sha256(model) != FIXTURE_SHA:
        raise RuntimeError("Isolated model copy mismatch")

    # All application paths are fixed before importing any Runtime singleton.
    os.environ["COMSOL_WORKBENCH_DESKTOP_MODE"] = "1"
    os.environ["COMSOL_WORKBENCH_APP_DATA_DIR"] = str(EVIDENCE / "appdata")
    os.environ["COMSOL_WORKBENCH_WORKSPACE_DIR"] = str(EVIDENCE / "workspace")
    os.environ["COMSOL_WORKBENCH_JAVA_BRIDGE_DIR"] = str(ROOT / "frontend/src-tauri/resources/java_bridge")
    os.environ["COMSOL_WORKBENCH_SESSION_TOKEN"] = "isolated-outer-api-test-token"
    os.environ.pop("COMSOL_WORKBENCH_JAVA_BRIDGE_TOKEN", None)
    sys.path.insert(0, str(ROOT / "backend"))

    import httpx
    from app.comsol.bridge_process import java_bridge_process_manager as manager
    from app.comsol.java_bridge import java_bridge_client as client
    from app.main import app
    from app.session.store import session_store
    from app.config import ensure_workspace

    ensure_workspace()
    session_store.initialize()

    result: dict[str, object] = {
        "scope": "modified source; isolated COMSOL model readback only",
        "fixture_sha256": FIXTURE_SHA,
        "bridge_state": None,
        "comsol_version": None,
        "direct_negative_cases": 0,
        "direct_negative_rejected": 0,
        "direct_correct_health_http": None,
        "authorized_model_tree": False,
        "model_unchanged": False,
        "unauthorized_study_event_count": None,
        "outer_unauthorized_stop_http": None,
        "owned_processes_left": None,
        "token_in_command_line": None,
        "token_in_parent_environment": None,
        "token_in_owned_logs": None,
        "token_in_status_or_events": None,
        "token_cleared_after_stop": None,
        "error_class": None,
    }
    pid = None
    identities: list[tuple[int, object]] = []
    bridge_token = None
    try:
        await manager.start(str(model))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = manager.status()
            if state["state"] in {"ready", "failed", "crashed"}:
                break
            await asyncio.sleep(0.4)
        result["bridge_state"] = state["state"]
        if state["state"] != "ready":
            raise RuntimeError("Real Java Bridge did not reach ready state")
        result["comsol_version"] = state.get("comsol_version")
        pid = int(state["pid"])
        owned = process_snapshot(pid)
        if not any(int(process["ProcessId"]) == pid for process in owned):
            raise RuntimeError("Owned Java process identity could not be confirmed")
        identities = [(int(process["ProcessId"]), process["CreationDate"]) for process in owned]
        bridge_token = manager.request_token()
        if bridge_token is None:
            raise RuntimeError("Owned Java Bridge credential unavailable")
        result["token_in_command_line"] = any(
            bridge_token in str(process.get("CommandLine") or "") for process in owned
        )
        result["token_in_parent_environment"] = "COMSOL_WORKBENCH_JAVA_BRIDGE_TOKEN" in os.environ
        result["token_in_status_or_events"] = bridge_token in json.dumps(
            {"status": manager.status(), "events": manager.events_after(0)}, default=str
        )
        parsed = urlsplit(str(state["base_url"]))
        if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
            raise RuntimeError("Bridge listener is not explicit loopback")
        port = parsed.port

        health = await client.health()
        tree = await client.model_tree()
        result["authorized_model_tree"] = bool(
            health.get("real_comsol") and tree.get("command_success")
            and tree.get("model_state_verified") and not tree.get("scientific_validation_passed")
        )
        result["direct_correct_health_http"] = direct(port, "GET", "/health", bridge_token=bridge_token)[0]
        before = await client.parameters()
        before_events = await client.events()

        # Each registered context is reached directly, bypassing FastAPI.
        endpoints = [
            ("GET", "/health", None), ("GET", "/model/tree", None),
            ("GET", "/model/parameters", None),
            ("POST", "/model/parameter", {"name": "wb_probe_unauth", "value": "1"}),
            ("DELETE", "/model/parameter?name=wb_probe_unauth", None),
            ("GET", "/model/boundary-feature", None),
            ("POST", "/model/boundary-feature", {}),
            ("GET", "/model/heat-core", None),
            ("POST", "/model/study/run", {"study_tag": "std1"}),
            ("GET", "/model/heat-field", None),
            ("POST", "/model/heat-export", {"run_id": "unauth", "attempt_id": "unauth"}),
            ("POST", "/commands/test-feature", {"tag": "wb_probe_unauth"}),
            ("GET", "/progress", None), ("GET", "/events", None),
        ]
        wrong = "0" * 64 if bridge_token != "0" * 64 else "1" * 64
        for method, path, body in endpoints:
            for supplied in (None, wrong, "malformed-token"):
                status, _ = direct(port, method, path, bridge_token=supplied, body=body)
                result["direct_negative_cases"] = int(result["direct_negative_cases"]) + 1
                result["direct_negative_rejected"] = int(result["direct_negative_rejected"]) + (status == 401)
        duplicate_status, _ = direct(port, "GET", "/health", bridge_token=bridge_token, duplicate=True)
        result["direct_negative_cases"] = int(result["direct_negative_cases"]) + 1
        result["direct_negative_rejected"] = int(result["direct_negative_rejected"]) + (duplicate_status == 401)

        after = await client.parameters()
        after_events = await client.events()
        result["model_unchanged"] = before.get("parameters") == after.get("parameters") and sha256(model) == FIXTURE_SHA
        result["unauthorized_study_event_count"] = sum(
            1 for event in after_events.get("events", [])
            if event.get("sequence", 0) > before_events.get("latest_sequence", 0)
            and event.get("phase") == "study"
        )

        # The outer shutdown route must also reject callers lacking its separate token.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as outer:
            stop_response = await outer.post("/api/comsol/bridge/stop")
            result["outer_unauthorized_stop_http"] = stop_response.status_code
        if manager.status()["state"] != "ready":
            raise RuntimeError("Unauthorized outer shutdown changed Bridge state")
    except Exception as exc:
        result["error_class"] = type(exc).__name__
    finally:
        try:
            await manager.stop()
            result["token_cleared_after_stop"] = manager.request_token() is None
        except Exception as exc:
            result["cleanup_error_class"] = type(exc).__name__
        residual = 0
        for process_pid, created in identities:
            try:
                if any(process.get("CreationDate") == created for process in process_snapshot(process_pid)):
                    residual += 1
            except (OSError, subprocess.SubprocessError, ValueError):
                result["cleanup_observation_incomplete"] = True
        result["owned_processes_left"] = residual
        if bridge_token is not None:
            logs = list((EVIDENCE / "workspace/logs").glob("java_bridge*.log"))
            result["token_in_owned_logs"] = any(bridge_token in path.read_text("utf-8", errors="replace") for path in logs)
        result["fixture_unchanged"] = sha256(FIXTURE) == FIXTURE_SHA
        (EVIDENCE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = (result["error_class"] is None and result["bridge_state"] == "ready"
              and result["direct_correct_health_http"] == 200
              and result["direct_negative_cases"] == result["direct_negative_rejected"]
              and result["authorized_model_tree"] is True and result["model_unchanged"] is True
              and result["unauthorized_study_event_count"] == 0
              and result["outer_unauthorized_stop_http"] == 401
              and result["owned_processes_left"] == 0
              and result.get("cleanup_observation_incomplete") is not True
              and result["token_in_command_line"] is False
              and result["token_in_parent_environment"] is False
              and result["token_in_owned_logs"] is False
              and result["token_in_status_or_events"] is False
              and result["token_cleared_after_stop"] is True
              and result["fixture_unchanged"] is True)
    print("BRIDGE_AUTH_SOURCE_TEST_PASS" if passed else "BRIDGE_AUTH_SOURCE_TEST_FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
