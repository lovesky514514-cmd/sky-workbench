"""Explainable, dependency-free resource preflight and runtime monitoring."""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import shutil
import time
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.scientific.schemas import ScientificPlan


GIB = 1024**3


class RiskLevel(StrEnum):
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class _RiskScore(IntEnum):
    SAFE = 0
    MODERATE = 1
    HIGH = 2
    CRITICAL = 3


def _windows_memory() -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.total_physical), int(status.available_physical)
    except (AttributeError, OSError):
        return None, None


def _posix_memory() -> tuple[int | None, int | None]:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        return values.get("MemTotal"), values.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        return None, None


def _cpu_model() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if str(value).strip():
                    return str(value).strip()
        except (OSError, ImportError):
            pass
    return platform.processor().strip() or platform.machine().strip() or "unavailable"


def _process_rss(pid: int) -> int | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_VM_READ = 0x0010

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
        )
        if not handle:
            return None
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return int(counters.working_set_size)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return None
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def hardware_profile(workspace: Path, process_ids: dict[str, int] | None = None) -> dict[str, Any]:
    total, available = _windows_memory() if os.name == "nt" else _posix_memory()
    disk = shutil.disk_usage(workspace)
    logical = os.cpu_count()
    process_memory: dict[str, Any] = {}
    for name, pid in (process_ids or {}).items():
        rss = _process_rss(pid)
        process_memory[name] = {
            "pid": pid,
            "rss_bytes": rss,
            "rss_gb": round(rss / GIB, 3) if rss is not None else None,
            "status": "available" if rss is not None else "unavailable",
        }
    return {
        "captured_at_epoch": round(time.time(), 3),
        "cpu": {
            "model": _cpu_model(),
            "physical_cores": None,
            "logical_threads": logical,
            "physical_cores_status": "unavailable",
        },
        "memory": {
            "total_bytes": total,
            "available_bytes": available,
            "total_gb": round(total / GIB, 3) if total else None,
            "available_gb": round(available / GIB, 3) if available is not None else None,
            "available_percent": round(available / total * 100, 2) if total and available is not None else None,
            "status": "available" if total and available is not None else "unavailable",
        },
        "disk": {
            "workspace": str(workspace.resolve()),
            "total_bytes": disk.total,
            "free_bytes": disk.free,
            "free_gb": round(disk.free / GIB, 3),
            "status": "available",
        },
        "gpu": {
            "model": None,
            "vram_total_bytes": None,
            "vram_available_bytes": None,
            "status": "unavailable",
            "detail": "No reliable dependency-free GPU/VRAM telemetry source is active",
        },
        "process_memory": process_memory,
        "os": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
    }


def _estimate_complexity(plan: ScientificPlan) -> dict[str, Any]:
    dimension = max((feature.dimension for feature in plan.geometry_features), default=2)
    mesh_level = plan.mesh_definition.level if plan.mesh_definition else "normal"
    mesh_factor = {"coarse": 0.5, "normal": 1.0, "fine": 4.0, "custom": 2.0}[mesh_level]
    feature_factor = max(1, len(plan.geometry_features) or len(plan.geometry))
    study_cases = max((study.case_count() for study in plan.study_definitions), default=1)
    transient_steps = max(
        (len(study.times) for study in plan.study_definitions if study.kind == "time_dependent"),
        default=1,
    )
    physics_count = max(1, len(plan.physics))
    base_dof = 45_000 if dimension == 2 else 850_000
    estimated_dof = int(base_dof * mesh_factor * feature_factor * physics_count)
    peak_bytes = int(0.8 * GIB + estimated_dof * 280 * min(study_cases, 8))
    return {
        "geometry_dimension": dimension,
        "mesh_level": mesh_level,
        "feature_count": feature_factor,
        "physics_count": physics_count,
        "sweep_case_count": study_cases,
        "transient_step_count": transient_steps,
        "estimated_dof": estimated_dof,
        "estimated_peak_memory_bytes": peak_bytes,
        "estimated_peak_memory_gb": round(peak_bytes / GIB, 3),
    }


def assess_risk(
    plan: ScientificPlan,
    profile: dict[str, Any],
    *,
    stage: str = "prebuild",
    mesh_elements: int | None = None,
    dof: int | None = None,
) -> dict[str, Any]:
    estimate = _estimate_complexity(plan)
    if dof is not None and dof >= 0:
        estimate["estimated_dof"] = dof
        estimate["estimated_peak_memory_bytes"] = int(0.8 * GIB + dof * 280)
        estimate["estimated_peak_memory_gb"] = round(estimate["estimated_peak_memory_bytes"] / GIB, 3)
    estimate["mesh_elements"] = mesh_elements

    reasons: list[str] = []
    suggestions: list[str] = []
    score = _RiskScore.SAFE
    memory = profile.get("memory", {})
    disk = profile.get("disk", {})
    available = memory.get("available_bytes")
    total = memory.get("total_bytes")
    free_disk = disk.get("free_bytes")
    expected = estimate["estimated_peak_memory_bytes"]

    if available is None or total is None:
        score = max(score, _RiskScore.MODERATE)
        reasons.append("RAM telemetry is unavailable; capacity cannot be verified")
    else:
        ratio = expected / max(int(available), 1)
        if ratio >= 1.0:
            score = _RiskScore.CRITICAL
            reasons.append("Estimated peak memory exceeds currently available RAM")
        elif ratio >= 0.7:
            score = max(score, _RiskScore.HIGH)
            reasons.append("Estimated peak memory uses at least 70% of available RAM")
        elif ratio >= 0.4:
            score = max(score, _RiskScore.MODERATE)
            reasons.append("Estimated peak memory uses at least 40% of available RAM")
    if estimate["estimated_dof"] >= 5_000_000:
        score = max(score, _RiskScore.CRITICAL)
        reasons.append("Estimated degrees of freedom are at least 5 million")
    elif estimate["estimated_dof"] >= 1_500_000:
        score = max(score, _RiskScore.HIGH)
        reasons.append("Estimated degrees of freedom are at least 1.5 million")
    if estimate["sweep_case_count"] >= 50:
        score = max(score, _RiskScore.HIGH)
        reasons.append("Parametric sweep contains at least 50 cases")
    elif estimate["sweep_case_count"] >= 10:
        score = max(score, _RiskScore.MODERATE)
        reasons.append("Parametric sweep contains at least 10 cases")
    if free_disk is not None and int(free_disk) < 1 * GIB:
        score = _RiskScore.CRITICAL
        reasons.append("Workspace disk has less than 1 GB free")
    elif free_disk is not None and int(free_disk) < 5 * GIB:
        score = max(score, _RiskScore.HIGH)
        reasons.append("Workspace disk has less than 5 GB free")

    if score >= _RiskScore.MODERATE:
        if estimate["mesh_level"] == "fine":
            suggestions.append("Use normal mesh or local refinement instead of a global fine mesh")
        if estimate["sweep_case_count"] > 1:
            suggestions.append("Reduce or batch parametric sweep cases")
        if estimate["geometry_dimension"] == 3:
            suggestions.append("Use symmetry or a justified 2D reduction when scientifically valid")
        suggestions.append("Close unrelated memory-intensive applications before RUN")
    if not reasons:
        reasons.append("Estimated demand is within the configured local resource envelope")

    level = RiskLevel(_RiskScore(score).name)
    override_required = level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    allowed = (
        not override_required
        or (level == RiskLevel.HIGH and plan.resource_policy.allow_high_risk)
        or (level == RiskLevel.CRITICAL and plan.resource_policy.allow_critical_risk)
    )
    return {
        "stage": stage,
        "risk_level": level.value,
        "allowed": allowed,
        "override_required": override_required,
        "override_present": allowed if override_required else False,
        "reasons": reasons,
        "suggestions": suggestions,
        "estimate": estimate,
        "profile": profile,
    }


def runtime_thresholds(profile: dict[str, Any], plan: ScientificPlan) -> dict[str, Any]:
    available_percent = profile.get("memory", {}).get("available_percent")
    disk_free_gb = profile.get("disk", {}).get("free_gb")
    events: list[dict[str, str]] = []
    if available_percent is not None:
        if available_percent < plan.resource_policy.critical_ram_available_percent:
            events.append({"severity": "critical", "reason": "Available RAM is below the critical threshold"})
        elif available_percent < plan.resource_policy.warning_ram_available_percent:
            events.append({"severity": "warning", "reason": "Available RAM is below the warning threshold"})
    if disk_free_gb is not None:
        if disk_free_gb < plan.resource_policy.critical_disk_free_gb:
            events.append({"severity": "critical", "reason": "Workspace disk is below the critical free-space threshold"})
        elif disk_free_gb < plan.resource_policy.warning_disk_free_gb:
            events.append({"severity": "warning", "reason": "Workspace disk is below the warning free-space threshold"})
    return {"events": events, "highest": "critical" if any(item["severity"] == "critical" for item in events) else "warning" if events else "safe"}


@dataclass
class ResourceMonitor:
    workspace: Path
    plan: ScientificPlan
    callback: Callable[[dict[str, Any]], Awaitable[None]]
    process_ids: Callable[[], dict[str, int]] = lambda: {}
    _task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="resource-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        interval = self.plan.resource_policy.monitor_interval_seconds
        while True:
            profile = await asyncio.to_thread(hardware_profile, self.workspace, self.process_ids())
            await self.callback({"profile": profile, "thresholds": runtime_thresholds(profile, self.plan)})
            await asyncio.sleep(interval)
