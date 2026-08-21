"""HTTP transport — the one place real network I/O happens for outbound commands.

Adapters build request descriptions (pure data). The transport dispatches them.
This isolates all I/O to a single, swappable, testable component (sdk-reliability:
one effect boundary; a fake/chaos transport is trivial to substitute).
"""

from __future__ import annotations

from typing import Any

import httpx

from caspian.core.errors import AdapterError
from caspian.core.ports import Result, Sent, TransportPort


class HttpTransport:
    """Dispatches http_json / http_form / http_multipart request descriptions via httpx."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def dispatch(self, sent: Sent) -> Result:
        """Send the request described in sent.raw. Returns Result[Sent, AdapterError]."""
        req = sent.raw
        transport = req.get("transport", "")

        if transport == "noop":
            return Result.ok(Sent(message_id="", raw={"native": req.get("native", "")}))

        if transport not in ("http_json", "http_form", "http_multipart"):
            return Result.err(
                AdapterError(reason=f"Unsupported transport: {transport!r}")
            )

        method = req.get("method", "POST")
        url = req.get("url", "")
        headers = req.get("headers", {})

        try:
            with httpx.Client(timeout=self._timeout) as client:
                kwargs: dict[str, Any] = {"headers": headers}
                if transport == "http_json":
                    kwargs["json"] = req.get("json", {})
                elif transport == "http_form":
                    kwargs["data"] = req.get("form", {})
                else:  # http_multipart
                    kwargs["data"] = req.get("form", {})
                    kwargs["files"] = req.get("files", {})

                resp = client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            return Result.err(AdapterError(reason=f"HTTP error: {e}"))

        if resp.status_code >= 400:
            return Result.err(
                AdapterError(
                    reason=f"{resp.status_code}: {resp.text[:200]}",
                    command_tag=req.get("native", ""),
                )
            )

        message_id = self._extract_message_id(resp)
        return Result.ok(Sent(message_id=message_id, raw={"status": resp.status_code}))

    def _extract_message_id(self, resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except (ValueError, httpx.HTTPError):
            return ""
        # Telegram: {"ok": true, "result": {"message_id": ...}}
        if isinstance(data, dict):
            result = data.get("result", data)
            if isinstance(result, dict):
                for key in ("message_id", "ts", "id"):
                    if key in result:
                        return str(result[key])
        return ""


class RecordingTransport:
    """Test transport: records dispatched requests, returns canned success."""

    def __init__(self) -> None:
        self.dispatched: list[Sent] = []

    def dispatch(self, sent: Sent) -> Result:
        self.dispatched.append(sent)
        return Result.ok(Sent(message_id="rec_1", raw=sent.raw))


class ChaosTransport:
    """Failure interpreter: every dispatch is an AdapterError (sdk-reliability)."""

    def __init__(self, reason: str = "chaos") -> None:
        self.reason = reason

    def dispatch(self, sent: Sent) -> Result:
        return Result.err(
            AdapterError(reason=self.reason, command_tag=str(sent.raw.get("native", "")))
        )


class MultiplexTransport:
    """Route a Sent to the transport named in sent.raw['transport']."""

    def __init__(
        self,
        routes: dict[str, TransportPort],
        default: TransportPort | None = None,
    ) -> None:
        self._routes = routes
        self._default = default

    def dispatch(self, sent: Sent) -> Result:
        name = str(sent.raw.get("transport", ""))
        impl = self._routes.get(name, self._default)
        if impl is None:
            return Result.err(AdapterError(reason=f"No transport for {name!r}"))
        dispatched: Result = impl.dispatch(sent)
        return dispatched
