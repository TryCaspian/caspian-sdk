import logging

log = logging.getLogger("comm.analytics")

_client = None

# Top-level scalar keys from an event's `data` forwarded as properties.
# Message events nest channel/direction under `data["message"]`.
_SAFE_KEYS = (
    "source", "reason", "scope", "channel", "provider",
    "amount_cents", "cap_cents", "balance_cents", "spent_this_month_cents",
)


def configure_analytics(key: str, host: str) -> None:
    """Initialize the PostHog client if a key is set. No-op otherwise."""
    global _client
    if not key:
        return
    try:
        from posthog import Posthog

        _client = Posthog(project_api_key=key, host=host)
        log.info("posthog analytics enabled")
    except Exception:
        log.exception("posthog init failed; analytics disabled")


def capture(distinct_id: str | None, event: str, properties: dict | None = None) -> None:
    """Send one event. Best-effort — never raises."""
    if _client is None:
        return
    try:
        _client.capture(
            distinct_id=distinct_id or "anonymous",
            event=event,
            properties=properties or {},
        )
    except Exception:
        log.warning("posthog capture failed for %s", event, exc_info=True)


def safe_props(event_type: str, data: dict) -> dict:
    """Extract the forwarded properties from an event payload."""
    props: dict = {"event_type": event_type}
    for k in _SAFE_KEYS:
        v = data.get(k)
        if isinstance(v, (str, int, float, bool)):
            props[k] = v
    msg = data.get("message")
    if isinstance(msg, dict):
        for k in ("channel", "direction"):
            v = msg.get(k)
            if isinstance(v, (str, int)):
                props[k] = v
    return props
