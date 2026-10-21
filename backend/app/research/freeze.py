"""Source/config freeze utilities for one-shot research batches."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Any, Iterable


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_source_freeze(root: Path, relative_paths: Iterable[str], *, batch_id: str) -> dict[str, Any]:
    root = root.resolve()
    files: dict[str, dict[str, Any]] = {}
    for relative in sorted(set(relative_paths)):
        path = (root / relative).resolve()
        path.relative_to(root)
        if not path.is_file():
            raise FileNotFoundError(relative)
        files[relative] = {"sha256": _hash(path), "size_bytes": path.stat().st_size}
    return {
        "schema_version": "research-source-freeze-v1",
        "batch_id": batch_id,
        "created_at": datetime.now(UTC).isoformat(),
        "algorithm": "SHA-256",
        "files": files,
    }


def verify_source_freeze(root: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    mismatches: list[dict[str, Any]] = []
    for relative, expected in sorted((freeze.get("files") or {}).items()):
        path = (root / relative).resolve()
        path.relative_to(root)
        actual = _hash(path) if path.is_file() else None
        if actual != expected.get("sha256"):
            mismatches.append({"path": relative, "expected": expected.get("sha256"), "actual": actual})
    return {"valid": not mismatches, "mismatches": mismatches}
