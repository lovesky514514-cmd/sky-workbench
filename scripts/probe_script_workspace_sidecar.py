"""Probe one new, isolated sidecar without installing or modifying a release."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(base: str, path: str, token: str = "", data: dict | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Workbench-Token"] = token
    body = json.dumps(data).encode() if data is not None else None
    call = urllib.request.Request(base + path, data=body, headers=headers,
                                  method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(call, timeout=4) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def run(exe: Path, output: Path, resource: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    app_data, workspace = output / "app-data", output / "workspace"
    app_data.mkdir(); workspace.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    token = secrets.token_urlsafe(32)
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    # The probe is intentionally given a separate Python on PATH. The frozen
    # sidecar itself is never treated as an arbitrary-script interpreter.
    env = {
        "SystemRoot": str(windows), "WINDIR": str(windows), "COMSPEC": str(windows / "System32/cmd.exe"),
        "PATH": os.pathsep.join((str(Path(os.sys.executable).parent), str(windows / "System32"), str(windows))),
        "APPDATA": str(app_data), "LOCALAPPDATA": str(app_data), "USERPROFILE": str(app_data),
        "TEMP": str(output), "TMP": str(output), "COMSOL_WORKBENCH_SESSION_TOKEN": token,
    }
    command = [str(exe), "--desktop", "--host", "127.0.0.1", "--port", str(port),
               "--app-data-dir", str(app_data), "--workspace-dir", str(workspace),
               "--resource-dir", str(resource), "--kb-dir", str(resource / "knowledge_base"),
               "--java-bridge-dir", str(resource / "java_bridge")]
    summary: dict[str, object] = {"health": "BLOCKED", "unauthorized": "BLOCKED",
                                  "script": "BLOCKED", "cancel": "BLOCKED", "shutdown": "BLOCKED"}
    with (output / "runtime-local.log").open("wb") as log:
        child = subprocess.Popen(command, env=env, cwd=output, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(90):
                if child.poll() is not None:
                    summary["runtime_exit_code"] = child.returncode
                    break
                try:
                    code, health = request(base, "/api/health", token)
                    if code == 200 and health.get("status") == "ok":
                        summary["health"] = "PASS"
                        break
                except (OSError, TimeoutError):
                    pass
                time.sleep(0.5)
            if summary["health"] == "PASS":
                summary["unauthorized"] = "PASS" if request(base, "/api/health")[0] == 401 else "FAIL"
                code, available = request(base, "/api/script-runs/availability", token)
                if code == 200 and available.get("engines", {}).get("Python") is True:
                    code, started = request(base, "/api/script-runs", token,
                                            {"engine": "Python", "source": "print('SKY_SCRIPT_RUNTIME_OK')", "confirmed": True})
                    if code == 200:
                        for _ in range(40):
                            _, result = request(base, f"/api/script-runs/{started['run_id']}", token)
                            if result.get("state") not in ("PENDING", "RUNNING"):
                                summary["script"] = "PASS" if result.get("state") == "COMPLETED" and \
                                    "SKY_SCRIPT_RUNTIME_OK" in result.get("output", "") and \
                                    result.get("scientific_validation_passed") is False else "FAIL"
                                break
                            time.sleep(0.25)
                    if summary["script"] == "PASS":
                        code, started = request(base, "/api/script-runs", token,
                                                {"engine": "Python", "source": "import time\ntime.sleep(60)", "confirmed": True})
                        if code == 200:
                            canceled = request(base, f"/api/script-runs/{started['run_id']}/cancel", token, {})[1]
                            summary["cancel"] = "PASS" if canceled.get("state") == "CANCELED" else "FAIL"
                else:
                    summary["script"] = "UNAVAILABLE_PYTHON"
                summary["shutdown"] = "PASS" if request(base, "/api/desktop/shutdown", token, {})[0] == 200 else "FAIL"
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                summary["shutdown"] = "FAIL"
                child.terminate()  # Only this probe's exact child, never a process-name match.
                child.wait(timeout=5)
            summary["runtime_exit_code"] = child.returncode
        finally:
            if child.poll() is None:
                child.terminate(); child.wait(timeout=5)
    (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--resource", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.exe.resolve(), args.out.resolve(), args.resource.resolve()), indent=2))
