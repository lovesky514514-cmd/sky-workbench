"""Exclusive-create artifact helpers for immutable research evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    path: Path
    ref: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha256": self.sha256, "size_bytes": self.size_bytes}


class ImmutableArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, ref: str) -> Path:
        candidate = (self.root / Path(ref.replace("/", str(Path('/'))))).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"artifact path escapes root: {ref}")
        return candidate

    def write_bytes(self, ref: str, payload: bytes) -> ArtifactRef:
        path = self.resolve(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
        return ArtifactRef(path, path.relative_to(self.root).as_posix(), sha256_bytes(payload), len(payload))

    def write_json(self, ref: str, value: Any) -> ArtifactRef:
        return self.write_bytes(ref, canonical_json_bytes(value))

    def verify(self, ref: str, expected_sha256: str) -> bool:
        path = self.resolve(ref)
        return path.is_file() and sha256_file(path) == expected_sha256
