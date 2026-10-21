"""Scientific project catalog and bounded file explorer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import LEGACY_ROOT, WORKSPACE_ROOT, settings_store


IGNORED_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".gitkeep",
    "sessions.db",
    "sessions.db-shm",
    "sessions.db-wal",
    "settings.json",
}
MAX_ITEMS = 600


class ProjectService:
    def projects(self) -> list[dict[str, Any]]:
        active = settings_store.load().get("active_project", "workbench")
        projects = [
            {
                "id": "workbench",
                "name": "COMSOL Scientific Workbench",
                "path": str(WORKSPACE_ROOT),
                "read_only": False,
            }
        ]
        if LEGACY_ROOT.exists():
            projects.append(
                {
                    "id": "legacy",
                    "name": "Universal Scientific COMSOL Agent (legacy)",
                    "path": str(LEGACY_ROOT),
                    "read_only": True,
                }
            )
        for project in projects:
            project["active"] = project["id"] == active
        return projects

    def select(self, project_id: str) -> dict[str, Any]:
        project = next((item for item in self.projects() if item["id"] == project_id), None)
        if project is None:
            raise KeyError(project_id)
        settings_store.update({"active_project": project_id})
        return project

    def tree(self, project_id: str, depth: int = 3) -> list[dict[str, Any]]:
        project = next((item for item in self.projects() if item["id"] == project_id), None)
        if project is None:
            raise KeyError(project_id)
        root = Path(project["path"]).resolve()
        counter = [0]
        return self._children(root, root, depth, counter)

    def _children(
        self, root: Path, directory: Path, depth: int, counter: list[int]
    ) -> list[dict[str, Any]]:
        if depth < 0 or counter[0] >= MAX_ITEMS:
            return []
        try:
            items = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for item in items:
            if item.name in IGNORED_NAMES or counter[0] >= MAX_ITEMS:
                continue
            try:
                resolved = item.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            counter[0] += 1
            entry: dict[str, Any] = {
                "name": item.name,
                "path": str(resolved),
                "kind": "directory" if item.is_dir() else self._kind(item),
            }
            if item.is_dir() and depth > 0:
                entry["children"] = self._children(root, item, depth - 1, counter)
            result.append(entry)
        return result

    @staticmethod
    def _kind(path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".mph": "comsol-model",
            ".pdf": "pdf",
            ".md": "note",
            ".txt": "note",
            ".csv": "data",
            ".xlsx": "data",
            ".png": "image",
            ".jpg": "image",
        }.get(suffix, "file")


project_service = ProjectService()
