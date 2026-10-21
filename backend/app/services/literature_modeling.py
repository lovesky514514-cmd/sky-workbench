"""Evidence-bounded literature model proposals, with no execution authority."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
from typing import Any

import httpx
from pypdf import PdfReader

from app.agent.intent_compiler import compile_intent


SUPPORTED = {"Heat Transfer in Solids", "Transport of Diluted Species", "Solid Mechanics", "Primary Current Distribution"}
MAX_TEXT = 50_000
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PAGES = 40


def _windows(text: str) -> list[str]:
    # Paragraphs keep an auditable source span. Long PDF pages are split into
    # overlapping bounded windows; no synthesis is inserted between spans.
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    result = []
    for part in parts:
        if len(part) <= 3_000:
            result.append(part)
        else:
            result.extend(part[start:start + 3_000] for start in range(0, len(part), 2_500))
        if len(result) >= 80:
            break
    return result[:80] or [text[:3_000].strip()]


def _candidate(title: str, sources: list[tuple[int | None, str]], *, source_kind: str, warning: str = "") -> dict[str, Any]:
    best: tuple[int, int | None, str, dict[str, Any]] | None = None
    for page, text in sources:
        for excerpt in _windows(text):
            intent = compile_intent(excerpt).model_dump(mode="json")
            score = 5 * len(intent["physics"]) + 3 * len(intent["geometry"]) + len(intent["parameters"]) + len(intent["boundary_conditions"])
            if best is None or score > best[0]:
                best = score, page, excerpt, intent
    if best is None:
        best = 0, None, "", compile_intent("").model_dump(mode="json")
    _, page, excerpt, intent = best
    supported = bool(intent["physics"] and intent["geometry"] and set(intent["physics"]) <= SUPPORTED and not intent["validation_issues"])
    return {
        "source_title": title[:240], "source_kind": source_kind, "page_number": page,
        "excerpt": excerpt, "intent": intent,
        "status": "review_required" if supported else "insufficient_evidence",
        "can_open_plan": supported, "requires_user_decision": True,
        "modeling_started": False, "scientific_validation_passed": False,
        "warning": warning or None,
    }


def extract_text_candidate(source_title: str, text: str) -> dict[str, Any]:
    """Suggest a reviewable candidate only from the supplied source words."""
    if not source_title.strip() or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("invalid_literature_text")
    return _candidate(source_title.strip(), [(None, text)], source_kind="text")


def extract_pdf_candidate(filename: str, data: bytes) -> dict[str, Any]:
    """Read selectable PDF text in memory; never index or retain the PDF."""
    pages, warning = _pdf_pages(filename, data)
    return _candidate(filename, pages, source_kind="pdf", warning=warning)


def _pdf_pages(filename: str, data: bytes) -> tuple[list[tuple[int, str]], str]:
    """Share the same size, page, encryption and parsing limits in both modes."""
    if not filename.lower().endswith(".pdf") or any(char in filename for char in "/\\:\x00") or not data or len(data) > MAX_PDF_BYTES:
        raise ValueError("invalid_literature_pdf")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf_unsupported")
        pages = [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages[:MAX_PAGES])]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("pdf_read_failed") from exc
    warning = "first_pages_only" if len(reader.pages) > MAX_PAGES else ""
    return pages, warning


async def extract_text_candidate_deepseek(
    source_title: str, text: str, *, api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
    pages: list[tuple[int | None, str]] | None = None,
    source_kind: str = "text", warning: str = "",
) -> dict[str, Any]:
    """Let DeepSeek select one exact source window; local code owns all claims.

    Only bounded extracted text leaves the machine after the user enabled the
    cloud option. The API key is read from memory and is never stored or logged.
    No provider fallback is permitted when DeepSeek fails.
    """
    if not api_key:
        raise ValueError("deepseek_key_unavailable")
    sources = pages if pages is not None else [(None, text)]
    if not source_title.strip() or not sources or len(text) > MAX_TEXT:
        raise ValueError("invalid_literature_text")
    windows = [(page, window) for page, part in sources for window in _windows(part) if window]
    if not windows:
        raise ValueError("no_selectable_text")
    # A deterministic relevance rank bounds cost and never gives the model
    # authority to invent a quote, parameter, validation or COMSOL execution.
    ranked = sorted(windows, key=lambda item: -_window_score(item[1]))[:8]
    snippets = [{"window_id": index, "text": window} for index, (_, window) in enumerate(ranked)]
    prompt = "Choose the ONE window with the clearest explicit physics, geometry, parameters and boundary conditions. Return ONLY JSON with integer window_id. Never invent content."
    try:
        # This opt-in call targets one fixed HTTPS endpoint directly. A stale
        # Windows proxy must not reroute or hang the bounded source request.
        async with httpx.AsyncClient(transport=transport, timeout=30, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "deepseek-chat", "temperature": 0, "stream": False,
                      "max_tokens": 128, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": prompt},
                                   {"role": "user", "content": json.dumps(snippets, ensure_ascii=False)}]},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("deepseek_unavailable") from exc
    try:
        choice = payload["choices"][0]
        selected = json.loads(choice["message"]["content"])["window_id"]
        if choice.get("finish_reason") != "stop" or type(selected) is not int or not 0 <= selected < len(ranked):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("deepseek_invalid_selection") from exc
    page, excerpt = ranked[selected]
    result = _candidate(source_title, [(page, excerpt)], source_kind=source_kind, warning=warning or "cloud_assisted_unverified")
    result["extraction_method"] = "deepseek_window_selection"
    return result


async def extract_pdf_candidate_deepseek(filename: str, data: bytes, *, api_key: str) -> dict[str, Any]:
    """Parse locally first; send only bounded text windows, never PDF bytes."""
    pages, warning = await asyncio.to_thread(_pdf_pages, filename, data)
    return await extract_text_candidate_deepseek(filename, "", api_key=api_key, pages=pages, source_kind="pdf", warning=warning)


def _window_score(excerpt: str) -> int:
    intent = compile_intent(excerpt).model_dump(mode="json")
    return 5 * len(intent["physics"]) + 3 * len(intent["geometry"]) + len(intent["parameters"]) + len(intent["boundary_conditions"])
