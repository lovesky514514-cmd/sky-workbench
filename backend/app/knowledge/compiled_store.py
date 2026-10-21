"""Read-only adapter for the audited COMSOL compiled knowledge base.

The source Markdown and JSON files are never modified.  Every generated index,
manifest and embedding is stored in the Workbench-owned workspace.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np

from app.config import COMPILED_KB_ROOT, COMSOL_PDF_ROOT, WORKSPACE_ROOT


MAIN_MARKDOWN_FILES = (
    "01_COMSOL_CORE_KNOWLEDGE.md",
    "02_COMSOL_JAVA_API.md",
    "03_COMSOL_PHYSICS_RECIPES.md",
    "04_COMSOL_SOLVER_MESH_VALIDATION.md",
    "05_COMSOL_TROUBLESHOOTING.md",
)
REQUIRED_FILES = (*MAIN_MARKDOWN_FILES, "06_SOURCE_INDEX.json", "COMPILATION_REPORT.md", "REPAIR_REPORT.md")
UNIT_HEADER = re.compile(r"^##\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s+[—-]\s+(.+?)\s*$")
SCHEMA_VERSION = "1"
DEFAULT_NAMESPACE = "local-hash-384"


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    unit_id: str
    heading_title: str
    markdown_file: str
    start_offset: int
    end_offset: int
    content: str


@dataclass(slots=True)
class IndexBuildReport:
    status: str
    indexed: int
    metadata_updated: int
    skipped: int
    failed: int
    removed: int
    embeddings_indexed: int
    duration_ms: int
    model: str
    namespace: str
    source_hash: str
    errors: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


ProgressCallback = Callable[[dict[str, Any]], None]


class CompiledKnowledgeStore:
    """Map audited Knowledge Units to a Workbench-owned SQLite search index."""

    def __init__(self, kb_root: Path = COMPILED_KB_ROOT, db_path: Path | None = None) -> None:
        self.kb_root = kb_root
        self.source_index_path = kb_root / "06_SOURCE_INDEX.json"
        self.db_path = db_path or WORKSPACE_ROOT / "knowledge_base" / "compiled_kb.sqlite3"
        self._lock = RLock()
        self._source_payload: dict[str, Any] | None = None
        self._source_hashes: dict[str, str] | None = None

    def status(self) -> dict[str, Any]:
        required = {name: (self.kb_root / name).is_file() for name in REQUIRED_FILES}
        source_error: str | None = None
        try:
            source_counts = self._source_counts()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            source_counts = {"documents": 0, "knowledge_units": 0}
            source_error = f"{type(exc).__name__}: {exc}"
        database_ready = False
        unit_rows = embedding_rows = semantic_embedding_rows = 0
        manifest: dict[str, str] = {}
        index_error: str | None = None
        if self.db_path.is_file():
            try:
                with self._connect(readonly=True) as db:
                    unit_rows = int(db.execute("SELECT count(*) FROM units").fetchone()[0])
                    embedding_rows = int(
                        db.execute("SELECT count(*) FROM embeddings WHERE namespace = ?", (DEFAULT_NAMESPACE,)).fetchone()[0]
                    )
                    manifest = dict(db.execute("SELECT key, value FROM manifest").fetchall())
                    semantic_namespace = manifest.get("semantic_embedding_namespace", "")
                    if semantic_namespace:
                        semantic_embedding_rows = int(
                            db.execute("SELECT count(*) FROM embeddings WHERE namespace = ?", (semantic_namespace,)).fetchone()[0]
                        )
                database_ready = (
                    unit_rows == source_counts["knowledge_units"]
                    and manifest.get("schema_version") == SCHEMA_VERSION
                    and manifest.get("build_complete") == "true"
                )
            except (OSError, sqlite3.Error) as exc:
                index_error = f"{type(exc).__name__}: {exc}"
        return {
            "status": "Ready" if all(required.values()) and database_ready else "Warning",
            "readonly": True,
            "root": str(self.kb_root),
            "source_index": str(self.source_index_path),
            "required_files": required,
            "documents": source_counts["documents"],
            "knowledge_units": source_counts["knowledge_units"],
            "indexed_units": unit_rows,
            "embedding_rows": embedding_rows,
            "semantic_embedding_rows": semantic_embedding_rows,
            "database_ready": database_ready,
            "index_path": str(self.db_path),
            "index_size_bytes": self.db_path.stat().st_size if self.db_path.is_file() else 0,
            "namespace": manifest.get("embedding_namespace", DEFAULT_NAMESPACE),
            "last_build": manifest.get("last_build"),
            "last_build_report": _decode_json(manifest.get("last_build_report"), None),
            "semantic_namespace": manifest.get("semantic_embedding_namespace"),
            "semantic_last_build": manifest.get("semantic_last_build"),
            "semantic_last_build_report": _decode_json(manifest.get("semantic_last_build_report"), None),
            "index_error": index_error or source_error,
        }

    def clear_semantic_index(self) -> dict[str, Any]:
        """Remove only rebuildable neural vectors; preserve sparse data and user files."""

        if not self.db_path.is_file():
            return {"status": "not_present", "removed_rows": 0, "index_path": str(self.db_path)}
        with self._lock, self._connect() as db:
            namespace_row = db.execute(
                "SELECT value FROM manifest WHERE key = 'semantic_embedding_namespace'"
            ).fetchone()
            namespace = str(namespace_row[0]) if namespace_row else ""
            removed = 0
            if namespace:
                removed = int(db.execute("SELECT count(*) FROM embeddings WHERE namespace = ?", (namespace,)).fetchone()[0])
                db.execute("DELETE FROM embeddings WHERE namespace = ?", (namespace,))
            db.execute("DELETE FROM manifest WHERE key LIKE 'semantic_%'")
            db.commit()
        return {"status": "cleared", "removed_rows": removed, "namespace": namespace, "index_path": str(self.db_path)}

    def build_index(
        self,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Incrementally index audited units without writing to ``kb_root``."""

        started = time.perf_counter()
        callback = progress or (lambda _: None)
        errors: list[str] = []
        counters = {
            "indexed": 0,
            "metadata_updated": 0,
            "skipped": 0,
            "failed": 0,
            "removed": 0,
            "embeddings_indexed": 0,
        }
        with self._lock:
            missing = [name for name in REQUIRED_FILES if not (self.kb_root / name).is_file()]
            if missing:
                raise FileNotFoundError(f"Compiled KB is incomplete: {', '.join(missing)}")
            source_hashes = self.source_hashes(refresh=True)
            source_hash = hashlib.sha256(
                json.dumps(source_hashes, sort_keys=True).encode("utf-8")
            ).hexdigest()
            payload = self._load_source_index(refresh=True)
            metadata_by_id = {str(item["id"]): item for item in payload["knowledge_units"]}
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            callback({"phase": "index", "status": "running", "completed": 0, "total": len(metadata_by_id)})
            with self._connect() as db:
                self._initialize_schema(db)
                self._write_documents(db, payload.get("documents", []))
                existing = {
                    str(row[0]): (int(row[1]), str(row[2]), str(row[3]))
                    for row in db.execute("SELECT unit_id, row_id, content_hash, metadata_hash FROM units")
                }
                embedded = {
                    str(row[0]): str(row[1])
                    for row in db.execute(
                        "SELECT unit_id, content_hash FROM embeddings WHERE namespace = ?", (namespace,)
                    )
                }
                seen: set[str] = set()
                completed = 0
                for markdown_name in MAIN_MARKDOWN_FILES:
                    for parsed in iter_markdown_units(self.kb_root / markdown_name):
                        completed += 1
                        seen.add(parsed.unit_id)
                        metadata = metadata_by_id.get(parsed.unit_id)
                        if metadata is None:
                            counters["failed"] += 1
                            errors.append(f"Unit {parsed.unit_id} is absent from 06_SOURCE_INDEX.json")
                            continue
                        try:
                            content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
                            metadata_hash = hashlib.sha256(
                                json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")
                            ).hexdigest()
                            prior = existing.get(parsed.unit_id)
                            if prior and prior[1] == content_hash and prior[2] == metadata_hash:
                                row_id = prior[0]
                                counters["skipped"] += 1
                            elif prior and prior[1] == content_hash:
                                row_id = prior[0]
                                self._update_unit_metadata(db, row_id, parsed, metadata, metadata_hash)
                                self._replace_fts(db, row_id, parsed.content, metadata)
                                counters["metadata_updated"] += 1
                            else:
                                row_id = self._upsert_unit(db, parsed, metadata, content_hash, metadata_hash)
                                self._replace_fts(db, row_id, parsed.content, metadata)
                                counters["indexed"] += 1

                            if embedded.get(parsed.unit_id) != content_hash:
                                vector = hash_embedding(parsed.content)
                                db.execute(
                                    """INSERT INTO embeddings(namespace, unit_id, content_hash, dimensions, vector)
                                       VALUES (?, ?, ?, ?, ?)
                                       ON CONFLICT(namespace, unit_id) DO UPDATE SET
                                         content_hash=excluded.content_hash,
                                         dimensions=excluded.dimensions,
                                         vector=excluded.vector""",
                                    (namespace, parsed.unit_id, content_hash, int(vector.size), vector.tobytes()),
                                )
                                counters["embeddings_indexed"] += 1
                            if completed % 250 == 0:
                                db.commit()
                                callback(
                                    {
                                        "phase": "index",
                                        "status": "running",
                                        "completed": completed,
                                        "total": len(metadata_by_id),
                                        **counters,
                                    }
                                )
                        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
                            counters["failed"] += 1
                            errors.append(f"{parsed.unit_id}: {type(exc).__name__}: {exc}")

                stale = sorted(set(existing) - seen)
                for unit_id in stale:
                    row_id = existing[unit_id][0]
                    db.execute("DELETE FROM units_fts WHERE rowid = ?", (row_id,))
                    db.execute("DELETE FROM embeddings WHERE unit_id = ?", (unit_id,))
                    db.execute("DELETE FROM units WHERE row_id = ?", (row_id,))
                    counters["removed"] += 1

                now = datetime.now(UTC).isoformat()
                report = IndexBuildReport(
                    status="passed" if not counters["failed"] and len(seen) == len(metadata_by_id) else "failed",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    model=namespace,
                    namespace=namespace,
                    source_hash=source_hash,
                    errors=errors[:100],
                    **counters,
                )
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "build_complete": "true" if report.status == "passed" else "false",
                    "source_hash": source_hash,
                    "source_hashes": json.dumps(source_hashes, sort_keys=True),
                    "embedding_namespace": namespace,
                    "last_build": now,
                    "last_build_report": json.dumps(report.as_dict(), ensure_ascii=False),
                }
                db.executemany(
                    """INSERT INTO manifest(key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    manifest.items(),
                )
                db.commit()
                db.execute("PRAGMA optimize")
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            callback({"phase": "index", "status": report.status, "completed": len(seen), "total": len(metadata_by_id), **counters})
            return report.as_dict()

    def bm25_search(
        self,
        query: str,
        *,
        limit: int = 80,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        match_query = fts_query(query)
        if not match_query or not self.db_path.is_file():
            return []
        where, values = _metadata_where(filters)
        sql = f"""SELECT u.unit_id, bm25(units_fts, 0.0, 8.0, 2.0, 2.0, 1.5, 1.0, 1.0, 0.6) AS rank
                  FROM units_fts
                  JOIN units u ON u.row_id = units_fts.rowid
                  WHERE units_fts MATCH ? AND u.excluded = 0 {where}
                  ORDER BY rank ASC LIMIT ?"""
        try:
            with self._connect(readonly=True) as db:
                rows = db.execute(sql, (match_query, *values, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"unit_id": str(row[0]), "score": round(1.0 / (1.0 + abs(float(row[1]))), 8)}
            for row in rows
        ]

    def dense_search(
        self,
        query: str,
        *,
        limit: int = 80,
        filters: dict[str, Any] | None = None,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        query_vector = hash_embedding(query)
        where, values = _metadata_where(filters, prefix="u")
        sql = f"""SELECT e.unit_id, e.vector
                  FROM embeddings e JOIN units u ON u.unit_id = e.unit_id
                  WHERE e.namespace = ? AND u.excluded = 0 {where}"""
        best: list[tuple[float, str]] = []
        with self._connect(readonly=True) as db:
            cursor = db.execute(sql, (namespace, *values))
            for unit_id, blob in cursor:
                vector = np.frombuffer(blob, dtype=np.float32)
                if vector.size != query_vector.size:
                    continue
                score = float(np.dot(vector, query_vector))
                if len(best) < limit:
                    heapq.heappush(best, (score, str(unit_id)))
                elif score > best[0][0]:
                    heapq.heapreplace(best, (score, str(unit_id)))
        return [
            {"unit_id": unit_id, "score": round(score, 8)}
            for score, unit_id in sorted(best, reverse=True)
            if score > 0
        ]

    def dense_search_vector(
        self,
        query_vector: np.ndarray,
        *,
        limit: int = 80,
        filters: dict[str, Any] | None = None,
        namespace: str,
    ) -> list[dict[str, Any]]:
        """Search a persisted derived index with a real normalized query vector."""

        if not self.db_path.is_file():
            return []
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if not vector.size or not np.all(np.isfinite(vector)):
            return []
        where, values = _metadata_where(filters, prefix="u")
        sql = f"""SELECT e.unit_id, e.vector, e.dimensions
                  FROM embeddings e JOIN units u ON u.unit_id = e.unit_id
                  WHERE e.namespace = ? AND u.excluded = 0 {where}"""
        best: list[tuple[float, str]] = []
        with self._connect(readonly=True) as db:
            for unit_id, blob, dimensions in db.execute(sql, (namespace, *values)):
                if int(dimensions) != vector.size:
                    continue
                candidate = np.frombuffer(blob, dtype=np.float32)
                score = float(np.dot(candidate, vector))
                if len(best) < limit:
                    heapq.heappush(best, (score, str(unit_id)))
                elif score > best[0][0]:
                    heapq.heapreplace(best, (score, str(unit_id)))
        return [
            {"unit_id": unit_id, "score": round(score, 8)}
            for score, unit_id in sorted(best, reverse=True)
        ]

    def embedding_count(self, namespace: str) -> int:
        if not self.db_path.is_file():
            return 0
        with self._connect(readonly=True) as db:
            return int(db.execute("SELECT count(*) FROM embeddings WHERE namespace = ?", (namespace,)).fetchone()[0])

    def build_neural_index(
        self,
        embed_batch: Callable[[list[str]], np.ndarray],
        *,
        namespace: str,
        dimensions: int,
        batch_size: int = 8,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Checkpoint a derived neural index without touching audited KB files."""

        started = time.perf_counter()
        callback = progress or (lambda _: None)
        if not self.status()["database_ready"]:
            raise RuntimeError("The metadata/BM25 index must be ready before neural indexing")
        counters = {"indexed": 0, "skipped": 0, "failed": 0}
        errors: list[str] = []
        with self._lock, self._connect() as db:
            rows = list(db.execute(
                """SELECT u.unit_id, u.content_hash, f.content
                   FROM units u JOIN units_fts f ON f.rowid = u.row_id
                   ORDER BY u.row_id"""
            ))
            existing = {
                str(unit_id): str(content_hash)
                for unit_id, content_hash in db.execute(
                    "SELECT unit_id, content_hash FROM embeddings WHERE namespace = ?", (namespace,)
                )
            }
            pending = [row for row in rows if existing.get(str(row[0])) != str(row[1])]
            counters["skipped"] = len(rows) - len(pending)
            callback({"phase": "semantic_index", "status": "running", "completed": counters["skipped"], "total": len(rows), **counters})
            for offset in range(0, len(pending), max(1, batch_size)):
                batch = pending[offset: offset + max(1, batch_size)]
                try:
                    vectors = np.asarray(embed_batch([str(row[2]) for row in batch]), dtype=np.float32)
                    if vectors.shape != (len(batch), dimensions) or not np.all(np.isfinite(vectors)):
                        raise ValueError(f"Embedding batch has invalid shape or values: {vectors.shape}")
                    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                    if np.any(norms <= 0):
                        raise ValueError("Embedding batch contains a zero vector")
                    vectors = vectors / norms
                    db.executemany(
                        """INSERT INTO embeddings(namespace, unit_id, content_hash, dimensions, vector)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(namespace, unit_id) DO UPDATE SET
                             content_hash=excluded.content_hash,
                             dimensions=excluded.dimensions,
                             vector=excluded.vector""",
                        [
                            (namespace, str(row[0]), str(row[1]), dimensions, vectors[index].tobytes())
                            for index, row in enumerate(batch)
                        ],
                    )
                    counters["indexed"] += len(batch)
                    db.commit()
                except Exception as exc:
                    counters["failed"] += len(batch)
                    errors.append(f"batch {offset}: {type(exc).__name__}: {exc}")
                    db.rollback()
                callback({
                    "phase": "semantic_index",
                    "status": "running",
                    "completed": counters["skipped"] + counters["indexed"] + counters["failed"],
                    "total": len(rows),
                    **counters,
                })
            valid_ids = {str(row[0]) for row in rows}
            stale = [unit_id for unit_id in existing if unit_id not in valid_ids]
            if stale:
                db.executemany(
                    "DELETE FROM embeddings WHERE namespace = ? AND unit_id = ?",
                    [(namespace, unit_id) for unit_id in stale],
                )
            count = int(db.execute("SELECT count(*) FROM embeddings WHERE namespace = ?", (namespace,)).fetchone()[0])
            complete = counters["failed"] == 0 and count == len(rows)
            report = {
                "status": "passed" if complete else "failed",
                "namespace": namespace,
                "model": namespace,
                "dimensions": dimensions,
                "knowledge_units": len(rows),
                "embedding_rows": count,
                "removed": len(stale),
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "errors": errors[:50],
                **counters,
            }
            db.executemany(
                """INSERT INTO manifest(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (
                    ("semantic_embedding_namespace", namespace),
                    ("semantic_last_build", datetime.now(UTC).isoformat()),
                    ("semantic_last_build_report", json.dumps(report, ensure_ascii=False)),
                ),
            )
            db.commit()
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        callback({"phase": "semantic_index", "status": report["status"], "completed": len(rows), "total": len(rows), **counters})
        return report

    def get_units(self, unit_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not unit_ids or not self.db_path.is_file():
            return {}
        placeholders = ",".join("?" for _ in unit_ids)
        sql = f"""SELECT u.*, f.content
                  FROM units u JOIN units_fts f ON f.rowid = u.row_id
                  WHERE u.unit_id IN ({placeholders})"""
        with self._connect(readonly=True) as db:
            rows = db.execute(sql, unit_ids).fetchall()
            documents = {
                str(row["document_id"]): dict(row)
                for row in db.execute("SELECT * FROM documents")
            }
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["sources"] = _decode_json(item.pop("sources_json"), [])
            item["applies_to_versions"] = _decode_json(item.pop("applies_to_versions_json"), [])
            item["source_document_ids"] = _decode_json(item.pop("source_document_ids_json"), [])
            item["documents"] = documents
            result[str(item["unit_id"])] = item
        return result

    def source_hashes(self, *, refresh: bool = False) -> dict[str, str]:
        if self._source_hashes is not None and not refresh:
            return dict(self._source_hashes)
        hashes: dict[str, str] = {}
        for name in (*MAIN_MARKDOWN_FILES, "06_SOURCE_INDEX.json"):
            path = self.kb_root / name
            if path.is_file():
                hashes[name] = _sha256_file(path)
        self._source_hashes = hashes
        return dict(hashes)

    def _source_counts(self) -> dict[str, int]:
        if not self.source_index_path.is_file():
            return {"documents": 0, "knowledge_units": 0}
        payload = self._load_source_index()
        return {
            "documents": len(payload.get("documents", [])),
            "knowledge_units": len(payload.get("knowledge_units", [])),
        }

    def _load_source_index(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._source_payload is None or refresh:
            payload = json.loads(self.source_index_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload.get("documents"), list) or not isinstance(payload.get("knowledge_units"), list):
                raise ValueError("06_SOURCE_INDEX.json must contain documents and knowledge_units arrays")
            self._source_payload = payload
        return self._source_payload

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            # A completed index is immutable during retrieval.  This also keeps
            # read-only clients from creating WAL/SHM side files.
            db = sqlite3.connect(
                f"file:{self.db_path.as_posix()}?mode=ro&immutable=1",
                uri=True,
                timeout=30,
            )
        else:
            db = sqlite3.connect(self.db_path, timeout=60)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _initialize_schema(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS manifest (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                recorded_path TEXT,
                resolved_path TEXT,
                title TEXT,
                version TEXT,
                module TEXT,
                document_type TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT
            );
            CREATE TABLE IF NOT EXISTS units (
                row_id INTEGER PRIMARY KEY,
                unit_id TEXT NOT NULL UNIQUE,
                type TEXT,
                title TEXT,
                module TEXT,
                physics TEXT,
                interface TEXT,
                feature_type TEXT,
                parent_context TEXT,
                document_version TEXT,
                version_status TEXT,
                applies_to_versions_json TEXT NOT NULL,
                source_document_ids_json TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                markdown_file TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                excluded INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                metadata_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_units_module ON units(module);
            CREATE INDEX IF NOT EXISTS idx_units_physics ON units(physics);
            CREATE INDEX IF NOT EXISTS idx_units_version ON units(document_version, version_status);
            CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
                unit_id UNINDEXED,
                title,
                module,
                physics,
                interface,
                feature_type,
                parent_context,
                content,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                namespace TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY(namespace, unit_id),
                FOREIGN KEY(unit_id) REFERENCES units(unit_id) ON DELETE CASCADE
            );
            """
        )

    def _write_documents(self, db: sqlite3.Connection, documents: list[dict[str, Any]]) -> None:
        pdf_paths: dict[str, str] = {}
        if COMSOL_PDF_ROOT.is_dir():
            for path in COMSOL_PDF_ROOT.glob("*/*.pdf"):
                pdf_paths.setdefault(path.name.lower(), str(path.resolve()))
        rows = []
        for document in documents:
            file_name = str(document.get("file_name", ""))
            rows.append(
                (
                    str(document.get("document_id", "")),
                    file_name,
                    str(document.get("full_path", "")),
                    pdf_paths.get(file_name.lower()),
                    str(document.get("title", "")),
                    str(document.get("version", "")),
                    str(document.get("module", "")),
                    str(document.get("document_type", "")),
                    int(document.get("page_count") or 0),
                    str(document.get("sha256", "")),
                )
            )
        db.executemany(
            """INSERT INTO documents(
                   document_id, file_name, recorded_path, resolved_path, title, version,
                   module, document_type, page_count, sha256
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET
                   file_name=excluded.file_name,
                   recorded_path=excluded.recorded_path,
                   resolved_path=excluded.resolved_path,
                   title=excluded.title,
                   version=excluded.version,
                   module=excluded.module,
                   document_type=excluded.document_type,
                   page_count=excluded.page_count,
                   sha256=excluded.sha256""",
            rows,
        )

    @staticmethod
    def _metadata_values(parsed: ParsedUnit, metadata: dict[str, Any], metadata_hash: str) -> tuple[Any, ...]:
        return (
            str(metadata.get("type", "Unknown")),
            str(metadata.get("title") or parsed.heading_title),
            str(metadata.get("module", "Unknown")),
            str(metadata.get("physics", "Unknown")),
            str(metadata.get("interface", "Unknown")),
            str(metadata.get("feature_type", "Unknown")),
            str(metadata.get("parent_context", "")),
            str(metadata.get("document_version", "")),
            str(metadata.get("version_status", "unspecified")),
            json.dumps(metadata.get("applies_to_versions", []), ensure_ascii=False),
            json.dumps(metadata.get("source_document_ids", []), ensure_ascii=False),
            json.dumps(metadata.get("sources", []), ensure_ascii=False),
            parsed.markdown_file,
            parsed.start_offset,
            parsed.end_offset,
            int(bool(metadata.get("excluded_from_main_rag", False))),
            metadata_hash,
        )

    def _upsert_unit(
        self,
        db: sqlite3.Connection,
        parsed: ParsedUnit,
        metadata: dict[str, Any],
        content_hash: str,
        metadata_hash: str,
    ) -> int:
        prior = db.execute("SELECT row_id FROM units WHERE unit_id = ?", (parsed.unit_id,)).fetchone()
        values = self._metadata_values(parsed, metadata, metadata_hash)
        if prior:
            row_id = int(prior[0])
            db.execute(
                """UPDATE units SET
                   type=?, title=?, module=?, physics=?, interface=?, feature_type=?, parent_context=?,
                   document_version=?, version_status=?, applies_to_versions_json=?, source_document_ids_json=?,
                   sources_json=?, markdown_file=?, start_offset=?, end_offset=?, excluded=?, metadata_hash=?, content_hash=?
                   WHERE row_id=?""",
                (*values, content_hash, row_id),
            )
            return row_id
        cursor = db.execute(
            """INSERT INTO units(
               unit_id, type, title, module, physics, interface, feature_type, parent_context,
               document_version, version_status, applies_to_versions_json, source_document_ids_json,
               sources_json, markdown_file, start_offset, end_offset, excluded, metadata_hash, content_hash
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (parsed.unit_id, *values, content_hash),
        )
        return int(cursor.lastrowid)

    def _update_unit_metadata(
        self,
        db: sqlite3.Connection,
        row_id: int,
        parsed: ParsedUnit,
        metadata: dict[str, Any],
        metadata_hash: str,
    ) -> None:
        values = self._metadata_values(parsed, metadata, metadata_hash)
        db.execute(
            """UPDATE units SET
               type=?, title=?, module=?, physics=?, interface=?, feature_type=?, parent_context=?,
               document_version=?, version_status=?, applies_to_versions_json=?, source_document_ids_json=?,
               sources_json=?, markdown_file=?, start_offset=?, end_offset=?, excluded=?, metadata_hash=?
               WHERE row_id=?""",
            (*values, row_id),
        )

    @staticmethod
    def _replace_fts(db: sqlite3.Connection, row_id: int, content: str, metadata: dict[str, Any]) -> None:
        db.execute("DELETE FROM units_fts WHERE rowid = ?", (row_id,))
        db.execute(
            """INSERT INTO units_fts(
               rowid, unit_id, title, module, physics, interface, feature_type, parent_context, content
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                str(metadata.get("id", "")),
                str(metadata.get("title", "")),
                str(metadata.get("module", "")),
                str(metadata.get("physics", "")),
                str(metadata.get("interface", "")),
                str(metadata.get("feature_type", "")),
                str(metadata.get("parent_context", "")),
                content,
            ),
        )


def iter_markdown_units(path: Path) -> Iterator[ParsedUnit]:
    """Yield complete audited units while retaining byte offsets for source lookup."""

    with path.open("rb") as handle:
        current_id: str | None = None
        current_title = ""
        current_start = 0
        content = bytearray()
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                if current_id is not None:
                    yield ParsedUnit(
                        current_id,
                        current_title,
                        path.name,
                        current_start,
                        handle.tell(),
                        content.decode("utf-8", errors="replace").strip(),
                    )
                break
            match = None
            if line.startswith(b"## "):
                match = UNIT_HEADER.match(line.decode("utf-8", errors="replace").strip())
            if match:
                if current_id is not None:
                    yield ParsedUnit(
                        current_id,
                        current_title,
                        path.name,
                        current_start,
                        line_start,
                        content.decode("utf-8", errors="replace").strip(),
                    )
                current_id = match.group(1)
                current_title = match.group(2)
                current_start = line_start
                content = bytearray(line)
            elif current_id is not None:
                content.extend(line)


def hash_embedding(text: str, dimensions: int = 384) -> np.ndarray:
    """Deterministic emergency embedding, explicitly not a neural model."""

    vector = np.zeros(dimensions, dtype=np.float32)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*|[\u4e00-\u9fff]{1,8}", text.lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector


def fts_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,64}|[\u4e00-\u9fff]{2,12}", query.lower())
    unique: list[str] = []
    for token in tokens:
        cleaned = token.replace('"', '""')
        if cleaned not in unique:
            unique.append(cleaned)
    return " OR ".join(f'"{token}"' for token in unique[:32])


def _metadata_where(filters: dict[str, Any] | None, prefix: str = "u") -> tuple[str, list[Any]]:
    if not filters:
        return "", []
    clauses: list[str] = []
    values: list[Any] = []
    for key in ("type", "module", "physics", "interface", "feature_type", "document_version", "version_status"):
        raw = filters.get(key)
        if raw in (None, "", []):
            continue
        candidates = raw if isinstance(raw, list) else [raw]
        placeholders = ",".join("?" for _ in candidates)
        clauses.append(f"{prefix}.{key} IN ({placeholders})")
        values.extend(str(item) for item in candidates)
    return (" AND " + " AND ".join(clauses), values) if clauses else ("", values)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decode_json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


compiled_store = CompiledKnowledgeStore()
