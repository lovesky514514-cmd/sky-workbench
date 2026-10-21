import asyncio
import json

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.agent.planner import extract_heat_transfer_plan
from app.agent.runtime import AgentRuntime
from app.knowledge.ingestion import KnowledgeChunk, KnowledgeIngestor
from app.knowledge.retrieval import HybridRetriever
from app.models import AgentState
from app.scientific.schemas import Citation, ParameterStatus, ScientificPlan
from app.session.store import SessionStore
from app.validators.physics.heat_transfer_validator import HeatTransferValidator


PROMPT = "建立二维稳态热传导模型。矩形 10 mm × 5 mm，左边界 373 K，右边界 293 K，其余边界绝热，材料为铝。"


def heat_plan() -> ScientificPlan:
    payload = extract_heat_transfer_plan(PROMPT)
    assert payload is not None
    return ScientificPlan.model_validate(payload)


def test_scientific_plan_schema_and_parameter_provenance() -> None:
    plan = heat_plan()
    assert plan.schema_version == "2.0"
    assert plan.execution_payload()["plan_id"] == plan.plan_id
    assert plan.parameters["width"].status == ParameterStatus.USER_PROVIDED
    assert plan.parameters["width"].verified is True
    assert plan.parameters["thermal_conductivity"].status == ParameterStatus.ASSUMPTION
    assert plan.parameters["thermal_conductivity"].verified is False
    with pytest.raises(ValidationError):
        ScientificPlan.model_validate({"title": "missing research goal"})


def test_citation_requires_traceable_identity_and_preserves_metadata() -> None:
    citation = Citation(
        document_id="doc-1",
        file="paper.pdf",
        file_name="paper.pdf",
        title="Heat Transfer Evidence",
        page=4,
        section="Methods",
        chunk_id="chunk-4",
        source_type="Scientific Literature",
        authors=["A. Author"],
        doi="10.1234/example",
        year=2025,
    )
    assert citation.model_dump()["page"] == 4
    assert citation.model_dump()["doi"] == "10.1234/example"
    with pytest.raises(ValidationError):
        Citation.model_validate({"title": "untraceable"})


def test_markdown_and_pdf_ingestion_capture_page_section_and_metadata(tmp_path) -> None:
    ingestor = KnowledgeIngestor(tmp_path / "chunks.jsonl")
    markdown = tmp_path / "paper.md"
    markdown.write_text(
        "# Thermal study\n\n=== PAGE 2 ===\n## Methods\nAluminum conduction evidence. DOI 10.1234/heat.2025 published 2025.",
        encoding="utf-8",
    )
    text_chunks = ingestor._read_text(markdown, "Scientific Literature")
    assert text_chunks[0].page == 2
    assert text_chunks[0].section == "Methods"
    assert text_chunks[0].doi == "10.1234/heat.2025"
    assert text_chunks[0].year == 2025

    pdf_path = tmp_path / "source.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Heat transfer DOI 10.5678/pdf.2024 published 2024) Tj ET")
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "PDF evidence", "/Author": "A. Researcher"})
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    pdf_chunks = ingestor._read_pdf(pdf_path, "Scientific Literature")
    assert pdf_chunks[0].page == 1
    assert pdf_chunks[0].title == "PDF evidence"
    assert pdf_chunks[0].authors == ("A. Researcher",)
    assert pdf_chunks[0].doi == "10.5678/pdf.2024"


def test_enhanced_retrieval_returns_rich_citation(monkeypatch) -> None:
    class EnhancedSettings:
        @staticmethod
        def load() -> dict[str, str]:
            return {"retrieval_mode": "enhanced"}

    monkeypatch.setattr("app.knowledge.retrieval.settings_store", EnhancedSettings())
    retriever = HybridRetriever()
    retriever._chunks = [
        KnowledgeChunk("doc-a", "a.pdf", "a.pdf", "Heat", 7, "Results", "a-7", "aluminum heat conduction temperature", "Scientific Literature", "2025-01-01T00:00:00+00:00", doi="10.1/a", year=2025),
        KnowledgeChunk("doc-b", "b.md", "b.md", "Mechanics", None, "Load", "b-1", "cantilever beam stress", "Project Knowledge", "2025-01-01T00:00:00+00:00"),
    ]
    retriever._build_index()
    results = retriever.search("aluminum heat temperature", 2)
    assert results[0]["retrieval_mode"] == "hybrid"
    assert results[0]["citation"]["page"] == 7
    assert results[0]["citation"]["section"] == "Results"
    assert results[0]["citation"]["chunk_id"] == "a-7"


def test_plan_edit_is_persisted_and_revalidated(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session = store.create("workbench")
    plan = heat_plan()
    plan.parameters["width"].value = 12
    runtime = AgentRuntime(sessions=store)
    updated = asyncio.run(runtime.update_plan(session["id"], plan.model_dump(mode="json")))
    assert updated["state"] == AgentState.PLAN_READY
    assert updated["plan"]["parameters"]["width"]["value"] == 12
    assert updated["plan"]["runnable"] is True
    assert updated["plan"]["precheck_summary"]["passed"] is True
    assert updated["plan"]["precheck_summary"]["command_success"] is False
    assert updated["plan"]["precheck_summary"]["model_state_verified"] is False
    assert updated["plan"]["precheck_summary"]["scientific_validation_success"] is False
    assert any(event["type"] == "plan.validated" for event in updated["events"])


def test_validation_separates_command_success_from_scientific_success() -> None:
    plan = heat_plan()
    validator = HeatTransferValidator()
    assert validator.precheck(plan).passed is True
    execution = {
        "command_success": False,
        "model_state": {
            "geometry": ["Rectangle 10 x 5 mm"],
            "materials": ["Aluminum"],
            "physics": ["Heat Transfer in Solids"],
            "boundary_conditions": ["Left: 373 K", "Right: 293 K"],
            "mesh": ["Physics-controlled mesh"],
            "study": ["Stationary"],
            "solver": ["converged"],
        },
        "validation": {
            "left_boundary_median_K": 373.0,
            "right_boundary_median_K": 293.0,
            "temperature_min_K": 293.0,
            "temperature_max_K": 373.0,
            "analytical_rmse_K": 0.01,
        },
    }
    report = validator.validate_execution(plan, execution)
    assert all(check.status == "passed" for check in report.checks)
    assert report.command_success is False
    assert report.model_state_verified is True
    assert report.scientific_validation_success is False


def test_timeline_keeps_categories_repairs_and_bounded_raw_output(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.initialize()
    session = store.create("workbench")
    runtime = AgentRuntime(sessions=store)
    item_id = runtime._timeline_start(session["id"], category="LLM", phase="Planning", label="Plan", tool="provider.chat", input_summary={"schema": "2.0"})
    runtime._timeline_finish(session["id"], item_id, status="success", output_summary="done", duration_ms=4, fallback=True, raw_output="x" * 13_000)
    restored = store.get(session["id"])["timeline"][0]
    assert restored["category"] == "LLM"
    assert restored["fallback"] is True
    assert len(restored["raw_output"]) == 12_000
