"""Identify the code used by a run without changing historical records."""

import hashlib
from functools import lru_cache
from pathlib import Path
import subprocess
import sys


@lru_cache(maxsize=1)
def source_revision() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    if getattr(sys, "frozen", False):
        binary = Path(sys.executable)
        return {
            "kind": "frozen_runtime",
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
    digest = hashlib.sha256()
    sources = [
        *(root / "backend" / "app").rglob("*.py"),
        root / "backend" / "mathics_worker.py",
        root / "backend" / "desktop_runtime.py",
    ]
    for path in sorted(sources):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=3,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        commit = "UNAVAILABLE"
    return {
        "kind": "source_snapshot",
        "git_commit": commit,
        "source_tree_sha256": digest.hexdigest(),
    }
