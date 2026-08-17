from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.fernet import Fernet

from caspian_mcp.privacy.types import MappingExpired

Clock = Callable[[], float]


@dataclass
class _Record:
    expires_at: float
    values: dict[str, bytes] = field(default_factory=dict)


class MappingStore:
    """In-memory Fernet Mapping. Process-local; gone on restart by design."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600,
        clock: Clock | None = None,
        fernet: Fernet | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock: Clock = clock or time.monotonic
        self._fernet = fernet or Fernet(Fernet.generate_key())
        self._maps: dict[str, _Record] = {}

    def create(self) -> str:
        mapping_id = str(uuid.uuid4())
        self._maps[mapping_id] = _Record(expires_at=self._clock() + self._ttl_seconds)
        return mapping_id

    def put(self, mapping_id: str, placeholder: str, plaintext: str) -> None:
        record = self._live(mapping_id)
        record.values[placeholder] = self._fernet.encrypt(plaintext.encode("utf-8"))

    def get_all(self, mapping_id: str) -> dict[str, str]:
        record = self._live(mapping_id)
        return {
            placeholder: self._fernet.decrypt(blob).decode("utf-8")
            for placeholder, blob in record.values.items()
        }

    def alive(self, mapping_id: str) -> bool:
        try:
            self._live(mapping_id)
        except MappingExpired:
            return False
        return True

    def refresh(self, mapping_id: str) -> None:
        record = self._live(mapping_id)
        record.expires_at = self._clock() + self._ttl_seconds

    def _live(self, mapping_id: str) -> _Record:
        self._expire()
        record = self._maps.get(mapping_id)
        if record is None:
            raise MappingExpired(mapping_id)
        if self._clock() >= record.expires_at:
            self._maps.pop(mapping_id, None)
            raise MappingExpired(mapping_id)
        return record

    def _expire(self) -> None:
        now = self._clock()
        dead = [mid for mid, rec in self._maps.items() if now >= rec.expires_at]
        for mid in dead:
            self._maps.pop(mid, None)
