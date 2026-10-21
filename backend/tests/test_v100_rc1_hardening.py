from __future__ import annotations

import json
import sqlite3
import tomllib
from pathlib import Path

from app import __version__
from app.config import SettingsStore
from app.agent.model_card import generate_model_card
from app.knowledge.compiled_store import CompiledKnowledgeStore, DEFAULT_NAMESPACE
from app.knowledge.integrity import verify_knowledge_base
from app.redaction import redact_text


def test_v070_settings_migrate_once_with_backup_and_no_persisted_secret(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = {"active_provider": "ollama", "providers": {"ollama": {"model": "qwen", "api_key": "secret"}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    store = SettingsStore(path)

    loaded = store.load()
    backups = list(tmp_path.glob("config_backup_v070_*.json"))
    assert loaded["schema_version"] == 1
    assert loaded["providers"]["ollama"]["model"] == "qwen"
    assert "api_key" not in json.loads(path.read_text(encoding="utf-8"))["providers"]["ollama"]
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == original
    assert store.migration_status()["status"] == "migrated"

    store.load()
    assert len(list(tmp_path.glob("config_backup_v070_*.json"))) == 1


def test_invalid_settings_are_preserved_until_explicit_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    store = SettingsStore(path)

    loaded = store.load()
    assert loaded["schema_version"] == 1
    assert store.migration_status()["status"] == "failed"
    assert path.read_text(encoding="utf-8") == "{broken"

    defaults = store.use_defaults()
    assert defaults["schema_version"] == 1
    assert store.migration_status()["status"] == "defaults"
    assert list(tmp_path.glob("config_backup_v070_*.json"))


def test_public_profile_is_honest_when_proprietary_kb_is_excluded(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "build_profile.json").write_text(
        json.dumps({"profile": "public", "proprietary_knowledge_included": False}), encoding="utf-8"
    )
    status = verify_knowledge_base(tmp_path)
    assert status["status"] == "not-configured"
    assert status["valid"] is False
    assert "excludes" in status["detail"]


def test_clear_semantic_index_preserves_fallback_vectors(tmp_path: Path) -> None:
    store = CompiledKnowledgeStore(tmp_path / "kb", tmp_path / "index.sqlite3")
    with sqlite3.connect(store.db_path) as db:
        store._initialize_schema(db)
        db.executemany(
            "INSERT INTO manifest(key, value) VALUES (?, ?)",
            [("semantic_embedding_namespace", "qwen-test"), ("semantic_last_build", "now")],
        )
        vector = b"1234"
        db.executemany(
            "INSERT INTO embeddings(namespace, unit_id, content_hash, dimensions, vector) VALUES (?, ?, ?, ?, ?)",
            [(DEFAULT_NAMESPACE, "KB-1", "a", 1, vector), ("qwen-test", "KB-1", "a", 1, vector)],
        )
    result = store.clear_semantic_index()
    assert result["removed_rows"] == 1
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT namespace FROM embeddings").fetchall() == [(DEFAULT_NAMESPACE,)]
        assert db.execute("SELECT count(*) FROM manifest WHERE key LIKE 'semantic_%'").fetchone()[0] == 0


def test_log_redaction_covers_tokens_and_passwords() -> None:
    fake_openai_key = "sk-" + "1234567890abcdefghijkl"
    fake_authorization = "Authorization: Bearer " + "abc"
    source = f"{fake_authorization} api_key=value password=hunter2 {fake_openai_key}"
    redacted = redact_text(source)
    assert "abc" not in redacted
    assert "value" not in redacted
    assert "hunter2" not in redacted
    assert fake_openai_key not in redacted
    assert redacted.count("REDACTED") >= 4


def test_v2_product_and_scientific_runtime_version_contract() -> None:
    # V2 is the product release; the unchanged RC5.2 engine has its own version.
    root = Path(__file__).resolve().parents[2]
    product_version = "2.0.0"
    assert __version__ == "1.1.0-rc5.2"
    assert json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))["version"] == product_version
    assert json.loads((root / "frontend" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"] == product_version
    assert tomllib.loads((root / "frontend" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"] == product_version
    rust_source = (root / "frontend" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'const VERSION: &str = env!("CARGO_PKG_VERSION");' in rust_source
    preparation = (root / "scripts" / "prepare_desktop.ps1").read_text(encoding="utf-8")
    assert "version = '2.0.0'" in preparation
    assert "runtime_version = '1.1.0-rc5.2'" in preparation


def test_model_card_preserves_resource_override_warning(monkeypatch, tmp_path: Path) -> None:
    import app.agent.model_card as model_card_module

    monkeypatch.setattr(model_card_module, "WORKSPACE_ROOT", tmp_path)
    path = generate_model_card(
        run_id="override-run",
        session_id="session",
        plan={"plan_id": "plan", "research_goal": "resource override test"},
        execution={
            "command_success": True,
            "model_state_verified": True,
            "scientific_validation_success": True,
            "resource": {"stage_b": {"risk_level": "HIGH", "allowed": True, "override_present": True}},
        },
        validation_report={"scope": "solution", "passed": True, "checks": []},
        repair_history=[],
    )
    content = path.read_text(encoding="utf-8")
    assert "Resource Precheck: USER_OVERRIDE" in content
    assert "Stage B risk: HIGH" in content
