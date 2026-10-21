"""Typed, physics-preserving projection of raw observations."""

from __future__ import annotations

import copy
from typing import Any


class PhysicsObservationNormalizer:
    version = "physics-observation-v0.3"
    _preserved = (
        "values", "parameters", "units", "selections", "geometry", "topology",
        "solver", "warnings", "readback", "checks", "artifacts", "lifecycle",
        "model_identity", "support_status", "action", "authoritative_verdict",
    )

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        normalized = {name: copy.deepcopy(raw[name]) for name in self._preserved if name in raw}
        normalized["normalizer_version"] = self.version
        normalized["display_summary"] = self._display_summary(raw)
        normalized["summary_role"] = "display_only"
        return normalized

    @staticmethod
    def _display_summary(raw: dict[str, Any]) -> str:
        solver = raw.get("solver") if isinstance(raw.get("solver"), dict) else {}
        action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
        status = action.get("status") or solver.get("state") or raw.get("support_status") or "recorded"
        return f"Research observation: {status}. See immutable raw evidence for scientific details."
