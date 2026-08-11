"""Cheap deterministic validation for provider responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QualityReport:
    valid: bool
    reason: str | None = None
    parsed: Any = None


_BOILERPLATE = (
    "i am an ai assistant",
    "how may i assist",
    "welcome to",
    "customer support",
    "here are some ways i can help",
)
_REFUSAL = re.compile(r"\b(?:i can(?:not|'t)|unable to|不可以|cannot)\b", re.I)


def validate_output(output: str, *, require_json: bool = False, task: str = "") -> QualityReport:
    text = output.strip()
    if not text:
        return QualityReport(False, "empty_output")
    lowered = text.casefold()
    if any(phrase in lowered for phrase in _BOILERPLATE):
        return QualityReport(False, "boilerplate")
    if _REFUSAL.search(text) and len(text) < 500:
        return QualityReport(False, "refusal_only")
    if "[truncated]" in lowered or "…" in text[-12:]:
        return QualityReport(False, "truncated")
    if text.count("```") % 2:
        return QualityReport(False, "unclosed_code_fence")
    parsed = None
    if require_json:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return QualityReport(False, "malformed_json")
        if parsed is None or parsed == {} or parsed == []:
            return QualityReport(False, "empty_json")
    if task in {"classification", "extraction"} and len(text) > 200_000:
        return QualityReport(False, "output_too_large")
    return QualityReport(True, parsed=parsed)
