"""Streaming replies."""

import threading
from typing import TYPE_CHECKING

from .client import CommError

if TYPE_CHECKING:
    from .client import CommClient


class UnsupportedOperation(Exception):
    pass


class MessageStream:
    def __init__(self, client: "CommClient", message_id: str) -> None:
        self._client = client
        self._message_id = message_id
        self._text = ""
        self._timer = None
        self._mode = None          # "edit" or "final", set once on first append
        self._stream_id = None
        self._opened = False       # cleaner than checking self._text == chunk

    def append(self, chunk: str) -> None:
        self._text += chunk

        if not self._opened:
            self._opened = True
            try:
                res = self._client._request(
                    "POST", f"/v1/messages/{self._message_id}/stream", json={}
                )
                self._mode = res.get("mode", "final")
                if self._mode == "edit":
                    self._stream_id = res.get("stream_id")
                    self._flush()        # first chunk goes out immediately
            except CommError as e:
                # If endpoint does not exist on the gateway, degrade gracefully to fallback
                if e.status_code in (404, 405):
                    self._mode = "final"
                else:
                    raise

        elif self._mode == "edit":
            # Restart the debounce timer on every subsequent chunk
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(0.3, self._flush)
            self._timer.start()

    def _flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._mode == "edit" and self._stream_id:
            try:
                self._client._request(
                    "PATCH",
                    f"/v1/streams/{self._stream_id}",
                    json={"text": self._text},
                )
            except Exception:
                pass

    def finalize(self) -> dict | None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if self._mode == "edit" and self._stream_id:
            try:
                return self._client._request(
                    "POST",
                    f"/v1/streams/{self._stream_id}/finalize",
                    json={"text": self._text},
                )
            except Exception:
                pass
        elif self._mode == "final" or (self._mode == "edit" and not self._stream_id):
            if self._text:
                return self._client.reply(self._message_id, text=self._text)
        # Never opened (no chunks appended) — nothing to send
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._text += " [stream interrupted]"
        self.finalize()
