"""Application paths and local settings."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from app.app_paths import app_paths
from app.providers.deepseek import DEFAULT_MODEL as DEEPSEEK_MODEL
from app.providers.endpoints import endpoint_identity, provider_endpoint

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
_logger = logging.getLogger(__name__)
_ENDPOINT_BINDINGS = "provider_credential_endpoints"

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
    "research": {"enabled": True, "model_calls": 8, "token_limit": 160000, "output_tokens": 1800, "concurrency": 3, "role_models": {}},
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
            "model": DEEPSEEK_MODEL,
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
    _secrets: dict[str, tuple[str, str]] = field(init=False, repr=False)
    _migration: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = RLock()
        self._secrets = {}
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
            for name, config in settings["providers"].items():
                if isinstance(config, dict) and "api_key" in config:
                    config.pop("api_key")
                    settings.setdefault(_ENDPOINT_BINDINGS, {})[name] = None
            _apply_product_runtime_overrides(settings)
            return settings

    def update(self, update: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            settings = self.load()
            if self._migration.get("status") == "failed":
                raise RuntimeError("Configuration migration failed; retry or explicitly use defaults before saving")
            clean = json.loads(json.dumps(update))
            clean.pop(_ENDPOINT_BINDINGS, None)
            provider_updates = clean.get("providers", {})
            bindings = dict(settings.get(_ENDPOINT_BINDINGS, {}))
            secrets = self._secrets.copy()
            endpoint_changes = []
            for name, patch in provider_updates.items():
                if name not in DEFAULT_SETTINGS["providers"] or not isinstance(patch, dict):
                    raise ValueError("invalid_provider_profile")
                existing = settings["providers"][name]
                old = self._identity(existing.get("base_url", ""))
                new = self._identity(patch.get("base_url", existing.get("base_url", "")))
                if "base_url" in patch and new is None:
                    # 无效地址可能含内嵌凭据。保留未配置状态，避免把原地址写回设置。
                    patch["base_url"] = ""
                previous_binding = bindings.get(name)
                if ("base_url" in patch and new is not None and new.startswith("http://")
                        and any(isinstance(value, str) and value.startswith("https://")
                                for value in (old, previous_binding))):
                    raise ValueError("provider_https_downgrade")
                if "base_url" in patch and old != new:
                    # 回到旧地址也要重新录入；空绑定表示旧凭据已失效。
                    secrets.pop(name, None)
                    bindings[name] = previous_binding if previous_binding else None
                    endpoint_changes.append((name, old, new))
                key = patch.pop("api_key", None)
                if isinstance(key, str) and key:
                    try:
                        bound = provider_endpoint(name, patch.get("base_url", existing["base_url"]))
                    except ValueError:
                        bindings[name] = None
                        secrets.pop(name, None)
                    else:
                        bindings[name] = bound
                        secrets[name] = (bound, key)
            self._merge(settings, clean)
            if bindings:
                settings[_ENDPOINT_BINDINGS] = bindings
            self._write_atomic(settings)
            self._secrets = secrets
            for name, old, new in endpoint_changes:
                before, after = (sha256(value.encode()).hexdigest() if value else None for value in (old, new))
                _logger.info("provider.endpoint_changed provider=%s previous=%s current=%s", name, before, after)
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
            self._secrets.clear()
            self._migration = {"status": "defaults", "backup": backup, "detail": "Safe defaults are active; the previous configuration backup was preserved"}
            return defaults

    @staticmethod
    def _identity(base_url: str) -> str | None:
        try:
            return endpoint_identity(str(base_url))
        except ValueError:
            return None

    def secret(self, provider: str, base_url: str | None = None) -> str:
        with self._lock:
            settings = self.load()
            profile = settings["providers"].get(provider, {})
            current = self._identity(profile.get("base_url", ""))
            requested = self._identity(base_url) if base_url is not None else current
            if not current or requested != current or self._migration["status"] == "failed":
                return ""
            bindings = settings.get(_ENDPOINT_BINDINGS, {})
            if provider in bindings:
                bound, key = self._secrets.get(provider, ("", ""))
                return key if bound == bindings[provider] == current else ""
            # 进程环境凭据只适用于产品内置端点。旧配置中的密钥不会迁入。
            if current != self._identity(DEFAULT_SETTINGS["providers"].get(provider, {}).get("base_url", "")):
                return ""
            env_name = f"COMSOL_WORKBENCH_{provider.upper()}_API_KEY"
            conventional = os.getenv("DEEPSEEK_API_KEY", "") if provider == "deepseek" else ""
            return os.getenv(env_name, "") or conventional

    def _migrate_v070(self, stored: dict[str, Any], original: str) -> dict[str, Any]:
        backup = self._existing_backup() or self._backup_original(original)
        migrated = json.loads(json.dumps(stored))
        providers = migrated.get("providers")
        if isinstance(providers, dict):
            for name, config in providers.items():
                if isinstance(config, dict) and "api_key" in config:
                    config.pop("api_key")
                    migrated.setdefault(_ENDPOINT_BINDINGS, {})[name] = None
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
