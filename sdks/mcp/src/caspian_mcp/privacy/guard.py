from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from caspian_mcp.privacy._mapping import MappingStore
from caspian_mcp.privacy._scanner import RegexScanner, scan_all
from caspian_mcp.privacy.types import (
    REGEX_CATEGORIES,
    Category,
    MappingExpired,
    SanitizeResult,
    Span,
    categories_from_env,
)

_PLACEHOLDER_KEY = re.compile(r"^\[(.+)_[0-9A-Fa-f]+\]$")
_PLACEHOLDER_HEX_CHARS = 32  # 128 bits


@dataclass
class Guard:
    """Regex Sanitize / Restore / Redaction Report. No spaCy by default."""

    store: MappingStore = field(default_factory=MappingStore)
    categories: frozenset[Category] | None = None
    _hmac_key: bytes = field(default_factory=lambda: os.urandom(32), repr=False, compare=False)
    _placeholders: dict[tuple[Category, str], str] = field(default_factory=dict, repr=False)
    _scanners: list[object] = field(init=False, repr=False)
    _categories: frozenset[Category] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        allowed = self.categories if self.categories is not None else categories_from_env()
        self._categories = allowed
        self.categories = allowed
        regex_allowed = allowed & REGEX_CATEGORIES
        self._scanners = [RegexScanner(allowed=regex_allowed)] if regex_allowed else []

    def _placeholder_for(self, category: Category, value: str) -> str:
        key = (category, value)
        cached = self._placeholders.get(key)
        if cached:
            return cached
        digest = hmac.new(
            self._hmac_key,
            f"{category}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()[:_PLACEHOLDER_HEX_CHARS].upper()
        placeholder = f"[{category}_{digest}]"
        self._placeholders[key] = placeholder
        return placeholder

    def sanitize(self, text: str, mapping_id: str | None = None) -> SanitizeResult:
        if mapping_id and self.store.alive(mapping_id):
            self.store.refresh(mapping_id)
        else:
            mapping_id = self.store.create()

        spans = [
            span for span in scan_all(text, self._scanners) if span.category in self._categories
        ]
        if not spans:
            return SanitizeResult(safe_text=text, mapping_id=mapping_id)

        assigned: dict[tuple[Category, str], str] = {}
        for span in spans:
            key = (span.category, span.value)
            if key not in assigned:
                placeholder = self._placeholder_for(span.category, span.value)
                assigned[key] = placeholder
                self.store.put(mapping_id, placeholder, span.value)

        safe = _replace_from_end(text, spans, assigned)
        return SanitizeResult(safe_text=safe, mapping_id=mapping_id)

    def restore(self, text: str, mapping_id: str) -> str:
        values = self.store.get_all(mapping_id)
        restored = text
        for placeholder, plaintext in sorted(values.items(), key=lambda kv: -len(kv[0])):
            restored = restored.replace(placeholder, plaintext)
        return restored

    def redaction_report(self, mapping_id: str) -> dict[str, int]:
        values = self.store.get_all(mapping_id)
        counts: dict[str, int] = {}
        for placeholder in values:
            match = _PLACEHOLDER_KEY.match(placeholder)
            if match is None:
                continue
            category = match.group(1)
            counts[category] = counts.get(category, 0) + 1
        return counts


def _replace_from_end(
    text: str,
    spans: list[Span],
    assigned: dict[tuple[Category, str], str],
) -> str:
    pieces: list[str] = []
    cursor = len(text)
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        pieces.append(text[span.end : cursor])
        pieces.append(assigned[(span.category, span.value)])
        cursor = span.start
    pieces.append(text[:cursor])
    pieces.reverse()
    return "".join(pieces)


__all__ = ["Guard", "MappingExpired"]
