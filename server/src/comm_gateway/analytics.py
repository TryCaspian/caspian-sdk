import logging

log = logging.getLogger("comm.analytics")

_client = None

# Top-level scalar keys from an event's `data` forwarded as properties.
# Message events nest channel/direction under `data["message"]`.
_SAFE_KEYS = (
    "source", "reason", "scope", "channel", "provider",
    "amount_cents", "cap_cents", "balance_cents", "spent_this_month_cents",
    "email", "from_project_id", "connections", "domains",
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


def identify(distinct_id: str, properties: dict | None = None) -> None:
    """Attach person properties (e.g. email) so web + gateway events can join."""
    if _client is None or not distinct_id:
        return
    try:
        props = {"$set": properties or {}}
        _client.capture(distinct_id=distinct_id, event="$identify", properties=props)
    except Exception:
        log.warning("posthog identify failed for %s", distinct_id, exc_info=True)


def alias(previous_id: str, distinct_id: str) -> None:
    """Merge a prior distinct_id (usually project_id) into the person distinct_id."""
    if _client is None or not previous_id or not distinct_id or previous_id == distinct_id:
        return
    try:
        # posthog-python: alias(previous_id=..., distinct_id=...)
        _client.alias(previous_id=previous_id, distinct_id=distinct_id)
    except Exception:
        log.warning("posthog alias failed %s -> %s", previous_id, distinct_id, exc_info=True)


def link_account(project_id: str, email: str, *, source: str) -> None:
    """Identify the developer and stitch project-scoped events onto their person."""
    if not email:
        return
    identify(email, {"email": email, "project_id": project_id})
    alias(project_id, email)
    capture(email, "gateway.account_linked", {
        "email": email, "project_id": project_id, "source": source,
    })


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
