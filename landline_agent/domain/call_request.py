"""The validation line.  Untrusted input becomes a ValidatedCallRequest once;
nothing downstream checks these fields again."""
from __future__ import annotations

from dataclasses import dataclass

from .number import REGULAR_TW, normalize

MAX_BRIEF = 8000
MAX_LABEL = 60
MAX_PURPOSE = 300
MIN_SEC, MAX_SEC = 30, 1800


@dataclass(frozen=True)
class ValidatedCallRequest:
    number: str
    brief: str
    label: str
    purpose: str
    max_sec: int


@dataclass(frozen=True)
class Invalid:
    errors: tuple[str, ...]


def validate(raw: dict) -> ValidatedCallRequest | Invalid:
    errors = []
    if not isinstance(raw, dict):
        return Invalid(("request must be an object",))
    number = normalize(str(raw.get("number", "")))
    if not REGULAR_TW.fullmatch(number):
        errors.append("number: must be a regular phone number with area code")
    brief = str(raw.get("brief", "")).strip()
    if not brief:
        errors.append("brief: required")
    elif len(brief) > MAX_BRIEF:
        errors.append(f"brief: longer than {MAX_BRIEF} characters")
    label = str(raw.get("label", "")).strip()
    if not label or len(label) > MAX_LABEL:
        errors.append(f"label: required, at most {MAX_LABEL} characters (who is being called)")
    purpose = str(raw.get("purpose", "")).strip()
    if not purpose or len(purpose) > MAX_PURPOSE:
        errors.append(f"purpose: required, at most {MAX_PURPOSE} characters (why)")
    try:
        max_sec = int(raw.get("max_sec", 300))
        if not MIN_SEC <= max_sec <= MAX_SEC:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"max_sec: integer between {MIN_SEC} and {MAX_SEC}")
        max_sec = 0
    if errors:
        return Invalid(tuple(errors))
    return ValidatedCallRequest(number, brief, label, purpose, max_sec)
