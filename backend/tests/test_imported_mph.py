"""Imported MPH files stay separate from generated models and never certify a run."""

import asyncio
import hashlib

import pytest

from app.services.imported_mph import ImportedMphStore


async def chunks(*parts: bytes):
    for part in parts:
        yield part


def test_import_preserves_original_and_creates_distinct_working_copy(tmp_path):
    store = ImportedMphStore(tmp_path / "models")
    item = asyncio.run(store.save("sample.mph", chunks(b"model-", b"bytes")))
    folder = tmp_path / "models" / "imported" / item["id"]

    assert (folder / "original.mph").read_bytes() == b"model-bytes"
    assert (folder / "working.mph").read_bytes() == b"model-bytes"
    assert item["sha256"] == hashlib.sha256(b"model-bytes").hexdigest()
    assert item["preview_state"] == "not_loaded"
    assert item["scientific_validation_passed"] is False
    (folder / "working.mph").write_bytes(b"modified-copy")
    assert (folder / "original.mph").read_bytes() == b"model-bytes"
    assert store.list()[0]["id"] == item["id"]


@pytest.mark.parametrize("filename", ["wrong.txt", "../escape.mph", r"C:\private\model.mph", "empty.mph\x00"])
def test_import_rejects_unsupported_or_unsafe_names(tmp_path, filename):
    store = ImportedMphStore(tmp_path / "models")
    with pytest.raises(ValueError):
        asyncio.run(store.save(filename, chunks(b"x")))
    assert store.list() == []


@pytest.mark.parametrize("payload", [b"", b"12345"])
def test_import_rejects_empty_or_oversized_stream_without_leaving_item(tmp_path, payload):
    store = ImportedMphStore(tmp_path / "models", max_bytes=4)
    with pytest.raises(ValueError):
        asyncio.run(store.save("sample.mph", chunks(payload)))
    assert store.list() == []


def test_import_id_cannot_escape_owned_folder(tmp_path):
    store = ImportedMphStore(tmp_path / "models")
    with pytest.raises(KeyError):
        store.get("../../elsewhere")
