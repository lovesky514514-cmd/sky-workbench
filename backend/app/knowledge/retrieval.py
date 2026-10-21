"""Hybrid BM25 plus deterministic local embedding retrieval with Basic fallback."""

from __future__ import annotations

import hashlib
import math
import re
from threading import RLock
from typing import Any

import numpy as np

from app.config import settings_store
from app.knowledge.ingestion import KnowledgeChunk, knowledge_ingestor


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*|[\u4e00-\u9fff]+")
EMBEDDING_SIZE = 384


class HybridRetriever:
    """Traceable retrieval whose optional enhanced path cannot block startup."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._chunks: list[KnowledgeChunk] | None = None
        self._tokens: list[list[str]] = []
        self._document_frequency: dict[str, int] = {}
        self._embeddings: np.ndarray | None = None
        self._embedding_error: str | None = None

    def status(self) -> dict[str, Any]:
        self._ensure_index()
        requested = settings_store.load().get("retrieval_mode", "basic")
        enhanced_available = self._embeddings is not None
        active = "enhanced" if requested == "enhanced" and enhanced_available else "basic"
        return {
            "status": "Ready" if self._chunks else "Warning",
            "active_mode": active,
            "requested_mode": requested,
            "basic_chunks": len(self._chunks or []),
            "enhanced_available": enhanced_available,
            "embedding_backend": "local-hash-384" if enhanced_available else "unavailable",
            "embedding_semantics": "deterministic lexical feature embedding; no neural model is claimed",
            "pdf_support": not any("pypdf is not installed" in item for item in knowledge_ingestor.issues),
            "ingestion_issues": knowledge_ingestor.issues[:20],
            "index_path": str(knowledge_ingestor.index_path),
            "detail": "Enhanced combines BM25 and cosine vector retrieval; Basic remains the startup-safe fallback.",
        }

    def refresh(self, *, include_legacy: bool = True) -> dict[str, Any]:
        with self._lock:
            self._chunks = knowledge_ingestor.ingest(include_legacy=include_legacy, persist=True)
            self._build_index()
        return self.status()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        self._ensure_index()
        chunks = self._chunks or []
        query_tokens = tokenize(expand_query(query))
        if not chunks or not query_tokens:
            return []
        bm25 = self._bm25_scores(query_tokens)
        requested = settings_store.load().get("retrieval_mode", "basic")
        if requested == "enhanced" and self._embeddings is not None:
            query_embedding = embed(query_tokens)
            vector = self._embeddings @ query_embedding
            bm25_max = max(bm25) if bm25 else 0.0
            combined = [0.65 * (score / bm25_max if bm25_max else 0.0) + 0.35 * max(float(vector[index]), 0.0) for index, score in enumerate(bm25)]
            ranked = [(score + scientific_bonus(query, chunks[index].text), index) for index, score in enumerate(combined) if score > 0]
            mode = "hybrid"
        else:
            ranked = [(score + scientific_bonus(query, chunks[index].text), index) for index, score in enumerate(bm25) if score > 0]
            mode = "bm25"
        selected = select_diverse(ranked, chunks, limit)
        return [self._result(chunks[index], score, mode) for score, index in selected]

    def _ensure_index(self) -> None:
        if self._chunks is not None:
            return
        with self._lock:
            if self._chunks is not None:
                return
            self._chunks = knowledge_ingestor.load_persisted()
            if self._chunks is None:
                self._chunks = knowledge_ingestor.ingest(include_legacy=True, persist=True)
            self._build_index()

    def _build_index(self) -> None:
        chunks = self._chunks or []
        self._tokens = [tokenize(chunk.text) for chunk in chunks]
        frequency: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                frequency[token] = frequency.get(token, 0) + 1
        self._document_frequency = frequency
        try:
            self._embeddings = np.vstack([embed(tokens) for tokens in self._tokens]) if chunks else None
            self._embedding_error = None
        except (MemoryError, ValueError, RuntimeError) as exc:
            self._embeddings = None
            self._embedding_error = f"{type(exc).__name__}: {exc}"

    def _bm25_scores(self, query_tokens: list[str]) -> list[float]:
        chunks = self._chunks or []
        average_length = sum(map(len, self._tokens)) / max(len(self._tokens), 1)
        scores: list[float] = []
        for tokens in self._tokens:
            term_counts: dict[str, int] = {}
            for token in tokens:
                term_counts[token] = term_counts.get(token, 0) + 1
            score = 0.0
            for term in query_tokens:
                frequency = term_counts.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency.get(term, 0)
                inverse = math.log(1 + (len(chunks) - document_frequency + 0.5) / (document_frequency + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1))
                score += inverse * frequency * 2.5 / denominator
            scores.append(score)
        return scores

    @staticmethod
    def _result(chunk: KnowledgeChunk, score: float, mode: str) -> dict[str, Any]:
        citation = {
            "document_id": chunk.document_id,
            "file": chunk.file,
            "file_name": chunk.file_name,
            "title": chunk.title,
            "page": chunk.page,
            "section": chunk.section,
            "chunk_id": chunk.chunk_id,
            "excerpt": chunk.text[:420],
            "source_type": chunk.source_type,
            "ingestion_time": chunk.ingestion_time,
            "authors": list(chunk.authors),
            "doi": chunk.doi,
            "year": chunk.year,
        }
        return {"text": chunk.text[:1600], "score": round(score, 4), "retrieval_mode": mode, "citation": citation}


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text.lower()):
        value = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            tokens.extend(value[index : index + 2] for index in range(max(len(value) - 1, 1)))
            if len(value) == 1:
                tokens.append(value)
        else:
            tokens.append(value)
    return tokens


def embed(tokens: list[str]) -> np.ndarray:
    vector = np.zeros(EMBEDDING_SIZE, dtype=np.float32)
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % EMBEDDING_SIZE
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector


def select_ranked(scores: list[tuple[float, int]], limit: int) -> list[tuple[float, int]]:
    if not scores or limit <= 0:
        return []
    ranked = sorted(scores, reverse=True)
    floor = max(0.05, ranked[0][0] * 0.55)
    return [item for item in ranked if item[0] >= floor][:limit]


def select_diverse(scores: list[tuple[float, int]], chunks: list[KnowledgeChunk], limit: int) -> list[tuple[float, int]]:
    candidates = select_ranked(scores, max(limit * 4, limit))
    selected: list[tuple[float, int]] = []
    fingerprints: set[str] = set()
    for score, index in candidates:
        normalized = re.sub(r"\s+", " ", chunks[index].text).strip().lower()
        fingerprint = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        selected.append((score, index))
        if len(selected) >= limit:
            break
    return selected


BasicRetriever = HybridRetriever
legacy_retriever = HybridRetriever()


def expand_query(query: str) -> str:
    """Add narrow scientific aliases without changing the user's visible query."""

    lower = query.lower()
    additions: list[str] = []
    if "thermal insulation" in lower or "绝热" in query or "不散热" in query:
        additions.append("zero normal heat flux thermally insulated temperature gradient")
    if "heat transfer in solids" in lower:
        additions.append("conductive heat flux boundary")
    if (("铝" in query and "热导" in query) or ("aluminum" in lower and "thermal conductivity" in lower)):
        additions.append("aluminum thermal_conductivity thermal conductivity W m K assumption alloy-specific verify")
    return " ".join((query, *additions))


def scientific_bonus(query: str, text: str) -> float:
    """Prefer passages that define a queried named scientific feature over index/table mentions."""

    lower_query = query.lower()
    lower_text = text.lower()
    bonus = 0.0
    if "thermal insulation" in lower_query or "绝热" in query or "不散热" in query:
        if "thermal insulation" in lower_text:
            bonus += 0.25
        if "no heat flux across the boundary" in lower_text:
            bonus += 0.55
    if (("铝" in query and "热导" in query) or ("aluminum" in lower_query and "thermal conductivity" in lower_query)):
        if "thermal_conductivity" in lower_text and "assumption" in lower_text:
            bonus += 0.5
    return bonus


# Import after the legacy implementation is fully defined so the next-stage
# facade can preserve its public/tested classes without a module cycle.
from app.knowledge.hybrid_rag import ScientificRAGService, index_task_manager, semantic_index_task_manager  # noqa: E402


retriever = ScientificRAGService(legacy_retriever)
index_task_manager.bind(retriever)
semantic_index_task_manager.bind(retriever)
