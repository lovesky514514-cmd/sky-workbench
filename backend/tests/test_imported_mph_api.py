"""Imported MPH preview never steals a live Bridge or invents certification."""

import asyncio

import pytest
import httpx
from fastapi import HTTPException

from app.services.imported_mph import ImportedMphStore


async def chunks():
    yield b"placeholder-mph-bytes"


def test_http_stream_upload_and_separate_listing(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "imported_mph_store", ImportedMphStore(tmp_path / "models"))

    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.post("/api/imported-mph", content=b"test-mph", headers={"X-Workbench-Filename": "my%20model.mph"})
            assert response.status_code == 201
            item = response.json()
            assert item["original_filename"] == "my model.mph"
            listing = await client.get("/api/imported-mph")
            assert [found["id"] for found in listing.json()["items"]] == [item["id"]]

    asyncio.run(exercise())


def test_preview_refuses_to_replace_other_live_model(monkeypatch, tmp_path):
    from app import main

    item = asyncio.run(ImportedMphStore(tmp_path / "models").save("x.mph", chunks()))
    monkeypatch.setattr(main, "imported_mph_store", ImportedMphStore(tmp_path / "models"))
    monkeypatch.setattr(main.java_bridge_process_manager, "status", lambda: {"state": "ready", "running": True, "model_path": "another.mph"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.start_imported_mph_preview(item["id"]))
    assert error.value.status_code == 409


def test_preview_unavailable_is_not_certified(monkeypatch, tmp_path):
    from app import main

    item = asyncio.run(ImportedMphStore(tmp_path / "models").save("x.mph", chunks()))
    monkeypatch.setattr(main, "imported_mph_store", ImportedMphStore(tmp_path / "models"))
    monkeypatch.setattr(main.java_bridge_process_manager, "status", lambda: {"state": "idle", "running": False})
    preview = asyncio.run(main.read_imported_mph_preview(item["id"]))
    assert preview["preview_state"] == "unavailable"
    assert preview["model_state_verified"] is False
    assert preview["scientific_validation_passed"] is False
