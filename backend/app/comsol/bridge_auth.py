"""Private per-process HTTP credential for the Workbench-owned Java Bridge."""

from __future__ import annotations

import re
import secrets


BRIDGE_TOKEN_ENV = "COMSOL_WORKBENCH_JAVA_BRIDGE_TOKEN"
BRIDGE_TOKEN_HEADER = "X-Workbench-Bridge-Token"
_TOKEN_FORMAT = re.compile(r"[0-9a-f]{64}\Z")


def new_bridge_token() -> str:
    """Create 256 unpredictable bits; never reuse a desktop/API/license token."""
    return secrets.token_hex(32)


def valid_bridge_token(value: str | None) -> bool:
    """Reject absent or noncanonical tokens before making an HTTP request."""
    return isinstance(value, str) and _TOKEN_FORMAT.fullmatch(value) is not None
