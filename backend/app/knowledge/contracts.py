"""Common source identity and hard retrieval constraints for all engines."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ENGINE_NAMES = {name.casefold(): name for name in (
    "COMSOL", "MATLAB", "VASP", "Materials Studio", "LAMMPS", "Mathics3", "shared", "unknown",
)}
FILTER_KEYS = {"engine", "engine_version", "engine_version_min", "engine_version_max", "module", "physics", "interface",
               "feature_type", "document_version", "version_status", "type", "license_class", "verification_status",
               "project_id", "include_shared", "source_url", "chunk_sha256"}
STOP_WORDS = set("a an the is are what how when where why can do does for of in on to and or please explain tell me about using command version".split())
ALIASES = {"绝热": "thermal insulation", "传热": "heat transfer", "均方位移": "compute msd",
           "时间步长": "timestep", "周期边界": "boundary periodic", "力场": "force field",
           "收敛": "convergence", "单点能": "single point energy", "晶体结构": "crystal structure"}


class KnowledgeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    engine: str
    module: str
    engine_version_min: str | None = None
    engine_version_max: str | None = None
    applies_to_versions: list[str] = Field(default_factory=list)
    version_independent: bool = False
    license_class: Literal["public_redistributable", "user_local_only", "reference_only"]
    verification_status: Literal["UNVERIFIED", "EXERCISED", "VALIDATED"] = "UNVERIFIED"
    source_url: str
    source_sha256: str | None = None
    chunk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision: str | None = None
    project_id: str | None = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    linked_case_id: str | None = None
    parent_unit_ids: list[str] = Field(default_factory=list)
    physics: str = "unknown"
    interface: str = "unknown"
    feature_type: str = "unknown"
    type: str = "reference"
    document_version: str = ""
    version_status: str = "unspecified"


def normalize_engine(value: str) -> str:
    try:
        return ENGINE_NAMES[value.casefold()]
    except KeyError as exc:
        raise ValueError("Unknown knowledge engine") from exc


def validate_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(filters or {})
    if set(result) - FILTER_KEYS:
        raise ValueError("Unsupported knowledge metadata filter")
    if result.get("engine"):
        values = result["engine"] if isinstance(result["engine"], list) else [result["engine"]]
        normalized = [normalize_engine(str(v)) for v in values]
        result["engine"] = normalized if isinstance(result["engine"], list) else normalized[0]
    return result


def _version(value: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", value.casefold())


def version_matches(metadata: dict, requested: str) -> bool:
    if metadata.get("version_independent"):
        return True
    versions = metadata.get("applies_to_versions", [])
    versions = [*versions, metadata.get("document_version", "")]
    if _version(requested) in {_version(str(v)) for v in versions if v}:
        return True
    low, high = metadata.get("engine_version_min"), metadata.get("engine_version_max")
    if low == high and low and _version(requested) == _version(low):
        return True
    if all(v and re.fullmatch(r"\d+(?:\.\d+)*", v) for v in (low, high, requested)):
        key = lambda value: tuple(int(x) for x in value.split("."))
        return key(low) <= key(requested) <= key(high)
    return False


def metadata_matches(metadata: dict, filters: dict[str, Any]) -> bool:
    if metadata.get("project_id") is not None and not filters.get("project_id"):
        return False
    for key, expected in filters.items():
        if key == "include_shared" or expected in (None, "", []):
            continue
        if key == "engine_version":
            if not version_matches(metadata, str(expected)):
                return False
            continue
        actual = metadata.get(key)
        choices = expected if isinstance(expected, list) else [expected]
        if key == "engine" and actual == "shared" and filters.get("include_shared") is True:
            continue
        # Global sources can be read by any local project; project data must match.
        if key == "project_id" and actual is None:
            continue
        if actual not in choices:
            return False
    return metadata.get("license_class") != "reference_only"


def normalized_metadata(content: str, **values) -> dict:
    values["engine"] = normalize_engine(values.get("engine", "unknown"))
    values["chunk_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    values.setdefault("module", "unknown")
    values.setdefault("license_class", "user_local_only")
    values.setdefault("source_url", "unknown:source")
    return KnowledgeMetadata.model_validate(values).model_dump(mode="json")


def query_terms(query: str) -> set[str]:
    for term, equivalent in ALIASES.items():
        query = query.replace(term, " " + equivalent + " ")
    return {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_./-]*", query.lower())
            if word not in STOP_WORDS}


def evidence_anchor(query: str, content: str) -> bool:
    terms = query_terms(query)
    if not terms:
        # Chinese words not covered by an alias require actual original-text overlap.
        terms = {part for part in re.findall(r"[\u4e00-\u9fff]{2,}", query)}
    lowered = content.casefold()
    matched = sum(term in lowered for term in terms)
    return bool(terms) and matched >= min(2, len(terms))
