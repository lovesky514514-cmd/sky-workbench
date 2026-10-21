"""Fetch only user-named arXiv/PMC records through fixed official APIs."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx


ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")
PMC_ID = re.compile(r"PMC\d+")
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def parse_source_url(url: str) -> tuple[str, str]:
    """Convert a strict article URL to an identifier; never fetch this URL itself."""
    try:
        parsed = urlsplit(url.strip())
        safe = parsed.scheme == "https" and not parsed.username and not parsed.password and not parsed.port and not parsed.query and not parsed.fragment
    except ValueError as exc:
        raise ValueError("source_not_allowed") from exc
    if not safe:
        raise ValueError("source_not_allowed")
    if parsed.hostname == "arxiv.org":
        match = re.fullmatch(r"/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?", parsed.path)
        if match and ARXIV_ID.fullmatch(match.group(1)):
            return "arxiv", match.group(1)
    if parsed.hostname == "pmc.ncbi.nlm.nih.gov":
        match = re.fullmatch(r"/articles/(PMC\d+)/?", parsed.path)
        if match and PMC_ID.fullmatch(match.group(1)):
            return "pmc", match.group(1)
    raise ValueError("source_not_allowed")


def _cutoff(today: date) -> date:
    # Rolling calendar years are more honest than a fixed 1095-day cutoff.
    try:
        return today.replace(year=today.year - 3)
    except ValueError:
        return today.replace(year=today.year - 3, day=28)


def _date(raw: str, today: date) -> date:
    try:
        published = date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError("source_outside_three_year_window") from exc
    if not _cutoff(today) <= published <= today:
        raise ValueError("source_outside_three_year_window")
    return published


def _plain(element: ET.Element | None) -> str:
    return " ".join(" ".join(element.itertext()).split()) if element is not None else ""


def _arxiv_record(root: ET.Element, article_id: str, today: date) -> dict[str, str]:
    entry = root.find(f"{ATOM}entry")
    if entry is None:
        raise ValueError("source_record_unavailable")
    identity = urlsplit(_plain(entry.find(f"{ATOM}id")))
    returned_id = identity.path.removeprefix("/abs/")
    exact = returned_id == article_id or ("v" not in article_id and bool(re.fullmatch(re.escape(article_id) + r"v\d+", returned_id)))
    if identity.scheme not in {"http", "https"} or identity.hostname != "arxiv.org" or not identity.path.startswith("/abs/") or not exact:
        raise ValueError("source_record_unavailable")
    published = _date(_plain(entry.find(f"{ATOM}published")), today)
    return {
        "source_kind": "arxiv", "source_url": f"https://arxiv.org/abs/{article_id}",
        "source_title": _plain(entry.find(f"{ATOM}title"))[:240],
        "publication_date": published.isoformat(), "text": _plain(entry.find(f"{ATOM}summary"))[:50_000],
    }


def _pmc_record(root: ET.Element, article_id: str, today: date) -> dict[str, str]:
    meta = root.find(".//article-meta")
    if meta is None or not any(_plain(item) == article_id for item in meta.findall("article-id")):
        raise ValueError("source_record_unavailable")
    dates = []
    for item in meta.findall("pub-date"):
        try:
            dates.append(date(int(_plain(item.find("year"))), int(_plain(item.find("month"))), int(_plain(item.find("day")))))
        except (ValueError, TypeError):
            continue
    if not dates:
        raise ValueError("source_outside_three_year_window")
    published = _date(min(dates).isoformat(), today)
    return {
        "source_kind": "pmc", "source_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{article_id}/",
        "source_title": _plain(meta.find("title-group/article-title"))[:240],
        "publication_date": published.isoformat(), "text": _plain(meta.find("abstract"))[:50_000],
    }


async def fetch_recent_source(url: str, *, today: date | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, str]:
    """Get article metadata/abstract only, with no redirects, PDF download or crawl."""
    source, article_id = parse_source_url(url)
    endpoint = "https://export.arxiv.org/api/query" if source == "arxiv" else "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"id_list": article_id} if source == "arxiv" else {"db": "pmc", "id": article_id, "rettype": "xml"}
    try:
        # The two fixed public endpoints use direct HTTPS. Inheriting a stale
        # Windows proxy can hang both live requests and controlled transports;
        # a connection failure is surfaced, never replaced by another source.
        async with httpx.AsyncClient(transport=transport, timeout=15, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", endpoint, params=params) as response:
                if response.is_redirect:
                    raise ValueError("source_redirect_rejected")
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_SOURCE_BYTES:
                        raise ValueError("source_too_large")
        # PMC's normal JATS response contains a DOCTYPE. ElementTree does not
        # fetch its external DTD, while rejecting inline ENTITY declarations
        # prevents expansion of attacker-controlled internal entity payloads.
        if b"<!ENTITY" in content.upper():
            raise ValueError("source_xml_unsafe")
        root = ET.fromstring(bytes(content))
    except (httpx.HTTPError, ET.ParseError) as exc:
        raise ValueError("source_fetch_failed") from exc
    record = _arxiv_record(root, article_id, today or date.today()) if source == "arxiv" else _pmc_record(root, article_id, today or date.today())
    if not record["source_title"] or not record["text"]:
        raise ValueError("source_record_unavailable")
    return record
