"""Conservative redaction for application logs and diagnostic text."""

from __future__ import annotations

import re
import traceback


PATTERNS = (
    (re.compile(r'''(?i)(["'](?:api[_-]?key|access[_-]?token|session[_-]?token|password|authorization|cookie|secret)["']\s*:\s*["'])(?:\\.|[^"'\\\r\n])*(["'])'''), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|session[_-]?token|password)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)((?:proxy-authorization|authorization)\s*[:=]\s*)(?:[A-Za-z]+\s+)?[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:cookie|set-cookie)\s*[:=]\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:token|secret|credential)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+"), r"\1?[REDACTED_QUERY]"),
    (re.compile(r"\b[A-Za-z]:[\\/][^\r\n\"'<>]*"), "[REDACTED_PATH]"),
    (re.compile(r"\\\\[^\s\\]+\\[^\r\n\"'<>]*"), "[REDACTED_PATH]"),
    (re.compile(r"/(?:home|Users|mnt|tmp|var|opt)/[^\s\"'<>]*"), "[REDACTED_PATH]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_object(value):
    """先递归处理字段再序列化，避免 JSON 转义遮蔽凭据模式。"""
    if isinstance(value, dict):
        return {key: '[REDACTED]' if re.fullmatch(
            r'(?i)(?:api[_-]?key|access[_-]?token|session[_-]?token|password|authorization|cookie|secret)', str(key))
                else redact_object(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_object(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


class RedactingLogFilter:
    def filter(self, record: object) -> bool:
        get_message = getattr(record, "getMessage", None)
        if callable(get_message):
            setattr(record, "msg", redact_text(str(get_message())))
            setattr(record, "args", ())
        exception = getattr(record, 'exc_info', None)
        if exception:
            setattr(record, 'exc_text', redact_text(''.join(traceback.format_exception(*exception))))
            setattr(record, 'exc_info', None)
        return True
