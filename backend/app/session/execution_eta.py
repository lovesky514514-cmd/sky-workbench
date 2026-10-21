"""Conservative runtime ETA derived only from the append-only execution ledger."""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime
from typing import Any


_ACTIVE_STATES = {
    "PRECHECK",
    "BUILDING",
    "BUILD_VALIDATION",
    "SOLVING",
    "SOLUTION_VALIDATION",
    "VALIDATING",
    "EXPORTING",
    "REPORTING",
}
_IRREGULAR_STATES = {"REPAIRING", "REVALIDATING", "PAUSED", "CANCEL_REQUESTED"}
_TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELED", "INTERRUPTED", "VALIDATION_FAILED"}


def _coarse_seconds(seconds: float) -> int:
    """Round up so the UI never presents false second-level precision."""

    step = 5 if seconds < 120 else 30 if seconds < 600 else 60
    return int(math.ceil(max(0.0, seconds) / step) * step)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def estimate_remaining_time(
    store: Any,
    session_id: str,
    *,
    now: datetime | None = None,
    min_samples: int = 3,
) -> dict[str, Any]:
    """Estimate remaining time from successful same-workflow executions.

    No model output participates. Fewer than three comparable successful
    executions, repair/cancellation paths, or absent workflow identity return
    unknown instead of fabricating precision.
    """

    session = store.get(session_id)
    state = str(session.get("state") or "IDLE")
    workflow_id = str((session.get("plan") or {}).get("workflow_id") or "")
    base = {
        "schema_version": 1,
        "source": "execution_ledger",
        "workflow_id": workflow_id or None,
        "state": state,
        "status": "unknown",
        "estimated_remaining_seconds": None,
        "sample_count": 0,
        "basis": "insufficient_same_workflow_history",
        "precision": "unknown",
    }
    if state == "COMPLETED":
        return {**base, "status": "complete", "estimated_remaining_seconds": 0, "basis": "terminal_state"}
    if state in _TERMINAL_STATES or state in _IRREGULAR_STATES or state not in _ACTIVE_STATES or not workflow_id:
        return base

    with store._connect() as db:
        rows = db.execute(
            """SELECT sequence, session_id, event_type, payload_json, created_at
               FROM events
               WHERE event_type IN ('timeline.started', 'timeline.completed')
               ORDER BY sequence"""
        ).fetchall()

    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        item_id = str(payload.get("id") or "")
        if not item_id:
            continue
        item = items.setdefault(item_id, {"sequence": row["sequence"], "session_id": row["session_id"]})
        item["sequence"] = max(int(item["sequence"]), int(row["sequence"]))
        if row["event_type"] == "timeline.started":
            item["started"] = payload
            item["started_at"] = row["created_at"]
        else:
            item["completed"] = payload

    durations: list[int] = []
    active_started_at: datetime | None = None
    active_sequence = -1
    for item in items.values():
        started = item.get("started") or {}
        completed = item.get("completed") or {}
        if started.get("tool") != "runtime.execution_protocol":
            continue
        item_workflow = str((started.get("input_summary") or {}).get("workflow_id") or "")
        if item_workflow != workflow_id:
            continue
        duration_ms = completed.get("duration_ms")
        if completed.get("status") == "success" and isinstance(duration_ms, (int, float)) and duration_ms > 0:
            durations.append(int(duration_ms))
        if item.get("session_id") == session_id and not completed and int(item["sequence"]) > active_sequence:
            active_started_at = _parse_time(str(item.get("started_at") or ""))
            active_sequence = int(item["sequence"])

    base["sample_count"] = len(durations)
    if len(durations) < max(3, min_samples):
        return base

    baseline_seconds = float(statistics.median(durations)) / 1000.0
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    elapsed_seconds = max(0.0, (current - active_started_at).total_seconds()) if active_started_at else 0.0
    remaining = _coarse_seconds(max(0.0, baseline_seconds - elapsed_seconds))
    return {
        **base,
        "status": "estimated",
        "estimated_remaining_seconds": remaining,
        "basis": "same_workflow_successful_execution_median",
        "precision": "coarse",
        "historical_median_seconds": _coarse_seconds(baseline_seconds),
    }
