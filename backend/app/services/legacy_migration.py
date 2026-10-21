"""Bounded detection and conservative migration of the portable legacy agent."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.app_paths import app_paths
from app.config import settings_store

MARKERS = {"RUN_ALL_ONE_CLICK.bat", "start_opencode.ps1", "mcp_servers", "setup_wizard.py", "AGENTS.md"}


def _candidate_paths(selected: str | None = None) -> list[Path]:
    values = [app_paths.legacy_root, app_paths.project_root.parent / "Universal_Scientific_COMSOL_Agent", app_paths.project_root.parent / "Universal_Scientific_COMSOL_Agent_Opencode_v6"]
    if selected:
        values.append(Path(selected).expanduser())
    unique: list[Path] = []
    for value in values:
        resolved = value.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def detect_legacy(selected: str | None = None) -> dict[str, Any]:
    detected = []
    for path in _candidate_paths(selected):
        if not path.is_dir():
            continue
        markers = sorted(name for name in MARKERS if (path / name).exists())
        if len(markers) >= 2:
            detected.append({"path": str(path), "markers": markers})
    state = settings_store.load().get("legacy", {})
    return {"detected": detected, "previous_action": state.get("action", ""), "prompt_required": bool(detected) and not bool(state.get("action"))}


def handle_legacy(action: Literal["keep", "import_keep", "backup_remove", "ignore"], path_value: str, *, confirmed: bool = False, backup_root: str | None = None) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    detection = detect_legacy(str(path))
    if not any(Path(item["path"]) == path for item in detection["detected"]):
        raise ValueError("Selected directory is not a recognized legacy agent")
    result: dict[str, Any] = {"action": action, "path": str(path), "imported": {}}
    if action == "import_keep":
        result["imported"] = _safe_import(path)
    elif action == "backup_remove":
        if not confirmed:
            return {**result, "status": "confirmation_required", "will_backup": ["docs", "workspace"]}
        backup_dir = Path(backup_root).expanduser().resolve() if backup_root else Path.home() / "Desktop"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging = app_paths.cache_dir / f"legacy_backup_{stamp}"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            copied = []
            for name in ("docs", "workspace"):
                source = path / name
                if source.is_dir():
                    shutil.copytree(source, staging / name)
                    copied.append(name)
            archive = Path(shutil.make_archive(str(backup_dir / f"Universal_COMSOL_Agent_Backup_{stamp}"), "zip", staging))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if not archive.is_file():
            raise RuntimeError("Legacy backup archive was not created; removal cancelled")
        shutil.rmtree(path)
        result.update({"backup": str(archive), "backed_up": copied, "removed": True})
    settings_store.update({"legacy": {"action": action, "path": str(path)}})
    return {**result, "status": "completed"}


def _safe_import(path: Path) -> dict[str, Any]:
    imported: dict[str, Any] = {}
    config_path = path / "config" / "local_paths.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            config = {}
        workspace = config.get("workspace") or config.get("workspace_path")
        comsol = config.get("comsol", {}).get("selected", {}) if isinstance(config.get("comsol"), dict) else {}
        comsol_root = comsol.get("root") if isinstance(comsol, dict) else None
        if isinstance(workspace, str):
            imported["workspace_path"] = workspace
        if isinstance(comsol_root, str):
            imported["comsol_path"] = comsol_root
    settings_store.update({"legacy": {"imported": imported}})
    return imported
