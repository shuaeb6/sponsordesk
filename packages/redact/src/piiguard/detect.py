"""Pattern and dictionary based PII detection.

Structured identifiers (SSNs, account numbers, emails, phones) are found with
patterns. Free-text classes that patterns cannot find reliably (names, dates of
birth, balances) use context words or a caller-supplied dictionary of known
names, which in a service desk comes from the sponsor's own census.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Detection:
    start: int
    end: int
    kind: str
    value: str


_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("ssn", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"), 0),
    ("ssn", re.compile(r"(?i)\bSSN[:#\s]*(\d{9})\b"), 1),
    (
        "ssn_last4",
        re.compile(
            r"(?i)\b(?:SSN|social)[\w\s]{0,20}?(?:ending|last four)"
            r"(?:\s+in|\s+of)?[:\s]+(\d{4})\b"
        ),
        1,
    ),
    ("ein", re.compile(r"(?<![\d-])\d{2}-\d{7}(?![\d-])"), 0),
    ("account_number", re.compile(r"\bRK\d{10}\b"), 0),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 0),
    ("phone", re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}[.-]\d{3}[.-]\d{4}\b"), 0),
    ("dob", re.compile(r"(?i)\b(?:DOB|date of birth|born)[:\s]+(\d{1,2}/\d{1,2}/\d{4})"), 1),
    ("balance", re.compile(r"(?i)balance[\w\s]{0,12}?(\$[\d,]+(?:\.\d{2})?)"), 1),
    ("balance", re.compile(r"(?i)(\$[\d,]+(?:\.\d{2})?)\s+balance"), 1),
)


class Detector:
    def __init__(self, known_names: Iterable[str] = ()) -> None:
        names = sorted({n.strip() for n in known_names if n.strip()}, key=len, reverse=True)
        self._names = (
            re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b") if names else None
        )

    def detect(self, text: str) -> list[Detection]:
        found: list[Detection] = []
        for kind, pattern, group in _PATTERNS:
            for m in pattern.finditer(text):
                found.append(Detection(m.start(group), m.end(group), kind, m.group(group)))
        if self._names is not None:
            for m in self._names.finditer(text):
                found.append(Detection(m.start(), m.end(), "person_name", m.group()))
        return _resolve_overlaps(found)


def _resolve_overlaps(found: list[Detection]) -> list[Detection]:
    """Keep the longest detection where spans overlap; earlier wins on ties."""
    kept: list[Detection] = []
    for d in sorted(found, key=lambda d: (d.start, -(d.end - d.start))):
        if kept and d.start < kept[-1].end:
            continue
        kept.append(d)
    return kept
