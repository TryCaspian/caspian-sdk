"""GatewayTransport — dispatches transport="gateway" request-descriptions.

Satisfies TransportPort like HttpTransport, so the ProcessInterpreter
is unchanged: in hosted mode, execute() emits a gateway request-description and
this transport sends it through the GatewayClient. Failure is returned as a
CaspianError value, never raised.
"""

from __future__ import annotations

from caspian.core.ports import Result, Sent
from caspian.hosted.client import GatewayClient, GatewayRequest


class GatewayTransport:
    """Routes a Sent(transport="gateway") through a GatewayClient."""

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def dispatch(self, sent: Sent) -> Result:
        raw = sent.raw
        if raw.get("transport") != "gateway":
            # Not ours (e.g. a noop from an adapter); pass through as success.
            return Result.ok(sent)

        spec = raw.get("gateway", {})
        request = GatewayRequest(
            method=spec.get("method", "POST"),
            path=spec.get("path", ""),
            json_body=spec.get("json_body"),
            params=spec.get("params", {}),
            text_response=spec.get("text_response", False),
        )
        result = self._client.send(request)
        if not result.is_ok:
            return result

        response = result.value
        message_id = ""
        if isinstance(response.json_body, dict):
            for key in ("message_id", "id", "ts"):
                if key in response.json_body:
                    message_id = str(response.json_body[key])
                    break
        return Result.ok(
            Sent(
                message_id=message_id,
                raw={"status": response.status_code, "native": raw.get("native", "")},
            )
        )
