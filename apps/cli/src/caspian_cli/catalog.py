"""Catalog is the phone book. It does not send."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CACHE: list[dict[str, Any]] | None = None


def _catalog_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "vectors" / "cli_catalog.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("vectors/cli_catalog.json not found")


def load_catalog() -> list[dict[str, Any]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_catalog_path().read_text())
    return list(_CACHE)


def get_catalog(id: str) -> dict[str, Any]:
    for entry in load_catalog():
        if entry["id"] == id:
            return entry
    raise KeyError(id)


def search_catalog(query: str) -> list[dict[str, Any]]:
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return load_catalog()
    hits: list[dict[str, Any]] = []
    for entry in load_catalog():
        hay = " ".join(str(v) for v in entry.values()).lower()
        if all(word in hay for word in words):
            hits.append(entry)
    return hits
