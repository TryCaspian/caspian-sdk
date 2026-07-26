from __future__ import annotations

import heapq
import secrets
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol



class StateLockTimeoutError(RuntimeError):
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Timed out acquiring state lock for conversation {conversation_id}")


class StateAdapter(Protocol):
    def seen(self, event_id: str) -> bool:
        """Return whether an event was already claimed."""

    def lock(self, conversation_id: str) -> AbstractContextManager[None]:
        """Return a context manager that serializes a conversation."""


@dataclass
class _LockState:
    mutex: Lock = field(default_factory=Lock)
    users: int = 0


class _InMemoryLock:
    def __init__(self, adapter: InMemoryStateAdapter, conversation_id: str) -> None:
        self._adapter = adapter
        self._conversation_id = conversation_id
        self._state: _LockState | None = None
        self._acquired = False

    def __enter__(self) -> None:
        if self._state is not None:
            raise RuntimeError("state lock cannot be entered twice")
        state = self._adapter._lock_state(self._conversation_id)
        state.mutex.acquire()
        self._state = state
        self._acquired = True
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.release()
        return False

    def release(self) -> None:
        if not self._acquired:
            return
        state = self._state
        self._acquired = False
        if state is None:
            return
        state.mutex.release()
        self._adapter._release_lock_state(self._conversation_id, state)


class InMemoryStateAdapter:
    def __init__(self, *, dedup_ttl_seconds: float = 24 * 60 * 60) -> None:
        if dedup_ttl_seconds <= 0:
            raise ValueError("dedup_ttl_seconds must be positive")
        self._dedup_ttl = dedup_ttl_seconds
        self._seen_lock = Lock()
        self._seen: dict[str, float] = {}
        self._expiry: list[tuple[float, str]] = []
        self._locks_lock = Lock()
        self._locks: dict[str, _LockState] = {}

    def seen(self, event_id: str) -> bool:
        now = time.monotonic()
        with self._seen_lock:
            while self._expiry and self._expiry[0][0] <= now:
                expires_at, expired_id = heapq.heappop(self._expiry)
                if self._seen.get(expired_id) == expires_at:
                    self._seen.pop(expired_id, None)
            expires_at = self._seen.get(event_id)
            if expires_at is not None and expires_at > now:
                return True
            expires_at = now + self._dedup_ttl
            self._seen[event_id] = expires_at
            heapq.heappush(self._expiry, (expires_at, event_id))
            return False

    def lock(self, conversation_id: str) -> AbstractContextManager[None]:
        return _InMemoryLock(self, conversation_id)

    def _lock_state(self, conversation_id: str) -> _LockState:
        with self._locks_lock:
            state = self._locks.setdefault(conversation_id, _LockState())
            state.users += 1
            return state

    def _release_lock_state(self, conversation_id: str, state: _LockState) -> None:
        with self._locks_lock:
            state.users -= 1
            if state.users == 0 and self._locks.get(conversation_id) is state:
                self._locks.pop(conversation_id, None)


_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class _RedisLock:
    def __init__(self, adapter: RedisStateAdapter, conversation_id: str) -> None:
        self._adapter = adapter
        self._conversation_id = conversation_id
        self._key: str | None = None
        self._token: str | None = None

    def __enter__(self) -> None:
        self._key, self._token = self._adapter._acquire(self._conversation_id)
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.release()
        return False

    def release(self) -> None:
        if self._key is None or self._token is None:
            return
        key, token = self._key, self._token
        self._key = self._token = None
        self._adapter._release(key, token)


class RedisStateAdapter:
    def __init__(
        self,
        client,
        *,
        namespace: str = "caspian",
        dedup_ttl_seconds: int = 24 * 60 * 60,
        lock_ttl_seconds: int = 30,
        lock_wait_timeout_seconds: float = 30,
        lock_retry_interval_seconds: float = 0.05,
    ) -> None:
        if not namespace:
            raise ValueError("namespace must not be empty")
        if dedup_ttl_seconds <= 0:
            raise ValueError("dedup_ttl_seconds must be positive")
        if lock_ttl_seconds <= 0:
            raise ValueError("lock_ttl_seconds must be positive")
        if lock_wait_timeout_seconds < 0:
            raise ValueError("lock_wait_timeout_seconds must be non-negative")
        if lock_retry_interval_seconds <= 0:
            raise ValueError("lock_retry_interval_seconds must be positive")
        self._client = client
        self._namespace = namespace
        self._dedup_ttl = dedup_ttl_seconds
        self._lock_ttl_ms = lock_ttl_seconds * 1000
        self._lock_wait_timeout = lock_wait_timeout_seconds
        self._lock_retry_interval = lock_retry_interval_seconds

    @classmethod
    def from_url(cls, url: str, **kwargs) -> RedisStateAdapter:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis support requires the optional 'redis' dependency. "
                "Install it with: pip install caspian-sdk[redis]"
            ) from exc
        return cls(redis.Redis.from_url(url), **kwargs)

    def seen(self, event_id: str) -> bool:
        key = f"{self._namespace}:seen:{event_id}"
        created = self._client.set(key, "1", nx=True, ex=self._dedup_ttl)
        return not bool(created)

    def lock(self, conversation_id: str) -> AbstractContextManager[None]:
        return _RedisLock(self, conversation_id)

    def _acquire(self, conversation_id: str) -> tuple[str, str]:
        key = f"{self._namespace}:lock:{conversation_id}"
        token = secrets.token_urlsafe(24)
        deadline = time.monotonic() + self._lock_wait_timeout
        while True:
            if self._client.set(key, token, nx=True, px=self._lock_ttl_ms):
                return key, token
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StateLockTimeoutError(conversation_id)
            time.sleep(min(self._lock_retry_interval, remaining))

    def _release(self, key: str, token: str) -> None:
        self._client.eval(_RELEASE_LOCK, 1, key, token)


__all__ = [
    "InMemoryStateAdapter",
    "RedisStateAdapter",
    "StateAdapter",
    "StateLockTimeoutError",
]
