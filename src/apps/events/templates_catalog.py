"""Event templates: ready-made rubrics and settings for recurring event formats.

Templates live in ``data/templates/*.json`` so an organizer can add a new
format without touching code. Every template is validated with the same rules
as a hand-built rubric when it is loaded.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.events import services
from apps.events.models import Event
from apps.events.services import CriterionSpec
from core.http import not_found
from core.policy import Principal


@dataclass(frozen=True)
class EventTemplate:
    key: str
    name: str
    description: str
    weighting: str
    criteria: list[CriterionSpec]
    tracks: list[str] = field(default_factory=list)
    prizes: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    required_artifacts: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    source: str = ""


def _criterion(raw: dict[str, Any], *, bonus: bool = False) -> CriterionSpec:
    threshold = raw.get("gate_threshold")
    return CriterionSpec(
        key=raw["key"],
        label=raw["label"],
        weight=Decimal(str(raw.get("weight", 1))),
        min_score=int(raw.get("min", 1)),
        max_score=int(raw.get("max", 5)),
        description=raw.get("description", ""),
        anchors={str(k): v for k, v in raw.get("anchors", {}).items()},
        is_gate=bool(raw.get("gate", False)),
        gate_threshold=Decimal(str(threshold)) if threshold is not None else None,
        is_bonus=bonus,
    )


def parse(raw: dict[str, Any], origin: str) -> EventTemplate:
    criteria = [_criterion(item) for item in raw.get("criteria", [])]
    criteria += [_criterion(item, bonus=True) for item in raw.get("bonus_criteria", [])]
    template = EventTemplate(
        key=raw["key"],
        name=raw["name"],
        description=raw.get("description", ""),
        weighting=raw.get("weighting", "percent"),
        criteria=criteria,
        tracks=list(raw.get("tracks", [])),
        prizes=list(raw.get("prizes", [])),
        questions=list(raw.get("questions", [])),
        required_artifacts=list(raw.get("required_artifacts", [])),
        settings=dict(raw.get("settings", {})),
        source=raw.get("source", ""),
    )
    problems = services.spec_problems(template.weighting, template.criteria)
    if problems:
        raise ImproperlyConfigured(f"Template {origin} is invalid: {problems}")
    unknown = set(template.settings) - services.EDITABLE_FIELDS
    if unknown:
        raise ImproperlyConfigured(f"Template {origin} has unknown settings: {sorted(unknown)}")
    return template


@cache
def catalog() -> dict[str, EventTemplate]:
    directory = Path(settings.EVENT_TEMPLATES_DIR)
    templates: dict[str, EventTemplate] = {}
    for path in sorted(directory.glob("*.json")):
        template = parse(json.loads(path.read_text(encoding="utf-8")), path.name)
        if template.key in templates:
            raise ImproperlyConfigured(f"Duplicate template key {template.key!r}")
        templates[template.key] = template
    return templates


def get_template(key: str) -> EventTemplate:
    template = catalog().get(key)
    if template is None:
        raise not_found("No such event template.")
    return template


@transaction.atomic
def apply_template(actor: Principal, event: Event, key: str) -> Event:
    """Configure ``event`` from a template: settings, rubric, tracks, prizes and questions."""
    template = get_template(key)
    services.update_event(actor, event, **template.settings)
    services.set_rubric(actor, event, template.weighting, template.criteria)
    for name in template.tracks:
        if not event.tracks.filter(name=name).exists():
            services.add_track(actor, event, name)
    if not event.prizes.exists():
        for prize in template.prizes:
            services.add_prize(
                actor, event, prize["name"], int(prize["rank"]), prize.get("amount", "")
            )
    if not event.questions.exists():
        for question in template.questions:
            services.add_question(
                actor,
                event,
                question["label"],
                kind=question.get("kind", "text"),
                required=bool(question.get("required", False)),
                choices=question.get("choices"),
                help_text=question.get("help_text", ""),
            )
    event.template_key = key
    event.save(update_fields=["template_key", "updated_at"])
    return event
