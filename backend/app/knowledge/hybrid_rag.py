"""Scientific Hybrid RAG over the audited hot KB plus project evidence."""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from app.config import settings_store
from app.knowledge.compiled_store import DEFAULT_NAMESPACE, CompiledKnowledgeStore, compiled_store
from app.knowledge.embedding_providers import retrieval_model_registry
from app.knowledge.semantic_runtime import EMBEDDING_NAMESPACE, semantic_runtime


RRF_K = 60
MODE_ALIASES = {
    "basic": "fast",
    "enhanced": "accurate",
    "fast": "fast",
    "accurate": "accurate",
    "deep": "deep",
    "deep_research": "deep",
}
DOMAIN_ALIASES = {
    "java api": "model component geom feature create selection set getType Java API",
    "heat transfer": "Heat Transfer in Solids thermal insulation temperature heat flux",
    "thermal insulation": "Thermal Insulation no heat flux boundary",
    "solid mechanics": "Solid Mechanics solid stress strain boundary load fixed constraint",
    "transport of diluted species": "Transport of Diluted Species concentration flux diffusion",
    "tds": "Transport of Diluted Species concentration diffusion",
    "solver": "solver convergence stationary time dependent segregated fully coupled",
    "mesh": "mesh element quality refinement physics controlled mesh",
    "validation": "validation convergence mesh independence verification result",
    "troubleshooting": "troubleshooting error warning failed convergence license",
    "version": "version release notes legacy current applies to versions",
    "绝热": "Thermal Insulation no heat flux boundary",
    "传热": "Heat Transfer in Solids temperature heat flux",
    "固体力学": "Solid Mechanics stress strain boundary load",
    "稀物质": "Transport of Diluted Species concentration diffusion",
    "网格": "mesh element quality refinement",
    "求解器": "solver convergence stationary time dependent",
    "验证": "validation verification convergence mesh independence",
    "故障": "troubleshooting error warning failed convergence",
}


@dataclass(slots=True)
class RetrievalTrace:
    mode: str
    queries: list[str]
    filters: dict[str, Any] = field(default_factory=dict)
    bm25_candidates: int = 0
    dense_candidates: int = 0
    fused_candidates: int = 0
    reranker_status: str = "unavailable"
    reranker_applied: bool = False
    dense_backend: str = "unavailable"
    fallback_used: bool = False
    bm25_latency_ms: float = 0.0
    embedding_latency_ms: float = 0.0
    dense_latency_ms: float = 0.0
    reranker_latency_ms: float = 0.0
    latency_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "queries": self.queries,
            "filters": self.filters,
            "bm25_candidates": self.bm25_candidates,
            "dense_candidates": self.dense_candidates,
            "fused_candidates": self.fused_candidates,
            "reranker_status": self.reranker_status,
            "reranker_applied": self.reranker_applied,
            "dense_backend": self.dense_backend,
            "fallback_used": self.fallback_used,
            "bm25_latency_ms": round(self.bm25_latency_ms, 3),
            "embedding_latency_ms": round(self.embedding_latency_ms, 3),
            "dense_latency_ms": round(self.dense_latency_ms, 3),
            "reranker_latency_ms": round(self.reranker_latency_ms, 3),
            "latency_ms": round(self.latency_ms, 3),
            "warnings": self.warnings,
        }


class ScientificRAGService:
    """Route Fast/Accurate/Deep retrieval without changing legacy project ingestion."""

    def __init__(self, legacy: Any, store: CompiledKnowledgeStore = compiled_store) -> None:
        self.legacy = legacy
        self.store = store

    def status(self) -> dict[str, Any]:
        compiled = self.store.status()
        legacy = self.legacy.status()
        models = retrieval_model_registry.status()
        mode = self.active_mode()
        compiled_ready = bool(compiled["database_ready"])
        project_chunks = int(legacy.get("basic_chunks", 0))
        # An absent private KB and an empty project index provide no retrieval
        # evidence. Keep project-only fallback distinct from complete absence.
        availability = "Ready" if compiled_ready else "Warning" if project_chunks else "Unavailable"
        return {
            "status": availability,
            "active_mode": mode,
            "requested_mode": settings_store.load().get("retrieval_mode", "accurate"),
            "basic_chunks": project_chunks,
            "compiled_units": int(compiled["knowledge_units"]),
            "indexed_units": int(compiled["indexed_units"]),
            "documents": int(compiled["documents"]),
            "database_ready": compiled_ready,
            "embedding_backend": models["active_embedding"],
            "reranker_backend": models["active_reranker"],
            "models": models,
            "compiled_kb": compiled,
            "legacy_project_index": legacy,
            "readonly": True,
            "detail": (
                "Audited compiled Knowledge Units are the hot evidence layer; project/literature chunks remain a secondary source."
                if compiled_ready
                else "Compiled KB unavailable; retrieval uses only the project index."
                if project_chunks
                else "Compiled KB and project index unavailable; no retrieval evidence is loaded."
            ),
            "ingestion_issues": list(legacy.get("ingestion_issues", [])),
            "index_path": compiled["index_path"],
        }

    def active_mode(self) -> str:
        settings = settings_store.load()
        raw = str(settings.get("retrieval_mode") or settings.get("rag", {}).get("mode") or "accurate")
        return MODE_ALIASES.get(raw.lower(), "accurate")

    def refresh(self, *, include_legacy: bool = True) -> dict[str, Any]:
        legacy = self.legacy.refresh(include_legacy=include_legacy)
        build = self.store.build_index(namespace=DEFAULT_NAMESPACE)
        return {"legacy": legacy, "compiled": build, **self.status()}

    def build_compiled_index(self, progress: Any = None) -> dict[str, Any]:
        return self.store.build_index(namespace=DEFAULT_NAMESPACE, progress=progress)

    def build_semantic_index(self, progress: Any = None) -> dict[str, Any]:
        index_parent = self.store.db_path.parent
        index_parent.mkdir(parents=True, exist_ok=True)
        existing_size = self.store.db_path.stat().st_size if self.store.db_path.is_file() else 0
        required_free = max(1024**3, existing_size * 2)
        free = shutil.disk_usage(index_parent).free
        if free < required_free:
            raise RuntimeError(
                f"Semantic index requires at least {required_free} free bytes; only {free} bytes are available"
            )
        verification = semantic_runtime.verify()
        if not verification.get("passed"):
            raise RuntimeError("Real semantic inference verification did not pass")
        return self.store.build_neural_index(
            lambda texts: semantic_runtime.embed(texts, query=False),
            namespace=EMBEDDING_NAMESPACE,
            dimensions=1024,
            batch_size=8,
            progress=progress,
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        mode: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.search_detailed(query, limit=limit, mode=mode, filters=filters)["results"]

    def search_detailed(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str | None = None,
        filters: dict[str, Any] | None = None,
        use_neural: bool | None = None,
        use_reranker: bool | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        normalized_mode = MODE_ALIASES.get(str(mode or self.active_mode()).lower(), "accurate")
        queries = decompose_query(query) if normalized_mode == "deep" else [expand_scientific_query(query)]
        effective_filters = filters or route_metadata_filters(query)
        trace = RetrievalTrace(mode=normalized_mode, queries=queries, filters=effective_filters)
        compiled_status = self.store.status()
        if not compiled_status["database_ready"]:
            trace.fallback_used = True
            trace.warnings.append("compiled_kb_index_unavailable")
            legacy_results = self.legacy.search(query, limit)
            for item in legacy_results:
                item["retrieval_mode"] = f"{normalized_mode}:legacy-fallback"
                item["retrieval_path"] = {
                    "bm25": item.get("retrieval_mode") == "bm25",
                    "dense": "hybrid" in str(item.get("retrieval_mode")),
                    "rrf": False,
                    "reranker": False,
                    "evidence_builder": False,
                    "claim_verification": "unverified",
                }
            trace.latency_ms = (time.perf_counter() - started) * 1000
            return {"query": query, "mode": normalized_mode, "results": legacy_results, "trace": trace.as_dict()}

        models = retrieval_model_registry.status()
        fallback = models["fallback_embedding"]
        fallback_available = bool(fallback["status"] == "installed")
        runtime = models.get("semantic_runtime", {})
        neural_files = bool(runtime.get("runtime_available") and runtime.get("embedding", {}).get("files_present"))
        neural_index_ready = self.store.embedding_count(EMBEDDING_NAMESPACE) == int(compiled_status["knowledge_units"])
        trace.dense_backend = models["active_embedding"]
        trace.reranker_status = models["reranker"]["status"]

        rank_lists: list[tuple[str, list[dict[str, Any]]]] = []
        for query_variant in queries:
            phase_started = time.perf_counter()
            bm25 = self.store.bm25_search(query_variant, limit=max(80, limit * 12), filters=effective_filters)
            trace.bm25_latency_ms += (time.perf_counter() - phase_started) * 1000
            trace.bm25_candidates += len(bm25)
            rank_lists.append(("bm25", bm25))
            dense: list[dict[str, Any]] = []
            if use_neural is not False and neural_files and neural_index_ready:
                try:
                    phase_started = time.perf_counter()
                    query_vector = semantic_runtime.embed([query_variant], query=True)[0]
                    trace.embedding_latency_ms += (time.perf_counter() - phase_started) * 1000
                    phase_started = time.perf_counter()
                    dense = self.store.dense_search_vector(
                        query_vector,
                        limit=max(80, limit * 12),
                        filters=effective_filters,
                        namespace=EMBEDDING_NAMESPACE,
                    )
                    trace.dense_latency_ms += (time.perf_counter() - phase_started) * 1000
                    trace.dense_backend = models["embedding"]["model"]
                except Exception as exc:
                    trace.warnings.append(f"neural_embedding_error:{type(exc).__name__}:{exc}")
            if dense:
                trace.dense_candidates += len(dense)
                rank_lists.append(("dense_qwen3", dense))
            elif fallback_available:
                phase_started = time.perf_counter()
                fallback_dense = self.store.dense_search(query_variant, limit=max(80, limit * 12), filters=effective_filters)
                trace.dense_latency_ms += (time.perf_counter() - phase_started) * 1000
                trace.dense_candidates += len(fallback_dense)
                rank_lists.append(("dense_hash_emergency", fallback_dense))
                trace.dense_backend = fallback["model"]
                trace.fallback_used = True
        if not neural_index_ready:
            trace.warnings.append(
                f"neural_index_incomplete:{self.store.embedding_count(EMBEDDING_NAMESPACE)}/{compiled_status['knowledge_units']}"
            )
        fused, methods = reciprocal_rank_fusion(rank_lists)
        trace.fused_candidates = len(fused)
        candidate_count = max(limit * (8 if normalized_mode == "deep" else 5), limit)
        selected_ids = [unit_id for unit_id, _ in fused[:candidate_count]]
        units = self.store.get_units(selected_ids)
        if use_reranker is True and normalized_mode in {"accurate", "deep"} and selected_ids and neural_index_ready and runtime.get("reranker", {}).get("files_present"):
            rerank_ids = [unit_id for unit_id in selected_ids if unit_id in units]
            try:
                phase_started = time.perf_counter()
                scores = semantic_runtime.rerank(query, [str(units[unit_id].get("content", "")) for unit_id in rerank_ids])
                trace.reranker_latency_ms += (time.perf_counter() - phase_started) * 1000
                base_scores = dict(fused)
                fused = sorted(
                    ((unit_id, float(score) + base_scores.get(unit_id, 0.0) * 0.01) for unit_id, score in zip(rerank_ids, scores, strict=True)),
                    key=lambda item: (-item[1], item[0]),
                )
                for unit_id in rerank_ids:
                    methods.setdefault(unit_id, []).append("qwen3_reranker")
                trace.reranker_status = "ready"
                trace.reranker_applied = True
            except Exception as exc:
                trace.reranker_status = "error"
                trace.warnings.append(f"reranker_error:{type(exc).__name__}:{exc}")
        elif use_reranker is True and normalized_mode in {"accurate", "deep"}:
            trace.warnings.append(f"reranker_unavailable:{models['reranker']['model']}")
        results: list[dict[str, Any]] = []
        for unit_id, score in fused:
            unit = units.get(unit_id)
            if unit is None:
                continue
            result = build_evidence(unit, score, methods.get(unit_id, []), normalized_mode, query)
            results.append(result)
            if len(results) >= limit:
                break

        # Preserve access to user/project evidence without displacing the audited
        # hot layer.  It is appended only when the compiled layer has spare slots.
        if len(results) < limit:
            for item in self.legacy.search(query, limit - len(results)):
                item["retrieval_mode"] = f"{normalized_mode}:project-secondary"
                item["retrieval_path"] = {
                    "bm25": True,
                    "dense": "hybrid" in str(item.get("retrieval_mode")),
                    "rrf": False,
                    "reranker": False,
                    "evidence_builder": False,
                    "claim_verification": "unverified",
                }
                results.append(item)
        trace.latency_ms = (time.perf_counter() - started) * 1000
        return {"query": query, "mode": normalized_mode, "results": results, "trace": trace.as_dict()}


class IndexTaskManager:
    """Run large index work off the request handler with a bounded event log."""

    def __init__(self, service: ScientificRAGService | None = None, max_events: int = 200, *, semantic: bool = False) -> None:
        self.service = service
        self.semantic = semantic
        self._task: asyncio.Task[None] | None = None
        self._lock = RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._sequence = 0
        self._status: dict[str, Any] = {"state": "idle", "result": None, "error": None}

    def bind(self, service: ScientificRAGService) -> None:
        self.service = service

    async def start(self) -> dict[str, Any]:
        if self.service is None:
            raise RuntimeError("IndexTaskManager is not bound to a RAG service")
        if self._task and not self._task.done():
            return self.status()
        self._status = {"state": "running", "result": None, "error": None, "started_at": _now()}
        self._append({"type": "progress", "phase": "index", "status": "running", "completed": 0})
        self._task = asyncio.create_task(self._run(), name="semantic-kb-index" if self.semantic else "compiled-kb-index")
        return self.status()

    def status(self) -> dict[str, Any]:
        return {**self._status, "latest_sequence": self._sequence, "running": bool(self._task and not self._task.done())}

    def events_after(self, sequence: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events if int(item["sequence"]) > sequence]

    async def _run(self) -> None:
        assert self.service is not None
        try:
            builder = self.service.build_semantic_index if self.semantic else self.service.build_compiled_index
            result = await asyncio.to_thread(builder, self._append)
            self._status = {"state": "completed", "result": result, "error": None, "completed_at": _now()}
            self._append({"type": "completed", "phase": "index", "status": result.get("status", "completed"), "result": result})
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._status = {"state": "failed", "result": None, "error": detail, "completed_at": _now()}
            self._append({"type": "error", "phase": "index", "status": "error", "detail": detail})

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._sequence += 1
            self._events.append({**event, "sequence": self._sequence, "created_at": _now()})


def reciprocal_rank_fusion(
    rank_lists: list[tuple[str, list[dict[str, Any]]]],
    *,
    k: int = RRF_K,
) -> tuple[list[tuple[str, float]], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    methods: dict[str, list[str]] = {}
    for method, ranked in rank_lists:
        for rank, item in enumerate(ranked, 1):
            unit_id = str(item["unit_id"])
            scores[unit_id] = scores.get(unit_id, 0.0) + 1.0 / (k + rank)
            if method not in methods.setdefault(unit_id, []):
                methods[unit_id].append(method)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0])), methods


def build_evidence(
    unit: dict[str, Any],
    score: float,
    methods: list[str],
    mode: str,
    query: str,
) -> dict[str, Any]:
    sources = unit.get("sources") or []
    source = sources[0] if sources else {}
    document_id = str(source.get("document_id") or (unit.get("source_document_ids") or [""])[0])
    document = unit.get("documents", {}).get(document_id, {})
    page = source.get("page") if isinstance(source.get("page"), int) else None
    page_count = int(document.get("page_count") or 0)
    page_status = "precise" if page is not None and 1 <= page <= page_count else "page_unknown" if page is None else "invalid"
    if page_status == "invalid":
        page = None
    resolved_path = document.get("resolved_path")
    recorded_path = document.get("recorded_path")
    file_path = str(resolved_path or recorded_path or "")
    content = str(unit.get("content", ""))
    excerpt = _excerpt(content)
    citation = {
        "document_id": document_id,
        "file": file_path,
        "file_name": str(source.get("file_name") or document.get("file_name") or ""),
        "title": str(document.get("title") or unit.get("title") or unit["unit_id"]),
        "page": page,
        "page_status": page_status,
        "section": source.get("section") or unit.get("parent_context") or None,
        "chunk_id": str(unit["unit_id"]),
        "unit_id": str(unit["unit_id"]),
        "excerpt": excerpt,
        "source_type": "COMSOL Official Knowledge Base",
        "ingestion_time": datetime.now(UTC).isoformat(),
        "authors": [],
        "doi": None,
        "year": None,
    }
    overlap = claim_overlap(query, f"{unit.get('title', '')} {content}")
    verified = bool(document_id and citation["file_name"] and page_status in {"precise", "page_unknown"} and overlap > 0)
    retrieval_method = "+".join(methods + ["rrf"])
    evidence = {
        "unit_id": unit["unit_id"],
        "title": unit.get("title"),
        "type": unit.get("type"),
        "module": unit.get("module"),
        "physics": unit.get("physics"),
        "interface": unit.get("interface"),
        "feature_type": unit.get("feature_type"),
        "parent_context": unit.get("parent_context"),
        "document_version": unit.get("document_version"),
        "applies_to_versions": unit.get("applies_to_versions", []),
        "version_status": unit.get("version_status"),
        "document": citation["file_name"],
        "page": page,
        "page_status": page_status,
        "section": citation["section"],
        "score": round(score, 8),
        "retrieval_method": retrieval_method,
        "claim_verification": "supported" if verified else "unverified",
        "query_overlap": overlap,
    }
    return {
        "text": content[:2200],
        "score": round(score, 8),
        "retrieval_mode": mode,
        "retrieval_path": {
            "bm25": "bm25" in methods,
            "dense": any(method.startswith("dense") for method in methods),
            "rrf": True,
            "reranker": "qwen3_reranker" in methods,
            "evidence_builder": True,
            "claim_verification": evidence["claim_verification"],
        },
        "evidence": evidence,
        "citation": citation,
    }


def expand_scientific_query(query: str) -> str:
    lower = query.lower()
    additions = [aliases for marker, aliases in DOMAIN_ALIASES.items() if marker in lower or marker in query]
    return " ".join([query, *additions]).strip()


def route_metadata_filters(query: str) -> dict[str, Any]:
    """Apply conservative metadata filters only for explicit domain requests."""

    lower = query.lower()
    if "java api" in lower or "java® api" in lower:
        return {"feature_type": "Java API"}
    if any(term in lower for term in ("release notes", "changed behavior", "version-specific", "version specific")):
        if "legacy" in lower or re.search(r"\b(?:4|5)\.\d\b", lower):
            return {"version_status": ["legacy", "version_specific"]}
        return {"version_status": ["current", "version_specific", "mixed"]}
    if "heat transfer" in lower or "thermal insulation" in lower or "传热" in query or "绝热" in query:
        return {"physics": "Heat Transfer"}
    if "solid mechanics" in lower or "固体力学" in query:
        return {"physics": "Structural Mechanics"}
    if "transport of diluted species" in lower or re.search(r"\btds\b", lower) or "稀物质" in query:
        return {"physics": "Chemical Species Transport"}
    if "troubleshooting" in lower or "故障" in query:
        return {"type": "Troubleshooting"}
    return {}


def decompose_query(query: str) -> list[str]:
    variants = [expand_scientific_query(query)]
    for part in re.split(r"[?？。；;，,]|\band\b|\bversus\b|\bvs\.?\b", query, flags=re.I):
        candidate = part.strip()
        if len(candidate) >= 4:
            expanded = expand_scientific_query(candidate)
            if expanded not in variants:
                variants.append(expanded)
    return variants[:6]


def claim_overlap(query: str, text: str) -> int:
    query_terms = set(re.findall(r"[a-z][a-z0-9_.+-]{2,}|[\u4e00-\u9fff]{2,6}", expand_scientific_query(query).lower()))
    text_lower = text.lower()
    return sum(1 for term in query_terms if term in text_lower)


def _excerpt(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    body = " ".join(line for line in lines if not line.startswith("**"))
    return re.sub(r"\s+", " ", body)[:500]


def _now() -> str:
    return datetime.now(UTC).isoformat()


index_task_manager = IndexTaskManager()
semantic_index_task_manager = IndexTaskManager(semantic=True)
