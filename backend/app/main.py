"""FastAPI application for the local COMSOL Scientific Workbench."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from httpx import HTTPError
from pydantic import BaseModel, Field

from app import __version__
from app.adapter.rc42_contract import project_session, project_event_batch, safe_error
from app.app_paths import app_paths
from app.agent.runtime import agent_runtime
from app.agent.intent_compiler import compile_intent
from app.agent.scientific_tools import scientific_tool_layer
from app.agent.tool_registry import tool_registry
from app.comsol.legacy_mcp import legacy_mcp_bridge
from app.comsol.java_bridge import java_bridge_client
from app.comsol.bridge_process import java_bridge_process_manager
from app.comsol.discovery import discover_comsol_java
from app.comsol.license_probe import probe_optional_modules
from app.config import FRONTEND_DIST, PROJECT_ROOT, WORKSPACE_ROOT, ensure_workspace, settings_store
from app.environment import detect_environment, installation_scan
from app.knowledge.retrieval import retriever
from app.knowledge.hybrid_rag import index_task_manager, semantic_index_task_manager
from app.knowledge.integrity import verify_knowledge_base
from app.knowledge.semantic_runtime import semantic_runtime
from app.models import (
    AgentMessageRequest,
    CancelRunRequest,
    CreateSessionRequest,
    KnowledgeRefreshRequest,
    KnowledgeSearchRequest,
    JavaBridgeParameterRequest,
    JavaBridgeStartRequest,
    JavaBridgeStudyRequest,
    IntentCompileRequest,
    LegacyActionRequest,
    ModelOperationRequest,
    PlanUpdateRequest,
    ProviderSettingsRequest,
    ExtensionToggleRequest,
    LiteratureTextRequest,
    LiteratureSourceRequest,
    ProviderTestRequest,
    RunCorrectionRequest,
    RunReviewRequest,
    RunBoundaryReviewRequest,
    RunBoundaryCorrectionRequest,
)
from app.providers.manager import provider_manager
from app.simulation_adapters import capabilities as simulation_capabilities, probe_engine
from app.script_workspace import ScriptRunManager
from app.services.projects import project_service
from app.services.legacy_migration import detect_legacy, handle_legacy
from app.services.model_manager import local_model_manager
from app.services.imported_mph import imported_mph_store
from app.services.literature_modeling import extract_text_candidate, extract_pdf_candidate, extract_pdf_candidate_deepseek, extract_text_candidate_deepseek, MAX_PDF_BYTES
from app.services.literature_sources import fetch_recent_source
from app.session.store import session_store
from app.session.execution_eta import estimate_remaining_time
from app.session.state_grounding import rebuild_context_snapshot
from app.skills.library import SkillLibrary
from app.scientific.modeling_engine import modeling_engine
from app.scientific.resource_guard import assess_risk, hardware_profile
from app.scientific.schemas import ScientificPlan
from app.validators.execution_verdict import verify_artifact_integrity


script_runs = ScriptRunManager(WORKSPACE_ROOT / "script_runs")


class ScriptRunRequest(BaseModel):
    engine: str
    source: str = Field(min_length=1, max_length=200_000)
    confirmed: bool = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_workspace()
    session_store.initialize()
    app.state.run_reconciliation = await agent_runtime.reconcile()
    app.state.kb_integrity = verify_knowledge_base(app_paths.kb_dir)
    try:
        yield
    finally:
        await script_runs.shutdown()
        await agent_runtime.shutdown()
        semantic_runtime.shutdown()
        if java_bridge_process_manager.status().get("running") or java_bridge_process_manager.status().get('state') == 'starting':
            await java_bridge_process_manager.stop()


app = FastAPI(
    title="COMSOL Scientific Workbench",
    version=__version__,
    lifespan=lifespan,
)


@app.middleware("http")
async def require_desktop_session_token(request: Request, call_next):
    """Protect every desktop Runtime API call from unrelated local web pages."""

    expected = os.getenv("COMSOL_WORKBENCH_SESSION_TOKEN", "")
    if expected and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        supplied = request.headers.get("X-Workbench-Token", "")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "Invalid Workbench desktop session token"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    integrity = getattr(app.state, "kb_integrity", None)
    if integrity is None:
        integrity = verify_knowledge_base(app_paths.kb_dir)
    return {
        "status": "ok",
        "version": __version__,
        "frontend_built": FRONTEND_DIST.exists(),
        "desktop_mode": app_paths.desktop_mode,
        "kb_integrity": integrity,
        "settings_migration": settings_store.migration_status(),
        "database_recovery": session_store.recovery_status,
    }


@app.post("/api/desktop/shutdown")
async def desktop_shutdown() -> dict[str, Any]:
    """Ask the owned Runtime to stop after returning an authenticated response."""

    if java_bridge_process_manager.status().get("running"):
        await java_bridge_process_manager.stop()
    callback = getattr(app.state, "request_shutdown", None)
    if callback is not None:
        asyncio.get_running_loop().call_later(0.15, callback)
    return {"status": "stopping", "java_bridge_stopped": True}


@app.get("/api/environment")
async def environment() -> dict[str, Any]:
    return await detect_environment()


@app.post("/api/environment/installations/scan")
async def start_installation_scan() -> dict[str, Any]:
    return await installation_scan.start()


@app.get("/api/environment/installations/scan")
async def installation_scan_status() -> dict[str, Any]:
    return installation_scan.status()


@app.get("/api/system/resources")
async def system_resources() -> dict[str, Any]:
    process_ids = {"scientific_runtime": os.getpid()}
    bridge_pid = java_bridge_process_manager.status().get("pid")
    if isinstance(bridge_pid, int):
        process_ids["java_bridge"] = bridge_pid
    return await asyncio.to_thread(hardware_profile, WORKSPACE_ROOT, process_ids)


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    matrix = modeling_engine.capability_matrix()
    # Historical acceptance is not current-session COMSOL attachment or a new
    # scientific verdict. Keep the distinction visible to external adapters.
    for workflow in ("heat_transfer_2d_stationary", "tds_2d_stationary_diffusion"):
        matrix[workflow] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False,
                            "reason": "Prior real COMSOL acceptance exists; current run requires its own evidence"}
    matrix["solid_mechanics_uniaxial_bar"] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False, "reason": "Prior uniaxial COMSOL acceptance; current run not certified"}
    matrix["electrochem_primary_current"] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False, "reason": "Prior Primary Current Distribution acceptance; current run not certified"}
    matrix["general_modeling_engine"] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False, "reason": "Prior modeling API acceptance; current run not certified"}
    matrix["lithium_ion_battery_baseline"] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False, "reason": "Prior battery baseline acceptance; current run not certified"}
    matrix["battery"] = matrix["lithium_ion_battery_baseline"]
    matrix["heat_tds_temperature_coupling"] = {"status": "HISTORICAL_ACCEPTANCE", "runtime_verified": False, "reason": "Prior Heat → TDS acceptance; current run not certified"}
    matrix["multiphysics_coupling"] = matrix["heat_tds_temperature_coupling"]
    matrix["live_interrupt"] = _live_interrupt_capability()
    for entry in matrix.values():
        entry.setdefault('runtime_verified', False)
    return {"capabilities": matrix}


def _live_interrupt_capability() -> dict[str, str]:
    root = PROJECT_ROOT / "vnext_next_acceptance"
    reverify = root / "reverification.json"
    try:
        payload = json.loads(reverify.read_text(encoding="utf-8"))
        live = root / "live_result.json"
        interrupt = root / "interrupt_result.json"
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        intact = (
            digest(live) == payload["source_live_result_sha256"]
            and digest(interrupt) == payload["source_interrupt_result_sha256"]
        )
        passed = bool(
            intact
            and payload["live_acceptance"]["passed"]
            and payload["interrupt_acceptance"]["passed"]
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        passed = False
    return {
        "status": "HISTORICAL_ACCEPTANCE" if passed else "UNVERIFIED",
        "runtime_verified": False,
        "reason": (
            "Real COMSOL TDS safe-boundary pause/correct/readback/resume acceptance with intact evidence"
            if passed else
            "Live COMSOL interrupt acceptance evidence is unavailable or does not verify"
        ),
    }


@app.post("/api/capabilities/probe")
async def probe_capabilities() -> dict[str, Any]:
    """Start a clean COMSOL process only after an explicit UI/API request."""

    return await asyncio.to_thread(probe_optional_modules)


@app.get("/api/sessions/{session_id}/resources")
async def session_resources(session_id: str) -> dict[str, Any]:
    try:
        session = session_store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    process_ids = {"scientific_runtime": os.getpid(), **tool_registry.process_ids(session_id)}
    bridge_pid = java_bridge_process_manager.status().get("pid")
    if isinstance(bridge_pid, int):
        process_ids["java_bridge"] = bridge_pid
    profile = await asyncio.to_thread(hardware_profile, WORKSPACE_ROOT, process_ids)
    plan = session.get("plan")
    return {
        "profile": profile,
        "assessment": assess_risk(ScientificPlan.model_validate(plan), profile) if plan else None,
    }


@app.post("/api/sessions/{session_id}/run/cancel")
async def cancel_run(session_id: str, request: CancelRunRequest) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return await agent_runtime.cancel_run(session_id, force=request.force)


@app.post("/api/sessions/{session_id}/run/pause")
async def pause_run(session_id: str) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await agent_runtime.pause_live_run(session_id)
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=safe_error('adapter_unavailable', location='Runtime')) from exc


@app.post("/api/sessions/{session_id}/run/correction")
async def correct_run(session_id: str, request: RunCorrectionRequest) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await agent_runtime.correct_live_run(session_id, request.model_dump())
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=safe_error('validation_failed', location='Runtime')) from exc


@app.post("/api/sessions/{session_id}/run/review")
async def request_run_review(session_id: str, request: RunReviewRequest) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await agent_runtime.request_live_parameter_review(session_id, request.model_dump())
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "review_unavailable",
                                                     "summary": "A current COMSOL readback is required before review."}) from exc


@app.post("/api/sessions/{session_id}/run/resume")
async def resume_run(session_id: str) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await agent_runtime.resume_live_run(session_id)
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=safe_error('adapter_unavailable', location='Runtime')) from exc


@app.post('/api/sessions/{session_id}/run/boundary-review')
async def request_boundary_review(session_id: str, request: RunBoundaryReviewRequest) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail='Session not found')
    try:
        return await agent_runtime.queue_live_boundary_review(session_id, request.model_dump())
    except (KeyError, RuntimeError, ValueError, HTTPError) as exc:
        raise HTTPException(status_code=409, detail={'code': 'boundary_review_unavailable',
            'summary': 'A current Heat run and real COMSOL boundary readback are required.'}) from exc


@app.post('/api/sessions/{session_id}/run/boundary-correction')
async def correct_boundary_feature(session_id: str, request: RunBoundaryCorrectionRequest) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail='Session not found')
    try:
        return await agent_runtime.correct_live_boundary(session_id, request.review_id)
    except (KeyError, RuntimeError, ValueError, HTTPError) as exc:
        raise HTTPException(status_code=409, detail={'code': 'boundary_correction_unavailable',
            'summary': 'The owned COMSOL boundary feature could not be safely changed and read back.'}) from exc


@app.get("/api/providers")
async def providers() -> dict[str, Any]:
    return {"providers": await provider_manager.detect(), "settings": settings_store.load()}


@app.get("/api/simulation-adapters")
async def simulation_adapters() -> dict[str, Any]:
    """Expose discovery without implying an untested adapter can execute."""
    return {"adapters": simulation_capabilities()}


@app.post("/api/simulation-adapters/{engine}/probe")
async def probe_simulation_adapter(engine: str) -> dict[str, Any]:
    """Explicit local handshake; it never opens a model or certifies a result."""
    try:
        return await probe_engine(engine)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown scientific engine") from exc


@app.get("/api/script-runs/availability")
async def script_run_availability() -> dict[str, Any]:
    """An available interpreter is not a scientific connector or model readback."""
    return {"engines": await asyncio.to_thread(script_runs.availability)}


@app.post("/api/script-runs")
async def start_script_run(request: ScriptRunRequest) -> dict[str, Any]:
    # Local code execution is available only behind the desktop session token.
    # The confirmation flag records explicit user intent; it is not authentication.
    if not os.getenv("COMSOL_WORKBENCH_SESSION_TOKEN"):
        raise HTTPException(status_code=403, detail="Desktop session authentication is required")
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="Local script execution requires confirmation")
    try:
        return await script_runs.start(request.engine, request.source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/script-runs/{run_id}")
async def script_run_status(run_id: str) -> dict[str, Any]:
    try:
        return script_runs.status(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Local script run not found") from exc


@app.post("/api/script-runs/{run_id}/cancel")
async def cancel_script_run(run_id: str) -> dict[str, Any]:
    try:
        return await script_runs.cancel(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Local script run not found") from exc


@app.get("/api/script-runs/{run_id}/figures/{name}")
async def script_run_figure(run_id: str, name: str) -> Response:
    try:
        mime, data = script_runs.figure(run_id, name)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Local figure unavailable") from exc
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store"})


@app.put("/api/settings/providers")
async def update_providers(request: ProviderSettingsRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {"providers": request.providers}
    if request.retrieval_mode:
        payload["retrieval_mode"] = request.retrieval_mode
    if request.active_provider is not None:
        payload["active_provider"] = request.active_provider
    if request.comsol_root is not None:
        root = request.comsol_root.strip()
        if root and (root.startswith("\\\\") or not Path(root).is_absolute() or not Path(root).is_dir()):
            raise HTTPException(status_code=422, detail="COMSOL root must be an existing local absolute directory")
        payload["comsol"] = {"root": root}
    try:
        settings = settings_store.update(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"settings": settings, "providers": await provider_manager.detect()}


@app.get("/api/settings/migration")
async def settings_migration() -> dict[str, Any]:
    settings_store.load()
    return settings_store.migration_status()


@app.get("/api/extensions/literature-modeling")
async def literature_modeling_status() -> dict[str, bool]:
    settings = settings_store.load()["extensions"]["literature_modeling"]
    return {"enabled": bool(settings["enabled"]), "deepseek_enabled": bool(settings["deepseek_enabled"]),
            "deepseek_available": bool(settings_store.secret("deepseek"))}


@app.put("/api/extensions/literature-modeling")
async def set_literature_modeling(request: ExtensionToggleRequest) -> dict[str, bool]:
    """Persist the opt-in switch; this grants extraction, never execution."""
    settings = settings_store.update({"extensions": {"literature_modeling": {"enabled": request.enabled}}})
    return {"enabled": bool(settings["extensions"]["literature_modeling"]["enabled"])}


@app.put("/api/extensions/literature-modeling/deepseek")
async def set_literature_deepseek(request: ExtensionToggleRequest) -> dict[str, bool]:
    """Opt in to sending bounded extracted text to DeepSeek, not raw PDFs."""
    settings_store.update({"extensions": {"literature_modeling": {"deepseek_enabled": request.enabled}}})
    return await literature_modeling_status()


def require_literature_modeling() -> None:
    if not settings_store.load()["extensions"]["literature_modeling"]["enabled"]:
        raise HTTPException(status_code=403, detail="Literature modeling extension is disabled")


def literature_deepseek_key() -> str:
    key = settings_store.secret("deepseek")
    if not key:
        raise HTTPException(status_code=503, detail="deepseek_key_unavailable")
    return key


@app.post("/api/extensions/literature-modeling/text")
async def literature_modeling_text(request: LiteratureTextRequest) -> dict[str, Any]:
    require_literature_modeling()
    try:
        return extract_text_candidate(request.source_title, request.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/extensions/literature-modeling/pdf")
async def literature_modeling_pdf(request: Request, x_workbench_filename: str = Header(default="")) -> dict[str, Any]:
    require_literature_modeling()
    cloud_enabled = bool(settings_store.load()["extensions"]["literature_modeling"]["deepseek_enabled"])
    key = literature_deepseek_key() if cloud_enabled else ""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="Literature PDF exceeds the 15 MiB limit")
    try:
        filename = unquote(x_workbench_filename)
        if cloud_enabled:
            return await extract_pdf_candidate_deepseek(filename, bytes(data), api_key=key)
        return await asyncio.to_thread(extract_pdf_candidate, filename, bytes(data))
    except ValueError as exc:
        code = 503 if str(exc).startswith("deepseek_") else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.post("/api/extensions/literature-modeling/source")
async def literature_modeling_source(request: LiteratureSourceRequest) -> dict[str, Any]:
    """Only arXiv/PMC article abstracts published within rolling three years."""
    require_literature_modeling()
    cloud_enabled = bool(settings_store.load()["extensions"]["literature_modeling"]["deepseek_enabled"])
    key = literature_deepseek_key() if cloud_enabled else ""
    try:
        source = await fetch_recent_source(request.url)
        if cloud_enabled:
            candidate = await extract_text_candidate_deepseek(source["source_title"], source["text"], api_key=key, source_kind=source["source_kind"])
        else:
            candidate = extract_text_candidate(source["source_title"], source["text"])
            candidate["source_kind"] = source["source_kind"]
        candidate["source_url"] = source["source_url"]
        candidate["publication_date"] = source["publication_date"]
        return candidate
    except ValueError as exc:
        code = 503 if str(exc).startswith("deepseek_") or str(exc) == "source_fetch_failed" else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.post("/api/settings/migration/retry")
async def retry_settings_migration() -> dict[str, Any]:
    return settings_store.retry_migration()


@app.post("/api/settings/migration/defaults")
async def use_default_settings() -> dict[str, Any]:
    settings_store.use_defaults()
    return settings_store.migration_status()


@app.post("/api/storage/clear/{kind}")
async def clear_rebuildable_storage(kind: str) -> dict[str, Any]:
    """Clear only application-owned, rebuildable data after an explicit UI action."""

    if kind == "semantic-index":
        return await asyncio.to_thread(retriever.store.clear_semantic_index)
    if kind != "temporary-cache":
        raise HTTPException(status_code=404, detail="Unknown rebuildable storage kind")

    def clear_cache() -> dict[str, Any]:
        root = app_paths.cache_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        removed = 0
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        return {"status": "cleared", "removed_entries": removed, "path": str(root)}

    return await asyncio.to_thread(clear_cache)


@app.get("/api/projects")
async def projects() -> dict[str, Any]:
    return {"projects": project_service.projects()}


@app.post("/api/projects/{project_id}/select")
async def select_project(project_id: str) -> dict[str, Any]:
    try:
        return project_service.select(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


@app.get("/api/projects/{project_id}/tree")
async def project_tree(project_id: str, depth: int = 3) -> dict[str, Any]:
    try:
        return {"project_id": project_id, "tree": project_service.tree(project_id, min(depth, 5))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


@app.get("/api/sessions")
async def sessions() -> dict[str, Any]:
    return {"sessions": session_store.list()}


@app.post("/api/sessions")
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    return session_store.create(request.project_id, request.title)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    try:
        return session_store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str) -> dict[str, Any]:
    try:
        return session_store.archive(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=safe_error('validation_failed', location='Runtime')) from exc


@app.get("/api/sessions/{session_id}/execution/eta")
async def execution_eta(session_id: str) -> dict[str, Any]:
    """Return a coarse ledger-only ETA or an explicit unknown result."""

    try:
        return estimate_remaining_time(session_store, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.get("/api/rc42/sessions/{session_id}/domain")
async def rc42_domain_projection(session_id: str) -> dict[str, Any]:
    """Fail-closed Greenfield domain projection; no UI code is loaded here."""
    try:
        projection = project_session(session_store, session_id)
        if projection['runState'] is not None:
            projection['runState']['pauseCapability'] = await agent_runtime.pause_capability(session_id)
        return projection
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.get("/api/rc42/sessions/{session_id}/events")
async def rc42_domain_events(session_id: str, after: int = 0) -> dict[str, Any]:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return project_event_batch(session_store, session_id, after)


@app.get("/api/sessions/{session_id}/context-snapshot")
async def context_snapshot(session_id: str) -> dict[str, Any]:
    """Rebuild bounded authoritative context without replaying chat messages."""
    try:
        return rebuild_context_snapshot(session_store, session_id).as_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.get("/api/vnext/skills")
async def vnext_skills(workflow_id: str | None = None) -> dict[str, Any]:
    library = SkillLibrary.default()
    cards = library.route(workflow_id) if workflow_id else library.cards
    return {"status": "advisory_only", "cards": [card.model_dump(mode="json") for card in cards]}


@app.get("/api/vnext/3d-contracts")
async def vnext_3d_contracts() -> dict[str, Any]:
    # The source tree and one-file Runtime resolve resources differently.
    # Serve the unchanged preparation-only contract from the selected resource root.
    contract = app_paths.resource_dir / "vnext" / "3d" / "minimal_3d_contracts.json"
    return json.loads(contract.read_text(encoding="utf-8"))


@app.get("/api/comsol/diagnostics")
async def comsol_diagnostics() -> dict[str, Any]:
    process = java_bridge_process_manager.status()
    live: dict[str, Any] = {"state": "not_attached", "model_connected": False}
    if process.get("state") == "ready":
        try:
            live = await java_bridge_client.health()
        except (OSError, RuntimeError, TimeoutError) as exc:
            live = {"state": "attachment_probe_failed", "model_connected": False, "detail": str(exc)}
    return {
        "installation_discovery": discover_comsol_java().as_dict(),
        "bridge_process": process,
        "live_attachment": live,
        "identity_verified": bool(live.get("real_comsol") and live.get("model_connected")),
    }


@app.get("/api/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if not session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        resumed = int(last_event_id) if last_event_id else 0
    except ValueError:
        resumed = 0

    async def events():
        cursor = max(0, after, resumed)
        heartbeat = 0
        while True:
            batch = session_store.events_after(session_id, cursor)
            for event in batch:
                cursor = int(event["sequence"])
                payload = {"type": "session_event", **event}
                yield f"id: {cursor}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            heartbeat += 1
            if heartbeat % 40 == 0:
                yield f": heartbeat {cursor}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{session_id}/messages")
async def send_message(session_id: str, request: AgentMessageRequest) -> dict[str, Any]:
    try:
        return await agent_runtime.handle_message(session_id, request.mode, request.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/api/intents/compile")
async def compile_scientific_intent(request: IntentCompileRequest) -> dict[str, Any]:
    return compile_intent(request.content).model_dump(mode="json")


@app.get("/api/maintenance/legacy")
async def legacy_status(path: str | None = None) -> dict[str, Any]:
    return detect_legacy(path)


@app.post("/api/maintenance/legacy")
async def legacy_action(request: LegacyActionRequest) -> dict[str, Any]:
    try:
        return handle_legacy(request.action, request.path, confirmed=request.confirmed, backup_root=request.backup_root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/models/status")
async def local_models_status() -> dict[str, Any]:
    return {"ollama": await local_model_manager.status(), "semantic": local_model_manager.semantic_pack()}


@app.post("/api/models/ollama/start")
async def start_ollama() -> dict[str, Any]:
    return await local_model_manager.start()


@app.post("/api/models/ollama/pull")
async def pull_ollama_model(request: ModelOperationRequest) -> dict[str, Any]:
    try:
        return await local_model_manager.pull(request.model, confirmed=request.confirmed)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/models/ollama/pull/cancel")
async def cancel_ollama_pull() -> dict[str, Any]:
    return await local_model_manager.cancel_pull()


@app.post("/api/models/ollama/remove")
async def remove_ollama_model(request: ModelOperationRequest) -> dict[str, Any]:
    try:
        return await local_model_manager.remove(request.model, confirmed=request.confirmed)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/messages/stream")
async def stream_message(session_id: str, request: AgentMessageRequest) -> StreamingResponse:
    async def events():
        try:
            async for event in agent_runtime.handle_message_stream(session_id, request.mode, request.content):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except KeyError:
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Session not found'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.put("/api/sessions/{session_id}/plan")
async def update_plan(session_id: str, request: PlanUpdateRequest) -> dict[str, Any]:
    try:
        return await agent_runtime.update_plan(session_id, request.plan)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/knowledge/status")
async def knowledge_status() -> dict[str, Any]:
    return {
        **retriever.status(),
        "index_task": index_task_manager.status(),
        "semantic_index_task": semantic_index_task_manager.status(),
    }


@app.post("/api/knowledge/search")
async def knowledge_search(request: KnowledgeSearchRequest) -> dict[str, Any]:
    return retriever.search_detailed(
        request.query,
        limit=request.limit,
        mode=request.mode,
        filters=request.filters,
    )


@app.post("/api/knowledge/refresh")
async def knowledge_refresh(request: KnowledgeRefreshRequest) -> dict[str, Any]:
    task = await index_task_manager.start()
    return {"accepted": True, "include_legacy": request.include_legacy, "index_task": task}


@app.get("/api/knowledge/index/status")
async def knowledge_index_status() -> dict[str, Any]:
    return {
        "index_task": index_task_manager.status(),
        "semantic_index_task": semantic_index_task_manager.status(),
        "knowledge": retriever.status(),
    }


@app.post("/api/knowledge/semantic/verify")
async def verify_semantic_runtime() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(semantic_runtime.verify)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/knowledge/semantic/index/start")
async def start_semantic_index() -> dict[str, Any]:
    return {"accepted": True, "semantic_index_task": await semantic_index_task_manager.start()}


@app.get("/api/knowledge/events")
async def knowledge_events(after: int = 0) -> StreamingResponse:
    """Resumable bounded SSE stream for long-running index events."""

    async def events():
        cursor = max(0, after)
        heartbeat = 0
        while True:
            batch = index_task_manager.events_after(cursor)
            for event in batch:
                cursor = int(event["sequence"])
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            status = index_task_manager.status()
            if not status["running"] and cursor >= int(status["latest_sequence"]):
                break
            heartbeat += 1
            if heartbeat % 20 == 0:
                yield f": heartbeat {cursor}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/knowledge/semantic/events")
async def semantic_knowledge_events(after: int = 0) -> StreamingResponse:
    """Resumable progress stream for the optional neural index."""

    async def events():
        cursor = max(0, after)
        heartbeat = 0
        while True:
            batch = semantic_index_task_manager.events_after(cursor)
            for event in batch:
                cursor = int(event["sequence"])
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            status = semantic_index_task_manager.status()
            if not status["running"] and cursor >= int(status["latest_sequence"]):
                break
            heartbeat += 1
            if heartbeat % 20 == 0:
                yield f": heartbeat {cursor}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/providers/test")
async def provider_test(request: ProviderTestRequest) -> dict[str, Any]:
    try:
        return await provider_manager.test_connection(request.provider)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Provider not found") from exc


@app.post("/api/providers/acceptance")
async def provider_acceptance(request: ProviderTestRequest) -> dict[str, Any]:
    try:
        return await provider_manager.acceptance_test(request.provider)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Provider not found") from exc


@app.get("/api/tools")
async def tools() -> dict[str, Any]:
    return {"tools": tool_registry.catalog()}


@app.get("/api/scientific-tools")
async def scientific_tools() -> dict[str, Any]:
    return {"tools": scientific_tool_layer.catalog(), "count": len(scientific_tool_layer.catalog())}


@app.get("/api/artifacts/{run_id}/{filename}")
async def run_artifact(run_id: str, filename: str) -> FileResponse:
    """Serve a narrow allowlist of generated Result Viewer artifacts."""

    import re

    allowed = {
        "concentration.png",
        "temperature.png",
        "temperature_field.csv",
        "concentration_profile.csv",
        "solid_mises.png",
        "displacement_profile.csv",
        "electrolyte_potential.png",
        "electrolyte_profile.csv",
        "battery_electrolyte_concentration.png",
        "battery_discharge.csv",
        "coupled_concentration.png",
        "coupled_temperature.png",
        "coupled_concentration_profile.csv",
    }
    if re.fullmatch(r"[A-Za-z0-9-]{1,80}", run_id) is None or filename not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not found")
    root = (WORKSPACE_ROOT / "outputs" / run_id).resolve()
    candidate = (root / filename).resolve()
    try:
        run = session_store.get_run(run_id)
    except KeyError:
        run = None
    if run:
        # Journal manifest selects the final attempt. File existence is not success.
        if run['outcome'] != 'Completed':
            raise HTTPException(status_code=404, detail='No completed artifact manifest')
        selected = [Path(value).resolve() for value in run['artifacts'].values()
                    if isinstance(value, str) and Path(value).name == filename]
        if len(selected) != 1: raise HTTPException(status_code=404, detail='Artifact not in manifest')
        root = (WORKSPACE_ROOT / 'models' / 'attempts' / run_id / str(run['attempt_id'])).resolve()
        candidate = selected[0]
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    if run:
        evidence = session_store.completed_evidence(run_id)
        records = evidence.get('artifacts', {}) if evidence else {}
        expected = next(
            (
                record for record in records.values()
                if isinstance(record, dict)
                and isinstance(record.get('path'), str)
                and Path(record['path']).resolve() == candidate
            ),
            None,
        )
        if expected is None:
            raise HTTPException(status_code=404, detail='No completed artifact evidence')
        integrity = verify_artifact_integrity(candidate, expected)
        event_type = 'artifact.integrity_verified' if integrity['passed'] else 'artifact.integrity_failed'
        session_store.append_event(
            run['session_id'], event_type,
            {'run_id': run_id, 'attempt_id': run['attempt_id'], 'filename': filename, **integrity},
        )
        if not integrity['passed']:
            raise HTTPException(status_code=409, detail={
                'code': 'artifact_corrupt', 'title': 'Artifact integrity lost',
                'summary': 'The result no longer matches its frozen record.',
                'diagnosticId': f'artifact-{run_id}-{filename}',
            })
    return FileResponse(candidate)


@app.get("/api/mcp/status")
async def mcp_status(include_tools: bool = False) -> dict[str, Any]:
    return await legacy_mcp_bridge.status(include_tools=include_tools)


@app.get("/api/comsol/bridge/status")
async def java_bridge_status() -> dict[str, Any]:
    health = await java_bridge_client.health()
    return {
        **health,
        "process": java_bridge_process_manager.status(),
        "environment": discover_comsol_java().as_dict(),
    }


@app.get("/api/comsol/bridge/environment")
async def java_bridge_environment() -> dict[str, Any]:
    return discover_comsol_java().as_dict()


@app.get("/api/imported-mph")
async def list_imported_mph() -> dict[str, Any]:
    """List imported user models separately from Workbench-generated runs."""
    return {"items": imported_mph_store.list()}


@app.post("/api/imported-mph", status_code=201)
async def import_mph(request: Request, x_workbench_filename: str = Header(default="")) -> dict[str, Any]:
    """Stream raw MPH bytes; the original is immutable to Bridge operations."""
    try:
        return await imported_mph_store.save(unquote(x_workbench_filename), request.stream())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/imported-mph/{item_id}/preview", status_code=202)
async def start_imported_mph_preview(item_id: str) -> dict[str, Any]:
    """Load only the working copy, without interrupting another owned model."""
    try:
        working = imported_mph_store.working_path(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Imported MPH unavailable") from exc
    status = java_bridge_process_manager.status()
    if (status.get("running") or status.get("state") == "starting") and Path(str(status.get("model_path", ""))).resolve() != working:
        raise HTTPException(status_code=409, detail="Another COMSOL model currently owns the Bridge")
    try:
        process = await java_bridge_process_manager.start(str(working))
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=503, detail=f"COMSOL preview unavailable: {type(exc).__name__}") from exc
    return {"preview_state": "starting", "bridge_state": process.get("state"), "scientific_validation_passed": False}


@app.get("/api/imported-mph/{item_id}/preview")
async def read_imported_mph_preview(item_id: str) -> dict[str, Any]:
    """A preview is verified only by a live COMSOL ModelUtil tree readback."""
    try:
        working = imported_mph_store.working_path(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Imported MPH unavailable") from exc
    process = java_bridge_process_manager.status()
    common = {"model_state_verified": False, "scientific_validation_passed": False}
    if Path(str(process.get("model_path", ""))).resolve() != working:
        return {**common, "preview_state": "unavailable", "reason": "model_not_loaded"}
    if process.get("state") != "ready" or not process.get("running"):
        return {**common, "preview_state": "starting" if process.get("state") == "starting" else "unavailable", "reason": str(process.get("state", "unavailable"))}
    health = await java_bridge_client.health()
    if not (health.get("real_comsol") and health.get("connected") and health.get("model_connected")):
        return {**common, "preview_state": "unavailable", "reason": "live_comsol_unavailable"}
    try:
        tree = await java_bridge_client.model_tree()
    except Exception:
        return {**common, "preview_state": "unavailable", "reason": "model_tree_readback_failed"}
    if not (tree.get("command_success") and tree.get("model_state_verified")):
        return {**common, "preview_state": "unavailable", "reason": "model_tree_not_verified"}
    return {"preview_state": "verified", "model_state_verified": True, "scientific_validation_passed": False,
            "comsol_version": health.get("comsol_version"), "model_tree": tree}


@app.post("/api/comsol/bridge/start", status_code=202)
async def start_java_bridge(request: JavaBridgeStartRequest) -> dict[str, Any]:
    try:
        return await java_bridge_process_manager.start(request.model_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/comsol/bridge/stop")
async def stop_java_bridge() -> dict[str, Any]:
    return await java_bridge_process_manager.stop()


@app.get("/api/comsol/bridge/model-tree")
async def java_bridge_model_tree(model_tag: str | None = None) -> dict[str, Any]:
    try:
        return await java_bridge_client.model_tree(model_tag)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Java Bridge model readback unavailable: {type(exc).__name__}: {exc}") from exc


@app.get("/api/comsol/bridge/parameters")
async def java_bridge_parameters() -> dict[str, Any]:
    try:
        return await java_bridge_client.parameters()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Java Bridge parameter readback unavailable: {type(exc).__name__}: {exc}") from exc


def reject_direct_bridge_mutation() -> None:
    """Raw Bridge writes cannot bypass ScientificPlan admission and run evidence.

    This deliberately applies in development and packaged builds alike. The
    guarded Runtime execution and review/correction flows call the in-process
    JavaBridgeClient; they do not use these HTTP compatibility endpoints.
    """
    raise HTTPException(
        status_code=403,
        detail=(
            "scientific_plan_required: Direct COMSOL mutations, including "
            "wb_probe_ parameters, are disabled. Use a registered "
            "ScientificPlan RUN or the verified session review/correction flow."
        ),
    )


@app.post("/api/comsol/bridge/parameter", include_in_schema=False)
async def java_bridge_set_parameter(request: JavaBridgeParameterRequest) -> dict[str, Any]:
    reject_direct_bridge_mutation()


@app.delete("/api/comsol/bridge/parameter/{name}", include_in_schema=False)
async def java_bridge_remove_parameter(name: str) -> dict[str, Any]:
    reject_direct_bridge_mutation()


@app.post("/api/comsol/bridge/study/run", include_in_schema=False)
async def java_bridge_run_study(request: JavaBridgeStudyRequest) -> dict[str, Any]:
    reject_direct_bridge_mutation()


@app.get("/api/comsol/bridge/events")
async def java_bridge_events(after: int = 0) -> dict[str, Any]:
    try:
        return await java_bridge_client.events(after)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Java Bridge events unavailable: {type(exc).__name__}: {exc}") from exc


@app.get("/api/comsol/bridge/stream")
async def java_bridge_stream(after: int = 0, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    cursor = max(after, int(last_event_id or 0) if str(last_event_id or "").isdigit() else 0)

    async def events():
        nonlocal cursor
        last_keepalive = asyncio.get_running_loop().time()
        unavailable_reported = False
        while True:
            try:
                payload = await java_bridge_client.events(cursor)
                latest_sequence = int(payload.get("latest_sequence", cursor))
                if latest_sequence < cursor:
                    cursor = 0
                    payload = await java_bridge_client.events(0)
                for event in payload.get("events", []):
                    cursor = max(cursor, int(event.get("sequence", cursor)))
                    unavailable_reported = False
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:
                if not unavailable_reported:
                    unavailable_reported = True
                    event = {
                        "sequence": cursor,
                        "event_type": "critical_failure",
                        "phase": "bridge",
                        "status": "unavailable",
                        "detail": f"Java Bridge unavailable: {type(exc).__name__}: {exc}",
                        "command_success": False,
                        "model_state_verified": False,
                        "scientific_validation_passed": False,
                    }
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            now = asyncio.get_running_loop().time()
            if now - last_keepalive >= 10:
                yield ": keepalive\n\n"
                last_keepalive = now
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        candidate = (FRONTEND_DIST / path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            candidate = FRONTEND_DIST / "index.html"
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
