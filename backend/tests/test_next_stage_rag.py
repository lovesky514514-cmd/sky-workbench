import json
from pathlib import Path

from app.config import COMPILED_KB_ROOT
from app.knowledge.compiled_store import CompiledKnowledgeStore
from app.knowledge.hybrid_rag import ScientificRAGService, build_evidence, reciprocal_rank_fusion


class EmptyLegacy:
    def status(self):
        return {"status": "Warning", "basic_chunks": 0, "ingestion_issues": []}

    def search(self, _query, _limit):
        return []

    def refresh(self, **_kwargs):
        return self.status()


def mini_store(tmp_path: Path) -> CompiledKnowledgeStore:
    root = tmp_path / "_compiled_kb"
    root.mkdir()
    markdown = """# COMSOL Core Knowledge

## KB-CORE-0001 — Thermal Insulation

Thermal Insulation specifies no heat flux across the boundary.

**Sources:**
- `HeatTransferModuleUsersGuide.pdf`; Page: 769; Section: Thermal Insulation
"""
    for index, name in enumerate(
        (
            "01_COMSOL_CORE_KNOWLEDGE.md",
            "02_COMSOL_JAVA_API.md",
            "03_COMSOL_PHYSICS_RECIPES.md",
            "04_COMSOL_SOLVER_MESH_VALIDATION.md",
            "05_COMSOL_TROUBLESHOOTING.md",
        )
    ):
        (root / name).write_text(markdown if index == 0 else f"# Empty {index}\n", encoding="utf-8")
    payload = {
        "documents": [
            {
                "document_id": "DOC-heat",
                "file_name": "HeatTransferModuleUsersGuide.pdf",
                "full_path": "E:\\old\\HeatTransferModuleUsersGuide.pdf",
                "sha256": "abc",
                "title": "Heat Transfer Module User's Guide",
                "version": "6.4",
                "module": "Heat Transfer",
                "document_type": "User Guide",
                "page_count": 1000,
                "knowledge_unit_count": 1,
            }
        ],
        "knowledge_units": [
            {
                "id": "KB-CORE-0001",
                "type": "Core",
                "title": "Thermal Insulation",
                "module": "Heat Transfer",
                "physics": "Heat Transfer",
                "interface": "Heat Transfer in Solids",
                "feature_type": "Boundary Condition",
                "parent_context": "Thermal Insulation",
                "document_version": "6.4",
                "applies_to_versions": ["6.4"],
                "version_status": "current",
                "sources": [
                    {
                        "document_id": "DOC-heat",
                        "file_name": "HeatTransferModuleUsersGuide.pdf",
                        "page": 769,
                        "section": "Thermal Insulation",
                    }
                ],
                "source_document_ids": ["DOC-heat"],
                "excluded_from_main_rag": False,
            }
        ],
    }
    (root / "06_SOURCE_INDEX.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / "COMPILATION_REPORT.md").write_text("passed", encoding="utf-8")
    (root / "REPAIR_REPORT.md").write_text("passed", encoding="utf-8")
    return CompiledKnowledgeStore(root, tmp_path / "index.sqlite3")


def test_real_source_index_is_readable_without_building_or_modifying_it() -> None:
    store = CompiledKnowledgeStore(COMPILED_KB_ROOT, Path("missing-never-created.sqlite3"))
    status = store.status()
    assert status["documents"] == 108
    assert status["knowledge_units"] == 14210
    assert all(status["required_files"].values())
    assert status["readonly"] is True


def test_compiled_store_indexes_real_units_and_skips_unchanged_hashes(tmp_path) -> None:
    store = mini_store(tmp_path)
    first = store.build_index()
    assert first["status"] == "passed"
    assert first["indexed"] == 1
    assert first["embeddings_indexed"] == 1
    second = store.build_index()
    assert second["indexed"] == 0
    assert second["skipped"] == 1
    assert second["embeddings_indexed"] == 0
    assert store.status()["database_ready"] is True

    bm25 = store.bm25_search("thermal insulation heat flux", filters={"physics": "Heat Transfer"})
    dense = store.dense_search("thermal insulation heat flux")
    assert bm25[0]["unit_id"] == "KB-CORE-0001"
    assert dense[0]["unit_id"] == "KB-CORE-0001"
    assert store.bm25_search("thermal insulation", filters={"physics": "Solid Mechanics"}) == []

    source_index = store.kb_root / "06_SOURCE_INDEX.json"
    payload = json.loads(source_index.read_text(encoding="utf-8"))
    payload["knowledge_units"][0]["parent_context"] = "Updated metadata only"
    source_index.write_text(json.dumps(payload), encoding="utf-8")
    metadata_only = store.build_index()
    assert metadata_only["indexed"] == 0
    assert metadata_only["metadata_updated"] == 1
    assert metadata_only["embeddings_indexed"] == 0

    new_namespace = store.build_index(namespace="local-hash-384-v2")
    assert new_namespace["embeddings_indexed"] == 1
    with store._connect(readonly=True) as db:
        assert db.execute("SELECT count(DISTINCT namespace) FROM embeddings").fetchone()[0] == 2


def test_three_rag_modes_return_evidence_and_precise_citation(tmp_path, monkeypatch) -> None:
    store = mini_store(tmp_path)
    store.build_index()
    service = ScientificRAGService(EmptyLegacy(), store)
    monkeypatch.setattr(service, "active_mode", lambda: "accurate")
    for mode in ("fast", "accurate", "deep"):
        detailed = service.search_detailed("What does Thermal Insulation mean?", mode=mode, limit=1)
        result = detailed["results"][0]
        assert detailed["mode"] == mode
        assert result["citation"]["unit_id"] == "KB-CORE-0001"
        assert result["citation"]["page"] == 769
        assert result["citation"]["page_status"] == "precise"
        assert result["evidence"]["retrieval_method"].endswith("rrf")
        assert result["retrieval_path"]["evidence_builder"] is True


def test_rrf_and_page_unknown_never_invent_a_page() -> None:
    fused, methods = reciprocal_rank_fusion(
        [
            ("bm25", [{"unit_id": "a"}, {"unit_id": "b"}]),
            ("dense", [{"unit_id": "b"}, {"unit_id": "a"}]),
        ]
    )
    assert {item[0] for item in fused} == {"a", "b"}
    assert methods["a"] == ["bm25", "dense"]
    unit = {
        "unit_id": "KB-X-1",
        "title": "Unknown page",
        "content": "solver convergence evidence",
        "sources": [{"document_id": "doc", "file_name": "source.pdf", "section": "Solver"}],
        "source_document_ids": ["doc"],
        "documents": {"doc": {"file_name": "source.pdf", "page_count": 20}},
        "applies_to_versions": [],
    }
    result = build_evidence(unit, 0.1, ["bm25"], "accurate", "solver convergence")
    assert result["citation"]["page"] is None
    assert result["citation"]["page_status"] == "page_unknown"
