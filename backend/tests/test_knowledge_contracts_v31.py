import json

import pytest

from app.knowledge.contracts import metadata_matches, normalized_metadata
from app.knowledge.hybrid_rag import ScientificRAGService
from app.knowledge.retrieval import HybridRetriever
from app.knowledge.ingestion import KnowledgeChunk
from app.knowledge.sources import SourceKnowledgeStore
from test_next_stage_rag import EmptyLegacy, mini_store


def unit(name="LAMMPS-MSD", engine="LAMMPS", version="22Jul2025-update4", content=None):
    return {"unit_id": name, "title": "Mean squared displacement command",
            "content": content or "LAMMPS compute msd reports mean squared displacement using unwrapped coordinates.",
            "metadata": {"engine": engine, "module": "core", "applies_to_versions": [version],
                         "license_class": "user_local_only", "source_url": "https://docs.lammps.org/compute_msd.html"}}


@pytest.mark.parametrize("engine", ["COMSOL", "MATLAB", "VASP", "Materials Studio", "Mathics3"])
@pytest.mark.parametrize("variant", range(4))
def test_twenty_cross_engine_negative_queries_return_zero(tmp_path, engine, variant):
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    sources.import_units([unit()], project_id="workbench")
    filters = {"engine": engine, "project_id": "workbench"}
    assert sources.search(["LAMMPS compute msd", "mean squared displacement", "unwrapped coordinates", "compute msd"][variant], 5, filters=filters) == []


@pytest.mark.parametrize("topic", [
    "superconducting gap", "galactic rotation", "banana tensile", "isotope abundance", "solar opacity",
    "quantum entanglement", "photosynthesis yield", "magnetic monopole", "electron affinity", "porosity threshold",
    "ion implantation", "viscoelastic compliance", "finite element fracture", "lithium intercalation", "supercritical argon",
    "phonon dispersion", "seismic moment", "water surface tension", "chromatic aberration", "dielectric breakdown",
])
def test_twenty_unanswerable_queries_report_no_evidence(tmp_path, topic):
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    sources.import_units([unit()], project_id="workbench")
    service = ScientificRAGService(EmptyLegacy(), mini_store(tmp_path), sources)
    response = service.search_detailed(topic, filters={"engine": "LAMMPS"}, project_id="workbench")
    assert response["evidence_status"] == "NO_EVIDENCE" and response["results"] == []


def test_version_project_and_reference_only_sources_are_isolated(tmp_path):
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    sources.import_units([unit()], project_id="alpha")
    assert not sources.search("compute msd", 5)
    assert not sources.search("compute msd", 5, filters={"project_id": "beta"})
    assert not sources.search("compute msd", 5, filters={"project_id": "alpha", "engine_version": "30Sep2026"})
    found = sources.search("compute msd", 5, filters={"project_id": "alpha", "engine_version": "22 Jul 2025 - Update 4"})
    assert found and found[0]["evidence"]["claim_verification"] == "unverified"
    assert len(found[0]["metadata"]["chunk_sha256"]) == 64


def test_import_cannot_claim_public_rights_or_validated_evidence(tmp_path):
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    sample = unit()
    sample["metadata"].update(license_class="public_redistributable", verification_status="VALIDATED", project_id="other", linked_case_id="forged")
    sources.import_units([sample], project_id="alpha")
    metadata = sources.search("compute msd", 1, filters={"project_id": "alpha"})[0]["metadata"]
    assert metadata["license_class"] == "user_local_only"
    assert metadata["verification_status"] == "UNVERIFIED" and metadata["linked_case_id"] is None
    assert metadata["project_id"] == "alpha"


def test_lineage_cycles_roll_back_and_are_checked_with_networkx(tmp_path):
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    a, b = unit("a"), unit("b")
    a["metadata"]["parent_unit_ids"] = ["b"]
    b["metadata"]["parent_unit_ids"] = ["a"]
    with pytest.raises(ValueError, match="cycle"):
        sources.import_units([a, b], project_id="alpha")
    assert sources.status()["units"] == 0
    assert sources.status()["lineage_backend"] == "networkx-3.7"


def test_empty_filter_does_not_enable_query_routing_and_legacy_cannot_bypass_filters(tmp_path):
    compiled = mini_store(tmp_path); compiled.build_index()
    sources = SourceKnowledgeStore(tmp_path / "sources.sqlite3")
    legacy = HybridRetriever()
    legacy._chunks = [KnowledgeChunk("doc", "fixture", "fixture", "Fixture", None, None, "chunk",
                                     "thermal insulation no heat flux", "COMSOL Documentation", "2026-10-31")]
    legacy._build_index()
    service = ScientificRAGService(legacy, compiled, sources)
    explicit = service.search_detailed("thermal insulation", filters={})
    assert explicit["trace"]["filters"] == {}
    routed = service.search_detailed("thermal insulation", filters=None)
    assert routed["trace"]["filters"] == {"physics": "Heat Transfer"}
    wrong = service.search_detailed("thermal insulation", filters={"engine": "VASP"})
    assert wrong["results"] == [] and wrong["evidence_status"] == "NO_EVIDENCE"
    right = service.search_detailed("thermal insulation", filters={"engine": "COMSOL", "engine_version": "6.4"})
    assert len(right["results"]) == 1  # Legacy fixture has no declared version and cannot fill the result.
    assert right["results"][0]["evidence"]["claim_verification"] == "unverified"


def test_unknown_filters_are_rejected_instead_of_dropped(tmp_path):
    service = ScientificRAGService(EmptyLegacy(), mini_store(tmp_path), SourceKnowledgeStore(tmp_path / "sources.sqlite3"))
    with pytest.raises(ValueError, match="Unsupported"):
        service.search_detailed("thermal insulation", filters={"arbitrary_filter": "ignored"})
