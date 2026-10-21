"""Conservative redaction for application logs and diagnostic text."""

from __future__ import annotations

import re


PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|session[_-]?token|password)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class RedactingLogFilter:
    def filter(self, record: object) -> bool:
        get_message = getattr(record, "getMessage", None)
        if callable(get_message):
            setattr(record, "msg", redact_text(str(get_message())))
            setattr(record, "args", ())
        return True
