"""Single source of truth for development and packaged desktop paths."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


ENV_PREFIX = "COMSOL_WORKBENCH_"


def _path(value: str | None, fallback: Path) -> Path:
    return Path(value).expanduser().resolve() if value else fallback.resolve()


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved paths; only ``workspace_dir`` and ``app_data_dir`` are writable."""

    backend_root: Path
    project_root: Path
    repository_root: Path
    install_dir: Path
    resource_dir: Path
    app_data_dir: Path
    workspace_dir: Path
    models_dir: Path
    outputs_dir: Path
    reports_dir: Path
    logs_dir: Path
    cache_dir: Path
    kb_dir: Path
    rag_index_dir: Path
    frontend_dist: Path
    java_bridge_dir: Path
    legacy_root: Path
    settings_path: Path
    database_path: Path
    desktop_mode: bool

    @classmethod
    def discover(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        frozen: bool | None = None,
        executable: Path | None = None,
        bundle_root: Path | None = None,
    ) -> "AppPaths":
        env = dict(os.environ if environment is None else environment)
        backend_root = Path(__file__).resolve().parents[1]
        project_root = backend_root.parent
        repository_root = project_root.parent
        is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        desktop_mode = is_frozen or env.get(f"{ENV_PREFIX}DESKTOP_MODE", "") == "1"
        executable_path = (executable or Path(sys.executable)).resolve()
        embedded_root = Path(
            bundle_root
            or getattr(sys, "_MEIPASS", "")
            or project_root
        ).resolve()

        install_default = executable_path.parent if is_frozen else project_root
        install_dir = _path(env.get(f"{ENV_PREFIX}INSTALL_DIR"), install_default)
        resource_default = embedded_root if is_frozen else project_root
        resource_dir = _path(env.get(f"{ENV_PREFIX}RESOURCE_DIR"), resource_default)

        local_app_data = Path(env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        app_data_default = local_app_data / "COMSOLScientificWorkbench" if desktop_mode else project_root / "workspace"
        app_data_dir = _path(env.get(f"{ENV_PREFIX}APP_DATA_DIR"), app_data_default)
        workspace_default = app_data_dir / "workspace" if desktop_mode else project_root / "workspace"
        workspace_dir = _path(env.get(f"{ENV_PREFIX}WORKSPACE_DIR"), workspace_default)

        development_kb = repository_root / "comsol_better_pdf" / "_compiled_kb"
        packaged_kb = resource_dir / "resources" / "knowledge_base"
        kb_default = packaged_kb if desktop_mode else development_kb
        kb_dir = _path(env.get(f"{ENV_PREFIX}KB_DIR"), kb_default)
        frontend_default = resource_dir / "frontend_dist" if is_frozen else project_root / "frontend" / "dist"
        bridge_default = resource_dir / "java_bridge" if is_frozen else project_root / "java_bridge"
        legacy_default = repository_root / "Universal_Scientific_COMSOL_Agent_Opencode_v6"
        rag_default = workspace_dir / "knowledge_base"

        return cls(
            backend_root=backend_root,
            project_root=project_root,
            repository_root=repository_root,
            install_dir=install_dir,
            resource_dir=resource_dir,
            app_data_dir=app_data_dir,
            workspace_dir=workspace_dir,
            models_dir=workspace_dir / "models",
            outputs_dir=workspace_dir / "outputs",
            reports_dir=workspace_dir / "reports",
            logs_dir=workspace_dir / "logs",
            cache_dir=app_data_dir / "cache",
            kb_dir=kb_dir,
            rag_index_dir=_path(env.get(f"{ENV_PREFIX}RAG_INDEX_DIR"), rag_default),
            frontend_dist=_path(env.get(f"{ENV_PREFIX}FRONTEND_DIST"), frontend_default),
            java_bridge_dir=_path(env.get(f"{ENV_PREFIX}JAVA_BRIDGE_DIR"), bridge_default),
            legacy_root=_path(env.get(f"{ENV_PREFIX}LEGACY_ROOT"), legacy_default),
            settings_path=app_data_dir / "settings.json",
            database_path=app_data_dir / "sessions.db",
            desktop_mode=desktop_mode,
        )

    def ensure_writable(self) -> None:
        """Create Workbench-owned user directories; never create or alter ``kb_dir``."""

        for path in (
            self.app_data_dir,
            self.workspace_dir,
            self.models_dir,
            self.outputs_dir,
            self.reports_dir,
            self.logs_dir,
            self.cache_dir,
            self.rag_index_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


app_paths = AppPaths.discover()

