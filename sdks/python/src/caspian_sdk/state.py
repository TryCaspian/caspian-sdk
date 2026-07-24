import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import redis

logger = logging.getLogger("caspian_sdk")


class StateAdapter(Protocol):
    def seen(self, event_id: str) -> bool:
        """
        Record the event_id as seen.
        Returns True if the event was already seen, False if it was newly added.
        Must be atomic to prevent duplicates in multi-instance setups.
        """
        ...

    @contextmanager
    def lock(self, conversation_id: str) -> Iterator[None]:
        """
        Acquire a lock for the given conversation ID.
        Yields when the lock is acquired. Releases on exit.
        """
        ...


class InMemoryStateAdapter:
    """Default state adapter utilizing an LRU dict and threading locks."""
    
    def __init__(self, max_events: int = 1000):
        self._max_events = max_events
        self._events: dict[str, None] = {}
        self._events_lock = threading.Lock()
        
        self._conv_locks: dict[str, threading.Lock] = {}
        self._conv_locks_lock = threading.Lock()

    def seen(self, event_id: str) -> bool:
        with self._events_lock:
            if event_id in self._events:
                return True
            
            self._events[event_id] = None
            if len(self._events) > self._max_events:
                # Python dicts are insertion-ordered. Pop the oldest.
                self._events.pop(next(iter(self._events)))
            return False

    @contextmanager
    def lock(self, conversation_id: str) -> Iterator[None]:
        with self._conv_locks_lock:
            if conversation_id not in self._conv_locks:
                self._conv_locks[conversation_id] = threading.Lock()
            lock = self._conv_locks[conversation_id]
            
        with lock:
            yield


class RedisStateAdapter:
    """State adapter using Redis for distributed idempotency and locking."""
    
    def __init__(self, client: "redis.Redis", key_prefix: str = "caspian:"):
        self.client = client
        self.key_prefix = key_prefix
        self._dedup_ttl = 86400  # 24 hours
        self._lock_ttl = 30      # 30 seconds

    def seen(self, event_id: str) -> bool:
        key = f"{self.key_prefix}seen:{event_id}"
        # SET NX returns True if set (was not there), False if already existed
        is_new = self.client.set(key, "1", nx=True, ex=self._dedup_ttl)
        return not is_new

    @contextmanager
    def lock(self, conversation_id: str) -> Iterator[None]:
        key = f"{self.key_prefix}lock:{conversation_id}"
        import time
        import uuid
        token = str(uuid.uuid4())
        
        start = time.time()
        while True:
            # Try to acquire lock
            if self.client.set(key, token, nx=True, ex=self._lock_ttl):
                break
            if time.time() - start > 10:
                raise Exception(f"Timeout acquiring lock for {conversation_id}")
            time.sleep(0.05)
            
        try:
            yield
        finally:
            # Safe release using lua script
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            try:
                self.client.eval(script, 1, key, token)
            except Exception:
                # Fallback for fakeredis or systems without lua scripting enabled
                val = self.client.get(key)
                if val:
                    # redis-py returns bytes by default
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    if val == token:
                        self.client.delete(key)
