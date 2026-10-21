"""Verify a staged compiled-KB resource without ever changing its contents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MANIFEST_NAME = "knowledge_base_manifest.json"


def composite_sha256(root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        path = root / name
        encoded = name.replace("\\", "/").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def verify_knowledge_base(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    profile_path = root / "build_profile.json"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8-sig")) if profile_path.is_file() else {}
    except (OSError, json.JSONDecodeError, TypeError):
        profile = {}
    if not root.is_dir():
        return {"status": "missing", "valid": False, "detail": "Knowledge Base directory is missing", "root": str(root)}
    if not manifest_path.is_file():
        if profile.get("profile") == "public":
            return {
                "status": "not-configured",
                "valid": False,
                "detail": "Public build excludes COMSOL-derived Knowledge Base content. Import a locally authorized compiled KB or continue without it.",
                "root": str(root),
                "build_profile": profile,
            }
        return {
            "status": "development-unverified",
            "valid": False,
            "detail": "Knowledge Base manifest is absent; audited development content remains readable but packaged integrity is unverified",
            "root": str(root),
            "build_profile": profile,
        }
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = [str(item) for item in payload["files"]]
        missing = [name for name in files if not (root / name).is_file()]
        if missing:
            return {"status": "corrupt", "valid": False, "detail": f"Missing manifest files: {', '.join(missing)}", "root": str(root), "manifest": payload}
        actual = composite_sha256(root, files)
        valid = actual == str(payload.get("sha256", ""))
        return {
            "status": "verified" if valid else "corrupt",
            "valid": valid,
            "detail": "Knowledge Base manifest hash verified" if valid else "Knowledge Base integrity warning: SHA-256 mismatch",
            "root": str(root),
            "sha256": actual,
            "manifest": payload,
            "build_profile": profile,
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "corrupt", "valid": False, "detail": f"Knowledge Base manifest error: {type(exc).__name__}: {exc}", "root": str(root)}
