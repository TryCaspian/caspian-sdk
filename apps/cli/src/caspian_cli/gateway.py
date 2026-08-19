from __future__ import annotations

from typing import Any, Protocol


class Gateway(Protocol):
    def request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | list[Any] | None = None,
    ) -> Any: ...
