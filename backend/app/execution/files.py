"""Bounded local reads with explicit reparse-point rejection."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def reject_reparse(path: Path) -> None:
    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        metadata = item.lstat()
        if item.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked or reparse-point workspace paths are not supported")


def bounded_read(root: Path, name: str, limit: int) -> bytes:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("Invalid artifact name")
    path = root / name
    reject_reparse(root)
    reject_reparse(path)
    if path.resolve().parent != root.resolve():
        raise ValueError("Artifact leaves its run directory")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"{name} exceeds its read limit or is not a regular file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        reject_reparse(path)
        current = path.stat()
        # Check the same open file and its final path; hashes use these exact bytes.
        if (len(data) > limit or before.st_size != len(data)
                or (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size)
                != (after.st_ino, after.st_dev, after.st_mtime_ns, after.st_size)
                or (after.st_ino, after.st_dev) != (current.st_ino, current.st_dev)):
            raise ValueError("Artifact changed while reading")
        return data
