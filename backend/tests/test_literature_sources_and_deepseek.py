"""Source and cloud-extraction gates use controlled responses, not live credentials."""

import asyncio
import json
from datetime import date

import httpx
import pytest


def test_source_url_allowlist_rejects_redirect_and_private_hosts():
    from app.services.literature_sources import parse_source_url

    assert parse_source_url("https://arxiv.org/abs/2410.12345") == ("arxiv", "2410.12345")
    assert parse_source_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC12345678/") == ("pmc", "PMC12345678")
    for url in (
        "http://arxiv.org/abs/2410.12345", "https://arxiv.org.evil.test/abs/2410.12345",
        "https://127.0.0.1/abs/2410.12345", "https://arxiv.org:443/abs/2410.12345",
        "https://arxiv.org/redirect?url=http://127.0.0.1", "file:///etc/passwd",
    ):
        with pytest.raises(ValueError, match="source_not_allowed"):
            parse_source_url(url)


def test_recent_source_uses_fixed_api_and_rolling_three_year_cutoff():
    from app.services.literature_sources import fetch_recent_source

    feed = """<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>https://arxiv.org/abs/2410.12345</id><title>Heat paper</title><published>2024-10-01T00:00:00Z</published><summary>2D steady heat rectangle 10 mm x 5 mm</summary></entry></feed>"""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=feed)

    async def exercise():
        source = await fetch_recent_source("https://arxiv.org/abs/2410.12345", today=date(2026, 10, 17), transport=httpx.MockTransport(handler))
        assert source["publication_date"] == "2024-10-01"
        assert source["source_kind"] == "arxiv"
        assert source["text"] == "2D steady heat rectangle 10 mm x 5 mm"

    asyncio.run(exercise())
    assert str(requests[0].url).startswith("https://export.arxiv.org/api/query?")
    assert requests[0].url.params["id_list"] == "2410.12345"


def test_old_or_missing_publication_date_is_rejected():
    from app.services.literature_sources import fetch_recent_source

    for published in ("2022-10-16T00:00:00Z", ""):
        feed = f"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>https://arxiv.org/abs/2410.12345</id><title>Old</title><published>{published}</published><summary>Methods</summary></entry></feed>"
        async def exercise():
            with pytest.raises(ValueError, match="source_outside_three_year_window"):
                await fetch_recent_source("https://arxiv.org/abs/2410.12345", today=date(2026, 10, 17), transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed)))
        asyncio.run(exercise())


def test_deepseek_can_only_choose_an_exact_source_window():
    from app.services.literature_modeling import extract_text_candidate_deepseek

    text = "Background notes.\n\n2D stationary heat rectangle 10 mm x 5 mm, left 373 K, right 293 K."
    calls = []
    excerpts = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"window_id":1}'}}], "model": "deepseek-chat"})

    async def exercise():
        result = await extract_text_candidate_deepseek("Paper", text, api_key="test-only", transport=httpx.MockTransport(handler))
        excerpts.append(result["excerpt"])
        assert result["excerpt"] in text
        assert result["extraction_method"] == "deepseek_window_selection"
        assert result["modeling_started"] is False
        assert result["scientific_validation_passed"] is False

    asyncio.run(exercise())
    assert calls[0].url.host == "api.deepseek.com"
    assert calls[0].headers["authorization"] == "Bearer test-only"
    sent = json.loads(json.loads(calls[0].content)["messages"][1]["content"])
    assert sent[1]["text"] == excerpts[0]


def test_deepseek_missing_key_or_invalid_response_never_falls_back():
    from app.services.literature_modeling import extract_text_candidate_deepseek

    async def missing_key():
        with pytest.raises(ValueError, match="deepseek_key_unavailable"):
            await extract_text_candidate_deepseek("Paper", "Methods", api_key="")
        with pytest.raises(ValueError, match="deepseek_invalid_selection"):
            await extract_text_candidate_deepseek("Paper", "Methods", api_key="test-only", transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"window_id":99}'}}]})
            ))

    asyncio.run(missing_key())


def test_pmc_uses_official_api_and_rejects_ambiguous_date():
    from app.services.literature_sources import fetch_recent_source

    xml = "<article><front><article-meta><article-id pub-id-type='pmcid'>PMC12345678</article-id><title-group><article-title>Thermal study</article-title></title-group><pub-date><year>2025</year><month>02</month><day>03</day></pub-date><abstract><p>2D heat rectangle.</p></abstract></article-meta></front></article>"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=xml)

    async def exercise():
        result = await fetch_recent_source("https://pmc.ncbi.nlm.nih.gov/articles/PMC12345678/", today=date(2026, 10, 17), transport=httpx.MockTransport(handler))
        assert result["publication_date"] == "2025-02-03"
        assert result["text"] == "2D heat rectangle."

    asyncio.run(exercise())
    assert calls[0].url.host == "eutils.ncbi.nlm.nih.gov"


def test_three_year_boundary_and_xml_entity_rejection():
    from app.services.literature_sources import fetch_recent_source

    async def attempt(published):
        feed = f"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>https://arxiv.org/abs/2310.12345</id><title>Paper</title><published>{published}T00:00:00Z</published><summary>Methods</summary></entry></feed>"
        return await fetch_recent_source("https://arxiv.org/abs/2310.12345", today=date(2026, 10, 17), transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed)))

    assert asyncio.run(attempt("2023-10-17"))["publication_date"] == "2023-10-17"
    with pytest.raises(ValueError, match="source_outside_three_year_window"):
        asyncio.run(attempt("2023-10-16"))
    unsafe = "<!DOCTYPE feed [<!ENTITY x 'unsafe'>]><feed/>"
    with pytest.raises(ValueError, match="source_xml_unsafe"):
        asyncio.run(fetch_recent_source("https://arxiv.org/abs/2310.12345", transport=httpx.MockTransport(lambda _: httpx.Response(200, text=unsafe))))


def test_arxiv_response_must_match_exact_requested_identity():
    from app.services.literature_sources import fetch_recent_source

    feed = "<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/2410.123456</id><title>Wrong paper</title><published>2024-10-01T00:00:00Z</published><summary>Methods</summary></entry></feed>"
    with pytest.raises(ValueError, match="source_record_unavailable"):
        asyncio.run(fetch_recent_source("https://arxiv.org/abs/2410.12345", today=date(2026, 10, 17), transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed))))


def test_cloud_toggle_is_off_by_default_and_key_is_never_persisted(monkeypatch, tmp_path):
    from app import main
    from app.config import SettingsStore

    store = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(main, "settings_store", store)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("COMSOL_WORKBENCH_DEEPSEEK_API_KEY", raising=False)
    assert store.load()["extensions"]["literature_modeling"]["deepseek_enabled"] is False

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1") as client:
            await client.put("/api/extensions/literature-modeling", json={"enabled": True})
            response = await client.put("/api/extensions/literature-modeling/deepseek", json={"enabled": True})
            assert response.status_code == 200
            assert response.json()["deepseek_enabled"] is True
            assert response.json()["deepseek_available"] is False
            failed = await client.post("/api/extensions/literature-modeling/pdf", content=b"not-a-pdf", headers={"X-Workbench-Filename": "paper.pdf"})
            assert failed.status_code == 503
            assert failed.json()["detail"] == "deepseek_key_unavailable"

    asyncio.run(exercise())
    saved = store.path.read_text(encoding="utf-8")
    assert '"deepseek_enabled": true' in saved
    assert "api_key" not in saved


def test_conventional_deepseek_env_key_is_read_without_persistence(monkeypatch, tmp_path):
    from app.config import SettingsStore

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-not-a-real-key")
    store = SettingsStore(path=tmp_path / "settings.json")
    assert store.secret("deepseek") == "test-only-not-a-real-key"
    store.update({"extensions": {"literature_modeling": {"deepseek_enabled": True}}})
    assert "test-only-not-a-real-key" not in store.path.read_text(encoding="utf-8")
