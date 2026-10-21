"""RC4.1 Lite installer bootstrap with verified external payload."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _message(title: str, body: str, *, error: bool = False) -> None:
    if os.getenv("COMSOL_WORKBENCH_HEADLESS") == "1":
        return
    ctypes.windll.user32.MessageBoxW(None, body, title, 0x10 if error else 0x40)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_payload(source: Path, payload: dict[str, object]) -> None:
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("payload manifest has no entries")
    source = source.resolve()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("invalid payload entry")
        relative = Path(str(raw["path"]))
        path = (source / relative).resolve()
        if source not in path.parents or not path.is_file():
            raise ValueError(f"unsafe or missing payload entry: {relative}")
        if path.stat().st_size != int(raw["bytes"]) or _sha256(path) != str(raw["sha256"]):
            raise ValueError(f"payload integrity mismatch: {relative}")


def main() -> int:
    executable = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    source = executable.parent / "installer_payload" / "COMSOL Scientific Workbench"
    manifest = source / "RC41_PAYLOAD_MANIFEST.json"
    if not manifest.is_file():
        _message("RC4.1 Setup — BLOCKED", f"Installer payload is missing:\n{manifest}", error=True)
        return 3
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("product") != "COMSOL Scientific Workbench RC4.1" or payload.get("version") != "1.1.0-rc4.1" or payload.get("distribution") != "lite":
            raise ValueError("payload identity mismatch")
        if payload.get("model_weights_included") is not False or payload.get("ollama_runtime_included") is not False:
            raise ValueError("Lite payload unexpectedly includes offline AI bytes")
        _verify_payload(source, payload)
        target = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "COMSOL Scientific Workbench RC4.1"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"target already exists; remove the prior RC4.1 explicitly before reinstalling: {target}")
        shutil.copytree(source, target)
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for shortcut in (desktop / "COMSOL Scientific Workbench RC4.1.lnk", start_menu / "COMSOL Scientific Workbench RC4.1.lnk"):
            shortcut.parent.mkdir(parents=True, exist_ok=True)
            ps = (
                "$w=New-Object -ComObject WScript.Shell;"
                f"$s=$w.CreateShortcut('{str(shortcut).replace(chr(39), chr(39) * 2)}');"
                f"$s.TargetPath='{str(target / 'START_RC41.exe').replace(chr(39), chr(39) * 2)}';"
                f"$s.WorkingDirectory='{str(target).replace(chr(39), chr(39) * 2)}';"
                f"$s.IconLocation='{str(target / 'Workbench.exe').replace(chr(39), chr(39) * 2)}';$s.Save()"
            )
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        _message("RC4.1 Setup — FAILED", f"Installation failed:\n{type(exc).__name__}: {exc}", error=True)
        return 2
    _message("RC4.1 Setup — COMPLETE", f"Installed to:\n{target}\n\nModel weights are not included. The first-run guide detects local providers and requests confirmation before any large download.")
    if os.getenv("COMSOL_WORKBENCH_SETUP_NO_LAUNCH") != "1":
        subprocess.Popen([str(target / "START_RC41.exe")], cwd=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
