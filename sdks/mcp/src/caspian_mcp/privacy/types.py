from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

ENV_CATEGORIES = "PRIVACY_GUARD_CATEGORIES"
_UNSET = object()


class Category(StrEnum):
    EMAIL = "EMAIL"
    IP_ADDRESS = "IP_ADDRESS"
    CREDIT_CARD = "CREDIT_CARD"
    API_KEY = "API_KEY"
    PHONE = "PHONE"
    PERSON = "PERSON"
    ORG = "ORG"


REGEX_CATEGORIES = frozenset(
    {
        Category.EMAIL,
        Category.IP_ADDRESS,
        Category.CREDIT_CARD,
        Category.API_KEY,
        Category.PHONE,
    }
)

CATEGORY_PRIORITY: dict[Category, int] = {
    Category.API_KEY: 100,
    Category.CREDIT_CARD: 90,
    Category.EMAIL: 80,
    Category.IP_ADDRESS: 70,
    Category.PHONE: 60,
    Category.PERSON: 50,
    Category.ORG: 50,
}


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    value: str
    category: Category

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    safe_text: str
    mapping_id: str


class MappingExpired(LookupError):
    """The Mapping Id is unknown or its TTL has elapsed."""


class MappingError(Exception):
    """The Mapping store refused an operation."""


def categories_from_env(raw: str | None | object = _UNSET) -> frozenset[Category]:
    """Allowlist. Unset → regex Categories only (no NER). Empty → none. Unknown names fail."""
    if raw is _UNSET:
        if ENV_CATEGORIES not in os.environ:
            return REGEX_CATEGORIES
        raw = os.environ[ENV_CATEGORIES]
    if raw is None:
        return REGEX_CATEGORIES
    text = str(raw).strip()
    if not text:
        return frozenset()
    found: list[Category] = []
    unknown: list[str] = []
    for part in text.split(","):
        name = part.strip().upper()
        if not name:
            continue
        try:
            found.append(Category(name))
        except ValueError:
            unknown.append(name)
    if unknown:
        raise ValueError(
            f"unknown {ENV_CATEGORIES} value(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(c.value for c in Category)}"
        )
    return frozenset(found)
