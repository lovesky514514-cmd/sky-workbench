"""Versioned local evidence store; source text remains distinct from claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3

import networkx as nx

from app.config import WORKSPACE_ROOT, app_paths
from app.knowledge.contracts import evidence_anchor, metadata_matches, normalized_metadata, query_terms, validate_filters


class SourceKnowledgeStore:
    def __init__(self, path: Path | None):
        self.path = path

    def _connect(self):
        if self.path is None:
            raise ValueError("No source-store path is configured")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.create_function("metadata_matches", 2, lambda raw, criteria: int(metadata_matches(json.loads(raw), json.loads(criteria))), deterministic=True)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS sources (
                unit_id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                engine TEXT NOT NULL, project_id TEXT, metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS source_engine_project ON sources(engine,project_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(unit_id UNINDEXED,title,content);
        ''')
        return db

    def import_units(self, units: list[dict], *, project_id: str | None, bundled: bool = False) -> dict:
        prepared = []
        for unit in units:
            content, unit_id = str(unit.get("content", "")).strip(), str(unit.get("unit_id", "")).strip()
            if not content or len(content.encode()) > 64000 or not re.fullmatch(r"[A-Za-z0-9_:-]{1,150}", unit_id):
                raise ValueError("Each knowledge unit needs a stable ID and 1–64000 bytes of text")
            metadata = dict(unit.get("metadata", {}))
            if not bundled:
                metadata.update(project_id=project_id, license_class="user_local_only",
                                verification_status="UNVERIFIED", linked_case_id=None)
            metadata = normalized_metadata(content, **metadata)
            if not bundled:
                metadata["parent_unit_ids"] = [f"project:{project_id}:{parent}" for parent in metadata["parent_unit_ids"]]
            # Project imports cannot overwrite another project's or a bundled identity.
            key = unit_id if bundled else f"project:{project_id}:{unit_id}"
            prepared.append((key, str(unit.get("title") or unit_id)[:300], content, metadata))
        if len({item[0] for item in prepared}) != len(prepared):
            raise ValueError("Duplicate knowledge unit IDs")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            graph = nx.DiGraph()
            for row in db.execute("SELECT unit_id,metadata_json FROM sources"):
                metadata = json.loads(row["metadata_json"])
                graph.add_edges_from((parent, row["unit_id"]) for parent in metadata.get("parent_unit_ids", []))
            for key, _, _, metadata in prepared:
                if key in graph:
                    graph.remove_edges_from(list(graph.in_edges(key)))
                graph.add_edges_from((parent, key) for parent in metadata.get("parent_unit_ids", []))
            if not nx.is_directed_acyclic_graph(graph):
                raise ValueError("Knowledge source lineage cannot contain a cycle")
            for key, title, content, metadata in prepared:
                db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)",
                           (key, title, content, metadata["engine"], metadata["project_id"], json.dumps(metadata, ensure_ascii=False)))
                db.execute("DELETE FROM sources_fts WHERE unit_id=?", (key,))
                db.execute("INSERT INTO sources_fts VALUES(?,?,?)", (key, title, content))
        return {"imported_units": len(prepared), "verification_status": "UNVERIFIED"}

    def search(self, query: str, limit: int, *, filters: dict | None = None) -> list[dict]:
        filters = validate_filters(filters)
        if self.path is None or not self.path.is_file():
            return []
        terms = sorted(query_terms(query)) + re.findall(r"[\u4e00-\u9fff]+", query)
        terms = [word for term in terms[:40] for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", term)]
        if not terms:
            return []
        expression = " OR ".join('"' + term + '"' for term in terms)
        with self._connect() as db:
            rows = db.execute('''
                SELECT s.*,bm25(sources_fts) AS rank FROM sources_fts
                JOIN sources s ON s.unit_id=sources_fts.unit_id
                WHERE sources_fts MATCH ? AND metadata_matches(s.metadata_json,?)
                ORDER BY rank,s.unit_id LIMIT ?
            ''', (expression, json.dumps(filters), min(200, max(limit * 8, 40)))).fetchall()
        results = []
        for row in rows:
            if not evidence_anchor(query, row["content"] + " " + row["title"]):
                continue
            metadata = json.loads(row["metadata_json"])
            if hashlib.sha256(row["content"].encode()).hexdigest() != metadata["chunk_sha256"]:
                continue
            results.append({"text": row["content"][:2200], "score": 1 / (1 + abs(row["rank"])),
                            "retrieval_mode": "versioned_fts", "metadata": metadata,
                            "evidence": {**metadata, "unit_id": row["unit_id"], "claim_verification": "unverified",
                                         "source_locatable": bool(metadata["source_url"])},
                            "citation": {"document_id": row["unit_id"], "chunk_id": row["unit_id"],
                                         "file": metadata["source_url"], "file_name": row["title"], "title": row["title"],
                                         "page": None, "page_status": "page_unknown", "excerpt": row["content"][:420],
                                         "source_type": "Versioned local knowledge"}})
            if len(results) >= limit:
                break
        return results

    def status(self) -> dict:
        if self.path is None or not self.path.is_file():
            return {"units": 0, "engines": {}, "lineage_backend": "networkx-3.7"}
        with self._connect() as db:
            counts = {row[0]: row[1] for row in db.execute("SELECT engine,count(*) FROM sources GROUP BY engine")}
        return {"units": sum(counts.values()), "engines": counts, "lineage_backend": "networkx-3.7"}

    def load_bundled(self) -> dict:
        import sys

        candidates = [app_paths.resource_dir / "resources" / "public_knowledge",
                      Path(getattr(sys, "_MEIPASS", app_paths.project_root)) / "public_knowledge",
                      app_paths.project_root / "knowledge" / "public"]
        folder = next((item for item in candidates if (item / "manifest.json").is_file()), None)
        if folder is None:
            return {"status": "UNAVAILABLE", "units": 0}
        manifest = json.loads((folder / "manifest.json").read_bytes())
        data = (folder / "units.json").read_bytes()
        if (manifest.get("vendor_manuals_included") is not False
                or hashlib.sha256(data).hexdigest() != manifest.get("files", {}).get("units.json")):
            raise ValueError("Bundled public knowledge manifest does not match")
        units = json.loads(data)
        if len(units) != manifest.get("units"):
            raise ValueError("Bundled public knowledge unit count does not match")
        return {"status": "LOADED", **self.import_units(units, project_id=None, bundled=True)}


source_store = SourceKnowledgeStore(WORKSPACE_ROOT / "knowledge_base" / "versioned_sources.sqlite3")
