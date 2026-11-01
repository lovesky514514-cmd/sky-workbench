"""Bind literal facts to named objects; ambiguous references stay unresolved."""

from dataclasses import asdict
import hashlib
import re

from app.scientific.domain_catalog import MATERIALS
from app.scientific.quantity import geometry_quantities, parse_quantity, TOKEN
from app.scientific.request_schema import (
    RequestBrief,
    RequestEntity,
    RequestFact,
    RequestRelation,
    PlanningQuestion,
    PlanningOption,
)

OBJECTS = (
    "铁块",
    "铝块",
    "铜块",
    "金属块",
    "铁板",
    "铝板",
    "铜板",
    "板",
    "球",
    "金属球",
    "金属棒",
    "铜棒",
    "梁",
    "支架",
    "芯片",
    "电极",
    "涂层",
    "样品",
    "块体",
    "水",
    "油",
    "空气",
    "block",
    "plate",
    "sphere",
    "sample",
    "water",
    "air",
)
MENTION = re.compile(
    r"(?:(?:第[一二三四五六七八九十0-9]+个|[一二三四0-9]+号))?"
    r"(?:"
    + "|".join(sorted(OBJECTS, key=len, reverse=True))
    + r")(?:[A-Z甲乙丙丁](?![a-z]))?",
    re.I,
)
TEMPERATURE = re.compile(rf"{TOKEN}\s*(?:℃|°C|\bK\b|摄氏度|开尔文)", re.I)


def utf16_span(text: str, span: tuple[int, int]) -> tuple[int, int]:
    return tuple(len(text[:index].encode("utf-16-le")) // 2 for index in span)


def _nearby_entity(text, span, mentions):
    left = max(text.rfind(mark, 0, span[0]) for mark in "，,。；;\n") + 1
    ends = [p for mark in "，,。；;\n" if (p := text.find(mark, span[1])) >= 0]
    right = min(ends) if ends else len(text)
    choices = [
        (start - span[1] if start >= span[1] else span[0] - end, entity)
        for start, end, entity in mentions
        if start >= left and end <= right and entity
    ]
    return min(choices, default=(0, None), key=lambda item: item[0])[1]


def bind_objects(text: str, brief: RequestBrief) -> RequestBrief:
    from app.scientific.model_features import requested_model_features

    brief.model_outline["model_features"] = requested_model_features(text)
    brief.request_sha256 = hashlib.sha256(text.encode()).hexdigest()
    entities = []
    mentions = []
    labels = {}
    for match in MENTION.finditer(text):
        label = match.group()
        # A fresh indefinite object in the same sentence may share its material/name.
        prefix = re.split(r"[，,。；;\n]", text[: match.start()])[-1][-25:]
        if (
            re.search(r"(?:两个|两块|各个|所有|各自的)$", prefix)
            and sum(e.kind == "body" for e in entities) >= 2
        ):
            # A collective reference does not introduce a third physical body.
            mentions.append((*match.span(), None))
            continue
        fresh = bool(
            re.search(r"(?:一个|一块|另一[块个]|another|a\s+).{0,18}$", prefix, re.I)
        )
        candidates = labels.get(label.casefold(), [])
        entity = candidates[0] if len(candidates) == 1 and not fresh else None
        if not candidates or fresh:
            kind = (
                "environment"
                if label.lower() in {"空气", "air"}
                else "medium"
                if label.lower() in {"水", "油", "water"}
                else "body"
            )
            material = next(
                (
                    value
                    for key, value in MATERIALS.items()
                    if key.casefold() in label.casefold()
                ),
                None,
            )
            entity = RequestEntity(
                id=f"entity-{len(entities) + 1}",
                label=label,
                kind=kind,
                material=material,
            )
            entities.append(entity)
            labels.setdefault(label.casefold(), []).append(entity)
        if entity:
            entity.mentions.append(match.span())
        mentions.append((*match.span(), entity))
    brief.entities = entities
    bodies = [e for e in entities if e.kind == "body"]
    scoped = len(bodies) > 1 or (
        bool(bodies) and any(e.kind == "medium" for e in entities)
    )
    if scoped:
        # Reconstruct each geometry and initial state instead of assigning the first one globally.
        brief.facts = [
            f
            for f in brief.facts
            if f.key not in {"width", "height", "length", "initial_temperature"}
            and not f.key.startswith("initial_temperature@")
        ]
        cursor = 0
        while cursor < len(text):
            dimensions = geometry_quantities(text[cursor:], 3) or geometry_quantities(
                text[cursor:], 2
            )
            if not dimensions:
                break
            spans = [
                (q.original_span[0] + cursor, q.original_span[1] + cursor)
                for q in dimensions
            ]
            entity = _nearby_entity(text, (spans[0][0], spans[-1][1]), mentions)
            for key, q, span in zip(("width", "height", "length"), dimensions, spans):
                brief.facts.append(
                    RequestFact(
                        key=key
                        + "@"
                        + (entity.id if entity else f"unresolved-{span[0]}"),
                        value=q.normalized_value if q.issue is None else q.original,
                        unit=q.si_unit,
                        original=text[slice(*span)],
                        span=span,
                        normalized=q.issue is None,
                        issue=q.issue,
                        entity_id=entity.id if entity else None,
                        role="geometry",
                        quantity={**asdict(q), "original_span": span},
                    )
                )
            cursor = max(end for _, end in spans)
        for match in TEMPERATURE.finditer(text):
            entity = _nearby_entity(text, match.span(), mentions)
            quantity = parse_quantity(match.group(), "temperature", start=match.start())
            role = (
                "environment_temperature"
                if entity and entity.kind == "environment"
                else "initial_temperature"
            )
            if not any(
                f.span == match.span() and f.entity_id == getattr(entity, "id", None)
                for f in brief.facts
            ):
                brief.facts.append(
                    RequestFact(
                        key=role
                        + "@"
                        + (entity.id if entity else f"unresolved-{match.start()}"),
                        value=quantity.normalized_value
                        if quantity.issue is None
                        else match.group(),
                        unit=quantity.si_unit,
                        original=match.group(),
                        span=match.span(),
                        normalized=quantity.issue is None,
                        issue=quantity.issue,
                        quantity=asdict(quantity),
                        entity_id=entity.id if entity else None,
                        role=role,
                    )
                )
    for fact in brief.facts:
        fact.utf16_span = utf16_span(text, fact.span)
        if fact.entity_id is None:
            entity = _nearby_entity(text, fact.span, mentions)
            if entity:
                fact.entity_id = entity.id
    for match in re.finditer(r"接触|贴合|贴在|contact", text, re.I):
        # Negated contacts are constraints, not relations that authorize coupling.
        if re.search(
            r"不(?:考虑|发生)?\s*$", text[max(0, match.start() - 5) : match.start()]
        ):
            continue
        ids = list(
            dict.fromkeys(
                entity.id
                for _, _, entity in mentions
                if entity and entity.kind == "body"
            )
        )
        if len(ids) >= 2:
            condition = (
                "perfect_contact_explicit"
                if re.search(r"理想热接触|perfect thermal contact", text, re.I)
                else "contact_resistance_required"
            )
            brief.relations.append(
                RequestRelation(
                    kind="contact",
                    entity_ids=ids,
                    original=match.group(),
                    span=match.span(),
                    condition=condition,
                )
            )
    if scoped:
        brief.model_outline["object_scope"] = "multiple_entities_requires_binding"
        brief.model_outline["geometry_status"] = "scoped_entities"
    if any(r.condition == "contact_resistance_required" for r in brief.relations):
        brief.questions.append(
            PlanningQuestion(
                key="contact_model",
                question="块体之间的接触如何建模？",
                options=[
                    PlanningOption(
                        id="ideal",
                        label="审核采用理想热接触",
                        consequence="假设接触温度与热流连续，忽略接触热阻。",
                    ),
                    PlanningOption(
                        id="provide",
                        label="提供接触热阻及区域",
                        consequence="按真实接触参数建立界面模型。",
                    ),
                    PlanningOption(
                        id="delegate",
                        label="让 AI 提出接触模型",
                        consequence="提出适用候选，审核后再绑定执行器。",
                    ),
                ],
            )
        )
    return brief
