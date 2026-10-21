"""Traceable text/PDF ingestion for Scientific RAG 2.0."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from app.config import LEGACY_ROOT, WORKSPACE_ROOT


TEXT_SUFFIXES = {".md", ".txt", ".rst"}
MAX_FILE_BYTES = 20_000_000
MAX_PDF_PAGES_PER_FILE = 80
MAX_LEGACY_DOCUMENTATION_PDFS = 8
TARGETED_PDF_PAGES: dict[str, tuple[int, ...]] = {
    # Page number is the stable user-guide page identity exposed in citations.
    "heattransfermoduleusersguide.pdf": (769,),
}
PAGE_MARKER = re.compile(r"^\s*===\s*PAGE\s+(\d+)\s*===\s*$", re.I | re.M)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    document_id: str
    file: str
    file_name: str
    title: str
    page: int | None
    section: str | None
    chunk_id: str
    text: str
    source_type: str
    ingestion_time: str
    authors: tuple[str, ...] = ()
    doi: str | None = None
    year: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["authors"] = list(self.authors)
        return payload


class KnowledgeIngestor:
    def __init__(self, index_path: Path | None = None) -> None:
        self.index_path = index_path or WORKSPACE_ROOT / "knowledge_base" / "chunks.jsonl"
        self.issues: list[str] = []

    def ingest(self, *, include_legacy: bool = True, persist: bool = True) -> list[KnowledgeChunk]:
        self.issues = []
        roots: list[tuple[Path, str, bool]] = [
            (WORKSPACE_ROOT / "research_notes", "Project Knowledge", False),
            (WORKSPACE_ROOT / "literature", "Scientific Literature", False),
            (WORKSPACE_ROOT / "comsol_documentation", "COMSOL Documentation", False),
            (WORKSPACE_ROOT / "past_runs", "Past Runs", False),
            (WORKSPACE_ROOT / "reports", "Past Runs", False),
        ]
        if include_legacy:
            roots = [
                (LEGACY_ROOT / "src" / "knowledge" / "prompts", "COMSOL Documentation", False),
                (LEGACY_ROOT / "docs", "COMSOL Documentation", True),
                (LEGACY_ROOT / "workspace" / "literature", "Scientific Literature", False),
                *roots,
            ]
        chunks: list[KnowledgeChunk] = []
        seen: set[str] = set()
        for root, source_type, bounded_legacy_docs in roots:
            if not root.exists():
                continue
            candidates = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES | {".pdf"}]
            if bounded_legacy_docs:
                text_files = sorted(path for path in candidates if path.suffix.lower() != ".pdf")
                pdf_files = sorted(
                    (path for path in candidates if path.suffix.lower() == ".pdf"),
                    key=lambda path: (-pdf_priority(path), str(path).lower()),
                )
                skipped = max(0, len(pdf_files) - MAX_LEGACY_DOCUMENTATION_PDFS)
                if skipped:
                    self.issues.append(
                        f"Cold-start PDF budget: indexed {MAX_LEGACY_DOCUMENTATION_PDFS} prioritized legacy manuals; "
                        f"deferred {skipped}. Add selected manuals to workspace/comsol_documentation for full project indexing."
                    )
                candidates = [*text_files, *pdf_files[:MAX_LEGACY_DOCUMENTATION_PDFS]]
            for path in candidates:
                resolved = str(path.resolve()).lower()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        self.issues.append(f"Skipped oversized file: {path}")
                        continue
                    chunks.extend(self._read_pdf(path, source_type) if path.suffix.lower() == ".pdf" else self._read_text(path, source_type))
                except (OSError, ValueError, RuntimeError) as exc:
                    self.issues.append(f"{path}: {type(exc).__name__}: {exc}")
        if persist:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text("\n".join(json.dumps(chunk.as_dict(), ensure_ascii=False) for chunk in chunks), encoding="utf-8")
        return chunks

    def load_persisted(self) -> list[KnowledgeChunk] | None:
        """Load a previous local index without rescanning source documents."""
        if not self.index_path.is_file():
            return None
        chunks: list[KnowledgeChunk] = []
        try:
            for line in self.index_path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                payload["authors"] = tuple(payload.get("authors") or ())
                chunks.append(KnowledgeChunk(**payload))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return chunks

    def _read_text(self, path: Path, source_type: str) -> list[KnowledgeChunk]:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        title = extract_title(text, path.stem)
        metadata = extract_metadata(text)
        pages = split_pages(text)
        return self._chunk_pages(path, source_type, title, pages, metadata)

    def _read_pdf(self, path: Path, source_type: str) -> list[KnowledgeChunk]:
        if not importlib.util.find_spec("pypdf"):
            self.issues.append(f"PDF skipped because pypdf is not installed: {path}")
            return []
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pdf_metadata = reader.metadata or {}
        title = str(pdf_metadata.get("/Title") or path.stem)
        author = str(pdf_metadata.get("/Author") or "").strip()
        pages: list[tuple[int | None, str]] = []
        full_sample = ""
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES_PER_FILE:
            self.issues.append(
                f"PDF page budget: indexed first {MAX_PDF_PAGES_PER_FILE}/{page_count} pages from {path}"
            )
        for page_number, page in enumerate(reader.pages[:MAX_PDF_PAGES_PER_FILE], 1):
            extracted = page.extract_text() or ""
            pages.append((page_number, extracted))
            full_sample += "\n" + extracted[:1500]
        targeted = TARGETED_PDF_PAGES.get(path.name.lower(), ())
        for page_number in targeted:
            if page_number <= MAX_PDF_PAGES_PER_FILE or page_number > page_count:
                continue
            extracted = reader.pages[page_number - 1].extract_text() or ""
            pages.append((page_number, extracted))
            full_sample += "\n" + extracted[:1500]
        if targeted:
            selected = [number for number in targeted if MAX_PDF_PAGES_PER_FILE < number <= page_count]
            if selected:
                self.issues.append(f"Targeted COMSOL reference pages indexed from {path.name}: {selected}")
        metadata = extract_metadata(full_sample)
        if author:
            metadata["authors"] = tuple(part.strip() for part in re.split(r"[;,]", author) if part.strip())
        return self._chunk_pages(path, source_type, title, pages, metadata)

    def _chunk_pages(
        self,
        path: Path,
        source_type: str,
        title: str,
        pages: list[tuple[int | None, str]],
        metadata: dict[str, Any],
    ) -> list[KnowledgeChunk]:
        resolved = str(path.resolve())
        document_id = hashlib.sha1(resolved.lower().encode()).hexdigest()[:16]
        ingestion_time = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        chunks: list[KnowledgeChunk] = []
        ordinal = 0
        for page, text in pages:
            for section, part in split_sections(text):
                for piece in split_text(part):
                    chunk_id = hashlib.sha1(f"{document_id}:{page}:{section}:{ordinal}".encode()).hexdigest()[:16]
                    ordinal += 1
                    chunks.append(
                        KnowledgeChunk(
                            document_id=document_id,
                            file=resolved,
                            file_name=path.name,
                            title=title,
                            page=page,
                            section=section,
                            chunk_id=chunk_id,
                            text=piece,
                            source_type=source_type,
                            ingestion_time=ingestion_time,
                            authors=metadata.get("authors", ()),
                            doi=metadata.get("doi"),
                            year=metadata.get("year"),
                        )
                    )
        return chunks


def extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip().lstrip("# ").strip()
        if candidate and not PAGE_MARKER.match(line):
            return candidate[:240]
    return fallback


def extract_metadata(text: str) -> dict[str, Any]:
    doi = DOI_RE.search(text[:20_000])
    year = YEAR_RE.search(text[:5_000])
    return {"authors": (), "doi": doi.group(0).rstrip(".,;") if doi else None, "year": int(year.group(0)) if year else None}


def split_pages(text: str) -> list[tuple[int | None, str]]:
    matches = list(PAGE_MARKER.finditer(text))
    if not matches:
        return [(None, text)]
    pages: list[tuple[int | None, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[start:end]))
    return pages


def split_sections(text: str) -> list[tuple[str | None, str]]:
    parts: list[tuple[str | None, str]] = []
    current_section: str | None = None
    current: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading:
            if current:
                parts.append((current_section, "\n".join(current)))
                current = []
            current_section = heading.group(1).strip()[:200]
        else:
            current.append(line)
    if current:
        parts.append((current_section, "\n".join(current)))
    return parts or [(None, text)]


def split_text(text: str, limit: int = 1400) -> Iterable[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > limit:
            yield current
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        yield current


knowledge_ingestor = KnowledgeIngestor()


def pdf_priority(path: Path) -> int:
    """Prefer broadly useful and heat-transfer manuals for the bounded legacy seed index."""
    normalized = path.stem.lower().replace("_", "")
    weights = {
        "heattransfer": 100,
        "comsolreferencemanual": 90,
        "introductiontocomsol": 80,
        "applicationprogramming": 70,
        "programmingreference": 60,
        "postprocessing": 50,
    }
    return max((weight for marker, weight in weights.items() if marker in normalized), default=0)
