from __future__ import annotations

import re
from collections.abc import Sequence

from caspian_mcp.privacy.types import CATEGORY_PRIORITY, REGEX_CATEGORIES, Category, Span

_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
)
_API_KEY = re.compile(
    r"\b(?:sk_live|sk_test|pk_live|pk_test)_[A-Za-z0-9]{6,}\b"
    r"|\bghp_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b",
)
_CARD = re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")
_PHONE = re.compile(
    r"(?<!\w)(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}(?!\w)",
)


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def resolve_overlaps(spans: Sequence[Span]) -> list[Span]:
    ranked = sorted(
        spans,
        key=lambda s: (
            -CATEGORY_PRIORITY[s.category],
            -(s.end - s.start),
            s.start,
        ),
    )
    kept: list[Span] = []
    for span in ranked:
        if any(span.overlaps(existing) for existing in kept):
            continue
        kept.append(span)
    kept.sort(key=lambda s: s.start)
    return kept


class RegexScanner:
    def __init__(self, allowed: frozenset[Category] | None = None) -> None:
        self._allowed = REGEX_CATEGORIES if allowed is None else (allowed & REGEX_CATEGORIES)

    def scan(self, text: str) -> list[Span]:
        found: list[Span] = []
        if Category.EMAIL in self._allowed:
            found.extend(_spans(text, _EMAIL, Category.EMAIL))
        if Category.IP_ADDRESS in self._allowed:
            found.extend(_spans(text, _IPV4, Category.IP_ADDRESS))
        if Category.API_KEY in self._allowed:
            found.extend(_spans(text, _API_KEY, Category.API_KEY))
        if Category.CREDIT_CARD in self._allowed:
            found.extend(_card_spans(text))
        if Category.PHONE in self._allowed:
            found.extend(_spans(text, _PHONE, Category.PHONE))
        return found


def scan_all(text: str, scanners: Sequence[object]) -> list[Span]:
    found: list[Span] = []
    for scanner in scanners:
        found.extend(scanner.scan(text))
    return resolve_overlaps(found)


def _spans(text: str, pattern: re.Pattern[str], category: Category) -> list[Span]:
    return [
        Span(start=m.start(), end=m.end(), value=m.group(0), category=category)
        for m in pattern.finditer(text)
    ]


def _card_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in _CARD.finditer(text):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if luhn_ok(digits):
            spans.append(
                Span(start=match.start(), end=match.end(), value=raw, category=Category.CREDIT_CARD)
            )
    return spans
