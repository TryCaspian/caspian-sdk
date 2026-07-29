"""Pluggable state and deduplication adapters for Caspian SDK."""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from threading import Lock
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("caspian_sdk.state")

LUA_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@runtime_checkable
class StateAdapter(Protocol):
    """Protocol defining the interface for Caspian state adapters."""

    async def seen(self, event_id: str) -> bool:
        """Atomic deduplication check.

        Returns True if `event_id` is new (and claims it). Returns False if it
        is a duplicate. Must not raise on duplicates.
        """
        ...

    def lock(self, conversation_id: str) -> AbstractAsyncContextManager[bool]:
        """Best-effort per-conversation lock yielding a bool (True if acquired, False if not).

        Must use an async context manager so locks cannot leak on exceptions.
        """
        ...


class InMemoryStateAdapter:
    """Default zero-config in-memory state adapter.

    Features bounded deduplication set (FIFO eviction) and thread-safe lazy per-conversation
    locks cleaned up when unused. Safe under multi-threaded execution.
    """

    def __init__(self, max_size: int = 10000) -> None:
        """Initialize in-memory state adapter.

        :param max_size: Maximum size of the deduplication set before oldest items are evicted.
        """
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._max_size = max_size
        self._seen: dict[str, None] = {}
        self._seen_lock = Lock()
        self._locks: dict[str, Lock] = {}
        self._lock_ref_counts: dict[str, int] = {}
        self._locks_guard = Lock()

    async def seen(self, event_id: str) -> bool:
        """Atomic claim of an event_id across threads. Returns True if new, False if duplicate."""
        with self._seen_lock:
            if event_id in self._seen:
                return False
            if len(self._seen) >= self._max_size:
                # Evict oldest entry (dict retains insertion order)
                oldest = next(iter(self._seen))
                del self._seen[oldest]
            self._seen[event_id] = None
            return True

    @asynccontextmanager
    async def lock(self, conversation_id: str) -> AsyncIterator[bool]:
        """Per-conversation lock yielding True if acquired, False if already locked."""
        with self._locks_guard:
            if conversation_id not in self._locks:
                self._locks[conversation_id] = Lock()
                self._lock_ref_counts[conversation_id] = 0
            lock_obj = self._locks[conversation_id]
            self._lock_ref_counts[conversation_id] += 1

        acquired = lock_obj.acquire(blocking=False)

        try:
            yield acquired
        finally:
            if acquired:
                lock_obj.release()

            with self._locks_guard:
                self._lock_ref_counts[conversation_id] -= 1
                if self._lock_ref_counts[conversation_id] == 0:
                    self._locks.pop(conversation_id, None)
                    self._lock_ref_counts.pop(conversation_id, None)


class RedisStateAdapter:
    """Redis-backed state adapter for multi-instance / distributed deployments."""

    def __init__(
        self,
        redis_client: Any = None,
        url: str | None = None,
        seen_ttl: int = 86400,
        lock_ttl: int = 30,
    ) -> None:
        """Initialize Redis state adapter.

        :param redis_client: Pre-configured redis async client instance.
        :param url: Redis connection URL if `redis_client` is not provided.
        :param seen_ttl: Deduplication key expiration in seconds. Default 86400 (24h).
            Rationale: Channel providers (Slack, Discord, Telegram, WhatsApp) retry failed
            webhook deliveries up to 24 hours. 24h ensures robust dedup across retry windows
            without unbounded key growth in Redis.
        :param lock_ttl: Per-conversation lock TTL in seconds. Default 30s.
            Rationale: 30s is long enough for typical handler runtimes (including LLM calls),
            while short enough that if a worker process crashes, the conversation lock
            auto-expires quickly without deadlocking the conversation indefinitely.
        """
        if redis_client is not None:
            self._redis = redis_client
        else:
            try:
                import redis.asyncio as aioredis
            except ImportError as exc:
                raise ImportError(
                    "redis-py is required to use RedisStateAdapter. "
                    "Install it with 'pip install redis'."
                ) from exc
            if url is not None:
                self._redis = aioredis.from_url(url)
            else:
                self._redis = aioredis.Redis()

        self._seen_ttl = seen_ttl
        self._lock_ttl = lock_ttl

    async def seen(self, event_id: str) -> bool:
        """Atomic SET event:{event_id} 1 NX EX <ttl>."""
        key = f"event:{event_id}"
        res = await self._redis.set(key, "1", nx=True, ex=self._seen_ttl)
        return bool(res)

    @asynccontextmanager
    async def lock(self, conversation_id: str) -> AsyncIterator[bool]:
        """Atomic SET lock:{conversation_id} <token> NX EX <ttl> and Lua release."""
        key = f"lock:{conversation_id}"
        token = str(uuid.uuid4())
        res = await self._redis.set(key, token, nx=True, ex=self._lock_ttl)
        acquired = bool(res)

        try:
            yield acquired
        finally:
            if acquired:
                # Release via Lua script so we only delete if token matches (avoids releasing a
                # lock that expired and was re-acquired by another worker).
                # TTL acts as the safety net if network issues prevent lock release.
                try:
                    await self._redis.eval(LUA_RELEASE_LOCK, 1, key, token)
                except Exception:
                    logger.exception(
                        "Failed to release Redis lock for conversation %s; "
                        "TTL will clean it up",
                        conversation_id,
                    )
