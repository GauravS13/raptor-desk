from typing import Any

from django import template

register = template.Library()

LABELS = {
    "weak_evidence": "fewer than 2 informative reviews",
}


@register.filter
def get_item(mapping: dict[Any, Any], key: Any) -> Any:
    return mapping.get(key)


@register.filter
def percent(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.0f}%"


@register.filter
def flag_label(flag: str) -> str:
    if flag in LABELS:
        return LABELS[flag]
    if flag.startswith("close_call_top"):
        return f"close call for top {flag.removeprefix('close_call_top')}"
    if flag.startswith("method_disagreement_top"):
        cutoff = flag.removeprefix("method_disagreement_top")
        return f"methods disagree on the top {cutoff}: decide with care"
    return flag.replace("_", " ")
