import json
from pathlib import Path
from types import SimpleNamespace

from app.services import legacy_migration


def make_legacy(path: Path) -> None:
    path.mkdir()
    (path / "RUN_ALL_ONE_CLICK.bat").write_text("", encoding="utf-8")
    (path / "AGENTS.md").write_text("legacy", encoding="utf-8")


def test_detection_is_bounded_to_candidates(tmp_path):
    legacy = tmp_path / "legacy"
    make_legacy(legacy)
    result = legacy_migration.detect_legacy(str(legacy))
    assert any(item["path"] == str(legacy.resolve()) for item in result["detected"])


def test_keep_does_not_modify_legacy_files(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    make_legacy(legacy)
    marker = legacy / "AGENTS.md"
    before = marker.read_bytes()
    monkeypatch.setattr(legacy_migration.settings_store, "path", tmp_path / "settings.json")
    result = legacy_migration.handle_legacy("keep", str(legacy))
    assert result["status"] == "completed"
    assert marker.read_bytes() == before


def test_import_excludes_secrets(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    make_legacy(legacy)
    (legacy / "config").mkdir()
    (legacy / "config" / "local_paths.json").write_text(json.dumps({"workspace_path": "D:/Science", "api_key": "SECRET"}), encoding="utf-8")
    monkeypatch.setattr(legacy_migration.settings_store, "path", tmp_path / "settings.json")
    result = legacy_migration.handle_legacy("import_keep", str(legacy))
    assert result["imported"] == {"workspace_path": "D:/Science"}
    assert "SECRET" not in json.dumps(result)


def test_backup_remove_requires_confirmation_and_preserves_backup(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    make_legacy(legacy)
    (legacy / "docs").mkdir()
    (legacy / "docs" / "note.md").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(legacy_migration.settings_store, "path", tmp_path / "settings.json")
    monkeypatch.setattr(legacy_migration, "app_paths", SimpleNamespace(cache_dir=tmp_path / "cache", legacy_root=tmp_path / "known", project_root=tmp_path / "project"))
    pending = legacy_migration.handle_legacy("backup_remove", str(legacy))
    assert pending["status"] == "confirmation_required"
    assert legacy.exists()
    result = legacy_migration.handle_legacy("backup_remove", str(legacy), confirmed=True, backup_root=str(tmp_path / "desktop"))
    assert Path(result["backup"]).is_file()
    assert not legacy.exists()
