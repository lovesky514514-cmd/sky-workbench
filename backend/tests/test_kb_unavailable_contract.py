"""Public-build retrieval availability must not imply missing KB evidence exists."""

from app.knowledge.hybrid_rag import ScientificRAGService
from types import SimpleNamespace


class _Compiled:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    def status(self) -> dict:
        return {
            "database_ready": self.ready,
            "knowledge_units": 1 if self.ready else 0,
            "indexed_units": 1 if self.ready else 0,
            "documents": 1 if self.ready else 0,
            "index_path": "isolated-index",
        }


class _Project:
    def __init__(self, chunks: int) -> None:
        self.chunks = chunks

    def status(self) -> dict:
        return {"basic_chunks": self.chunks, "ingestion_issues": []}


def test_no_compiled_kb_or_project_index_is_unavailable(monkeypatch) -> None:
    from app.knowledge import hybrid_rag

    monkeypatch.setattr(hybrid_rag, "settings_store", SimpleNamespace(load=lambda: {"retrieval_mode": "accurate"}))
    status = ScientificRAGService(_Project(0), _Compiled(False)).status()
    assert status["status"] == "Unavailable"
    assert status["database_ready"] is False
    assert "unavailable" in status["detail"].lower()


def test_project_only_fallback_is_explicitly_degraded(monkeypatch) -> None:
    from app.knowledge import hybrid_rag

    monkeypatch.setattr(hybrid_rag, "settings_store", SimpleNamespace(load=lambda: {"retrieval_mode": "accurate"}))
    status = ScientificRAGService(_Project(3), _Compiled(False)).status()
    assert status["status"] == "Warning"
    assert "unavailable" in status["detail"].lower()
    assert status["basic_chunks"] == 3
