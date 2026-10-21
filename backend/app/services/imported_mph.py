"""Bounded MPH upload storage; file import is not scientific verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator

from app.app_paths import app_paths


class ImportedMphStore:
    """Keep received bytes and the editable copy in an owned, separate area."""

    def __init__(self, models_root: Path = app_paths.models_dir, *, max_bytes: int = 1024**3) -> None:
        self.models_root = models_root.resolve()
        self.max_bytes = max_bytes

    def _root(self) -> Path:
        self.models_root.mkdir(parents=True, exist_ok=True)
        root = self.models_root / "imported"
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
        if resolved.parent != self.models_root:
            raise ValueError("import_root_outside_workspace")
        return resolved

    async def save(self, filename: str, chunks: AsyncIterator[bytes]) -> dict[str, object]:
        # The display name is never used in a filesystem path. Reject path and
        # control characters anyway, so UI and audit records stay unambiguous.
        if (not filename or len(filename) > 240 or not filename.lower().endswith(".mph")
                or any(char in filename for char in "/\\:\x00")
                or any(ord(char) < 32 for char in filename)):
            raise ValueError("invalid_mph_filename")

        root = self._root()
        item_id = uuid.uuid4().hex
        folder = root / item_id
        folder.mkdir(exist_ok=False)
        original, working = folder / "original.mph", folder / "working.mph"
        digest, size = hashlib.sha256(), 0
        try:
            # Stream in bounded blocks. Yield between writes so other Runtime
            # requests remain responsive without requiring a thread pool.
            with original.open("xb") as stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("mph_file_too_large")
                    digest.update(chunk)
                    for offset in range(0, len(chunk), 1024 * 1024):
                        stream.write(chunk[offset:offset + 1024 * 1024])
                        await asyncio.sleep(0)
            if size == 0:
                raise ValueError("empty_mph_file")
            with original.open("rb") as source, working.open("xb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
                    await asyncio.sleep(0)
            item: dict[str, object] = {
                "id": item_id, "original_filename": filename, "size_bytes": size,
                "sha256": digest.hexdigest(), "imported_at": datetime.now(UTC).isoformat(),
                "storage_relative_path": f"models/imported/{item_id}",
                "preview_state": "not_loaded", "scientific_validation_passed": False,
            }
            temporary = folder / "manifest.json.tmp"
            temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, folder / "manifest.json")
            return item
        except BaseException:
            # Only this just-created UUID directory is removed. An existing MPH
            # or user workspace is never a deletion target on upload failure.
            if folder.resolve().parent == root and not folder.is_symlink():
                shutil.rmtree(folder)
            raise

    def get(self, item_id: str) -> dict[str, object]:
        if not re.fullmatch(r"[0-9a-f]{32}", item_id):
            raise KeyError(item_id)
        root = self._root()
        folder = root / item_id
        manifest = folder / "manifest.json"
        if folder.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 32_768:
            raise KeyError(item_id)
        try:
            item = json.loads(manifest.read_text(encoding="utf-8"))
            if item["id"] != item_id or not (folder / "original.mph").is_file():
                raise KeyError(item_id)
            return item
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise KeyError(item_id) from exc

    def list(self) -> list[dict[str, object]]:
        result = []
        for folder in self._root().iterdir():
            try:
                result.append(self.get(folder.name))
            except KeyError:
                continue
        return sorted(result, key=lambda item: str(item["imported_at"]), reverse=True)

    def working_path(self, item_id: str) -> Path:
        self.get(item_id)
        path = (self._root() / item_id / "working.mph").resolve()
        if path.parent != self._root() / item_id or not path.is_file():
            raise KeyError(item_id)
        return path


imported_mph_store = ImportedMphStore()
