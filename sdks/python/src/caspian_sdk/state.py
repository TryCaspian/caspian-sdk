"""State and deduplication adapters for stateless and multi-instance deployments."""

import threading
import time
import uuid
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol


class StateAdapter(Protocol):
    """Protocol for state management: atomic dedup and conversation locks."""

    def seen(self, event_id: str, ttl: float = 86400.0) -> bool:
        """Atomic deduplication check.

        Returns True if event_id was already seen (duplicate), False if first time.
        """
        ...

    def lock(self, conversation_id: str, ttl: float = 30.0) -> AbstractContextManager[None]:
        """Best-effort per-conversation lock context manager."""
        ...


class InMemoryStateAdapter:
    """In-memory state adapter suitable for single-process deployments."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _cleanup_expired_seen(self, now: float) -> None:
        expired = [eid for eid, exp in self._seen.items() if exp <= now]
        for eid in expired:
            del self._seen[eid]

    def seen(self, event_id: str, ttl: float = 86400.0) -> bool:
        now = time.time()
        with self._global_lock:
            self._cleanup_expired_seen(now)
            if event_id in self._seen:
                return True
            self._seen[event_id] = now + ttl
            return False

    @contextmanager
    def lock(self, conversation_id: str, ttl: float = 30.0):
        with self._global_lock:
            if conversation_id not in self._locks:
                self._locks[conversation_id] = threading.Lock()
            lock = self._locks[conversation_id]

        lock.acquire()
        try:
            yield
        finally:
            lock.release()


class RedisStateAdapter:
    """Redis-backed state adapter for distributed/stateless deployments.

    Accepts any duck-typed Redis client (redis-py or compatible fake client).
    """

    def __init__(self, redis_client: Any, prefix: str = "caspian:") -> None:
        self.client = redis_client
        self.prefix = prefix

    def seen(self, event_id: str, ttl: float = 86400.0) -> bool:
        key = f"{self.prefix}seen:{event_id}"
        ttl_seconds = max(1, int(ttl))
        # SET key 1 NX EX ttl
        # If set succeeds (returns True or 'OK'), it was NOT seen before.
        res = self.client.set(key, "1", nx=True, ex=ttl_seconds)
        if res is True or res == "OK" or res == 1:
            return False  # First time seen
        return True  # Already seen (duplicate)

    @contextmanager
    def lock(self, conversation_id: str, ttl: float = 30.0):
        key = f"{self.prefix}lock:{conversation_id}"
        token = uuid.uuid4().hex
        ttl_seconds = max(1, int(ttl))
        acquired = False
        start_time = time.monotonic()
        timeout = ttl

        while not acquired:
            res = self.client.set(key, token, nx=True, ex=ttl_seconds)
            if res is True or res == "OK" or res == 1:
                acquired = True
                break
            if time.monotonic() - start_time >= timeout:
                break
            time.sleep(0.05)

        try:
            yield
        finally:
            if acquired:
                # Release lock if token matches
                lua_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                try:
                    if hasattr(self.client, "eval"):
                        self.client.eval(lua_script, 1, key, token)
                    else:
                        val = self.client.get(key)
                        if val == token or (isinstance(val, bytes) and val == token.encode()):
                            del_fn = getattr(self.client, "delete", None) or getattr(
                                self.client, "del", None
                            )
                            if del_fn:
                                del_fn(key)
                except Exception:
                    # Fallback check
                    val = getattr(self.client, "get", lambda k: None)(key)
                    if val == token or (isinstance(val, bytes) and val == token.encode()):
                        del_fn = getattr(self.client, "delete", None) or getattr(
                            self.client, "del", None
                        )
                        if del_fn:
                            del_fn(key)
