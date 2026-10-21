"""Opt-in literature extraction must never authorize a scientific run."""

import asyncio
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import SettingsStore


TDS = ("2D stationary Transport of Diluted Species rectangle 10 mm x 5 mm, "
       "D=1e-9 m^2/s, left concentration 1 mol/m^3, right concentration "
       "0 mol/m^3, top and bottom no flux.")


def test_extension_disabled_by_default_and_persists_explicit_toggle(tmp_path):
    settings = SettingsStore(path=tmp_path / "settings.json")
    assert settings.load()["extensions"]["literature_modeling"]["enabled"] is False
    settings.update({"extensions": {"literature_modeling": {"enabled": True}}})
    assert SettingsStore(path=settings.path).load()["extensions"]["literature_modeling"]["enabled"] is True


def test_literature_candidate_keeps_exact_evidence_and_no_execution():
    from app.services.literature_modeling import extract_text_candidate

    result = extract_text_candidate("Paper A", f"Background notes.\n\n{TDS}")
    assert result["source_title"] == "Paper A"
    assert TDS in result["excerpt"]
    assert result["intent"]["physics"] == ["Transport of Diluted Species"]
    assert result["requires_user_decision"] is True
    assert result["modeling_started"] is False
    assert result["scientific_validation_passed"] is False


def test_missing_and_unsupported_evidence_never_becomes_runnable_claim():
    from app.services.literature_modeling import extract_text_candidate

    for excerpt in ("We study a model.", "3D turbulent flow simulation in a pipe."):
        candidate = extract_text_candidate("Paper B", excerpt)
        assert candidate["status"] == "insufficient_evidence"
        assert candidate["can_open_plan"] is False
        assert candidate["modeling_started"] is False


def test_pdf_extracts_page_bound_candidate_without_retaining_pdf():
    from app.services.literature_modeling import extract_pdf_candidate

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({TDS}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    result = extract_pdf_candidate("paper.pdf", buffer.getvalue())
    assert result["source_kind"] == "pdf"
    assert result["page_number"] == 1
    assert result["status"] == "review_required"
    assert result["modeling_started"] is False
    with pytest.raises(ValueError, match="pdf_read_failed"):
        extract_pdf_candidate("broken.pdf", b"not-a-pdf")


def test_endpoint_rejects_when_off_then_returns_review_only(monkeypatch, tmp_path):
    from app import main

    settings = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(main, "settings_store", settings)

    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            off = await client.post("/api/extensions/literature-modeling/text", json={"source_title": "Paper A", "text": TDS})
            assert off.status_code == 403
            switch = await client.put("/api/extensions/literature-modeling", json={"enabled": True})
            assert switch.status_code == 200
            on = await client.post("/api/extensions/literature-modeling/text", json={"source_title": "Paper A", "text": TDS})
            assert on.status_code == 200
            assert on.json()["requires_user_decision"] is True
            assert on.json()["modeling_started"] is False

    asyncio.run(exercise())
