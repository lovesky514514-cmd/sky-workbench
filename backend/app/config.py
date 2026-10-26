"""Application paths and local settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from app.app_paths import app_paths

BACKEND_ROOT = app_paths.backend_root
PROJECT_ROOT = app_paths.project_root
REPOSITORY_ROOT = app_paths.repository_root
LEGACY_ROOT = app_paths.legacy_root
COMPILED_KB_ROOT = app_paths.kb_dir
COMSOL_PDF_ROOT = COMPILED_KB_ROOT.parent
WORKSPACE_ROOT = app_paths.workspace_dir
FRONTEND_DIST = app_paths.frontend_dist
SETTINGS_PATH = app_paths.settings_path
DATABASE_PATH = app_paths.database_path
JAVA_BRIDGE_ROOT = app_paths.java_bridge_dir
CACHE_ROOT = app_paths.cache_dir

WORKSPACE_DIRS = (
    "models",
    "data",
    "literature",
    "comsol_documentation",
    "research_notes",
    "knowledge_base",
    "past_runs",
    "outputs",
    "reports",
    "logs",
)


def ensure_workspace() -> None:
    """Create only Workbench-owned directories."""
    app_paths.ensure_writable()
    for name in WORKSPACE_DIRS:
        (WORKSPACE_ROOT / name).mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 1,
    "active_project": "workbench",
    "active_provider": "",
    "retrieval_mode": "accurate",
    # Optional workflows are opt-in. The switch never bypasses PLAN review or
    # starts a COMSOL build by itself.
    "extensions": {"literature_modeling": {"enabled": False, "deepseek_enabled": False}},
    "rag": {
        "mode": "accurate",
        "dense": {
            "enabled": True,
            "provider": "local",
            "model": "Qwen/Qwen3-Embedding-0.6B",
        },
        "reranker": {
            "enabled": True,
            "provider": "local",
            "model": "Qwen/Qwen3-Reranker-0.6B",
        },
        "fallback": {
            "hash_embedding_allowed": True,
            "label": "emergency-only",
        },
    },
    "comsol": {
        "root": "",
        "java_bridge": {
            "enabled": True,
            "base_url": "http://127.0.0.1:8765",
            "mode": "standalone",
            "poll_ms": 1000,
        }
    },
    "execution": {
        "max_repairs": 1,
        "resource_monitor_interval_seconds": 2.0,
        "warning_ram_available_percent": 10.0,
        "critical_ram_available_percent": 5.0,
        "warning_disk_free_gb": 5.0,
        "critical_disk_free_gb": 1.0,
    },
    "providers": {
        "ollama": {
            "enabled": True,
            "base_url": "http://127.0.0.1:11434",
            "model": "",
        },
        "lm_studio": {
            "enabled": True,
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "",
        },
        "openai_compatible": {
            "enabled": True,
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "",
        },
        "deepseek": {
            "enabled": False,
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        },
        "kimi": {
            "enabled": False,
            "base_url": "https://api.moonshot.cn/v1",
            "model": "",
        },
        "doubao": {
            "enabled": False,
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "",
        },
        "openai": {
            "enabled": False,
            "base_url": "https://api.openai.com/v1",
            "model": "",
        },
        "claude": {
            "enabled": False,
            "base_url": "https://api.anthropic.com/v1",
            "model": "",
        },
        "gemini": {
            "enabled": False,
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": "",
        },
        "openrouter": {
            "enabled": False,
            "base_url": "https://openrouter.ai/api/v1",
            "model": "",
        },
    },
}


def _apply_product_runtime_overrides(settings: dict[str, Any]) -> None:
    """Supply a packaged Ollama default without overriding an explicit choice."""

    base_url = os.getenv("COMSOL_WORKBENCH_OLLAMA_BASE_URL", "").strip()
    model = os.getenv("COMSOL_WORKBENCH_PRODUCT_MODEL", "").strip()
    if not base_url or not model:
        return
    # The desktop shell only recommends a locally detected model. A saved
    # cloud selection must survive restart; it is never silently replaced.
    if not settings.get("active_provider"):
        settings["active_provider"] = "ollama"
        for name, provider in settings.get("providers", {}).items():
            if name != "ollama" and isinstance(provider, dict):
                provider["enabled"] = False
    providers = settings.get("providers", {})
    ollama = providers.setdefault("ollama", {})
    ollama["base_url"] = base_url.rstrip("/")
    if not ollama.get("model"):
        ollama["model"] = model


@dataclass(slots=True)
class SettingsStore:
    """Thread-safe settings store that never persists API keys."""

    path: Path = SETTINGS_PATH
    _lock: RLock = field(init=False, repr=False)
    _secrets: dict[str, str] = field(init=False, repr=False)
    _migration: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = RLock()
        self._secrets: dict[str, str] = {}
        self._migration = {"status": "not_required", "backup": None, "detail": "Configuration schema is current"}

    def load(self) -> dict[str, Any]:
        with self._lock:
            settings = json.loads(json.dumps(DEFAULT_SETTINGS))
            if self.path.exists():
                try:
                    original = self.path.read_text(encoding="utf-8-sig")
                    stored = json.loads(original)
                    if not isinstance(stored, dict):
                        raise ValueError("settings root must be an object")
                    if int(stored.get("schema_version", 0)) < int(DEFAULT_SETTINGS["schema_version"]):
                        stored = self._migrate_v070(stored, original)
                    self._merge(settings, stored)
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._migration = {
                        "status": "failed",
                        "backup": self._existing_backup(),
                        "detail": f"Configuration migration failed. Your previous configuration was preserved. {type(exc).__name__}: {exc}",
                    }
            _apply_product_runtime_overrides(settings)
            return settings

    def update(self, update: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            clean = self._extract_secrets(update)
            settings = self.load()
            if self._migration.get("status") == "failed":
                raise RuntimeError("Configuration migration failed; retry or explicitly use defaults before saving")
            self._merge(settings, clean)
            self._write_atomic(settings)
            return settings

    def migration_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._migration)

    def retry_migration(self) -> dict[str, Any]:
        with self._lock:
            self._migration = {"status": "retrying", "backup": self._existing_backup(), "detail": "Retrying configuration migration"}
            self.load()
            return self.migration_status()

    def use_defaults(self) -> dict[str, Any]:
        with self._lock:
            backup = self._existing_backup()
            if self.path.exists() and not backup:
                backup = self._backup_original(self.path.read_text(encoding="utf-8-sig"))
            defaults = json.loads(json.dumps(DEFAULT_SETTINGS))
            self._write_atomic(defaults)
            self._migration = {"status": "defaults", "backup": backup, "detail": "Safe defaults are active; the previous configuration backup was preserved"}
            return defaults

    def secret(self, provider: str) -> str:
        env_name = f"COMSOL_WORKBENCH_{provider.upper()}_API_KEY"
        # DeepSeek's conventional environment name is also accepted. Neither
        # name is copied into settings, logs, API responses or diagnostic files.
        conventional = os.getenv("DEEPSEEK_API_KEY", "") if provider == "deepseek" else ""
        return self._secrets.get(provider, "") or os.getenv(env_name, "") or conventional

    def _extract_secrets(self, update: dict[str, Any]) -> dict[str, Any]:
        clean = json.loads(json.dumps(update))
        providers = clean.get("providers", {})
        if isinstance(providers, dict):
            for name, config in providers.items():
                if isinstance(config, dict) and "api_key" in config:
                    value = config.pop("api_key")
                    if isinstance(value, str) and value:
                        self._secrets[name] = value
        return clean

    def _migrate_v070(self, stored: dict[str, Any], original: str) -> dict[str, Any]:
        backup = self._existing_backup() or self._backup_original(original)
        migrated = json.loads(json.dumps(stored))
        providers = migrated.get("providers")
        if isinstance(providers, dict):
            for config in providers.values():
                if isinstance(config, dict):
                    config.pop("api_key", None)
        migrated["schema_version"] = DEFAULT_SETTINGS["schema_version"]
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        self._merge(merged, migrated)
        self._write_atomic(merged)
        self._migration = {"status": "migrated", "backup": backup, "detail": "v0.7 configuration migrated to the current release schema"}
        return merged

    def _existing_backup(self) -> str | None:
        try:
            matches = sorted(self.path.parent.glob("config_backup_v070_*.json"))
            return str(matches[0]) if matches else None
        except OSError:
            return None

    def _backup_original(self, original: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.parent / f"config_backup_v070_{stamp}.json"
        backup.write_text(original, encoding="utf-8")
        return str(backup)

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.rc1.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @classmethod
    def _merge(cls, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                cls._merge(target[key], value)
            else:
                target[key] = value


settings_store = SettingsStore()
