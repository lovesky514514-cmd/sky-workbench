"""Durable append-only scientific research trajectory records."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from .artifacts import ImmutableArtifactStore


def is_silent_physical_failure(execution: bool | None, numerical: bool | None, physical: bool | None) -> bool:
    return execution is True and numerical is True and physical is False


class TrajectoryRecorder:
    def __init__(self, path: Path, store: ImmutableArtifactStore) -> None:
        self.path = path
        self.store = store
        self._lock = RLock()

    def append(self, record: dict[str, Any]) -> None:
        observation = record.get("observation")
        if not isinstance(observation, dict):
            raise ValueError("trajectory observation is required")
        raw_ref = str(observation.get("raw_ref", ""))
        raw_sha256 = str(observation.get("raw_sha256", ""))
        if not raw_ref or not self.store.verify(raw_ref, raw_sha256):
            raise ValueError("raw hash linkage is invalid")
        required = ("case_id", "run_id", "attempt_id", "step_id")
        if any(not record.get(name) for name in required):
            raise ValueError("trajectory identity is incomplete")
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
