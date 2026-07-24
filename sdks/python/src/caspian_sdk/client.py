"""Python SDK for the communication gateway.

Usage:

    client = CommClient(api_key="...", base_url="https://gateway.example.com")
    customer = client.create_customer("Acme")
    agent = client.create_agent("Support Agent")
    connection = client.connect_email(customer["id"], agent["id"])
    print(connection["address"])

    @client.on_message
    def handle(message):
        message.reply(f"You said: {message.text}")

    client.listen()
"""

import hashlib
import hmac
import json
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Timer
from typing import Literal

import httpx

from .state import InMemoryStateAdapter, StateAdapter

logger = logging.getLogger("caspian_sdk")

ConcurrencyStrategy = Literal["queue", "debounce", "drop", "parallel"]


def _dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _config(explicit: str | None, env_key: str, default: str | None = None) -> str | None:
    """Resolve a value from an explicit arg, env, or ./.env. Prefers the branded
    CASPIAN_* name, falling back to the legacy COMM_* one for back-compat."""
    dotenv = _dotenv()
    keys = [env_key]
    if env_key.startswith("CASPIAN_"):
        keys.append("COMM_" + env_key[len("CASPIAN_") :])  # legacy alias
    for source in (lambda k: explicit if k == env_key else None, os.environ.get, dotenv.get):
        for key in keys:
            value = source(key)
            if value:
                return value
    return default


class CommError(Exception):
    """CommError implementation."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Execute __init__."""
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class WebhookVerificationError(CommError):
    """Raised when an inbound webhook fails signature verification."""

    def __init__(self, detail: str = "Webhook signature mismatch") -> None:
        """Execute __init__."""
        super().__init__(401, detail)


class AccountRequiredError(CommError):
    """Raised when a paid channel needs a one-time developer sign-in first (HTTP
    401). Paid channels are tied to a real Caspian account (identity) before any
    spend; free channels never raise this. Call ``.login()`` to run the sign-in,
    or read ``login_options`` for the raw device-flow endpoints."""

    def __init__(self, status_code: int, payload: dict, client: "CommClient") -> None:
        """Execute __init__."""
        self.reason = payload.get("reason", "account_required")
        self.message = payload.get("message", "Sign in to Caspian to use paid channels.")
        self.login_options = payload.get("login_options", [])
        self._client = client
        super().__init__(status_code, self.message)

    def login(self, **kwargs) -> dict:
        """Run the one-time developer sign-in (prints a URL, waits for approval)."""
        return self._client.login(**kwargs)


class InsufficientCreditError(CommError):
    """Raised when a paid channel is blocked because the project is out of credit
    (HTTP 402) or has hit a spend cap (HTTP 429).

    Carries the machine-actionable fields the gateway returns so you can react in
    code: ``balance_cents`` and ``payment_options`` (each option describes the
    request that mints a Stripe checkout URL). ``top_up(amount_cents)`` is a
    shortcut that mints that link for you.
    """

    def __init__(self, status_code: int, payload: dict, client: "CommClient") -> None:
        """Execute __init__."""
        self.reason = payload.get("reason", "insufficient_credit")
        self.message = payload.get("message", "Out of Caspian credit.")
        self.balance_cents = payload.get("balance_cents")
        self.payment_options = payload.get("payment_options", [])
        self._client = client
        super().__init__(status_code, self.message)

    def top_up(self, amount_cents: int | None = None) -> dict:
        """Mint a Stripe-hosted checkout link to refill credit. Defaults to the
        amount the gateway suggested in the 402. Returns ``{"checkout_url", ...}``;
        open it (or hand it to whoever holds the card)."""
        if amount_cents is None:
            for option in self.payment_options:
                body = (option.get("create") or {}).get("body") or {}
                if body.get("amount_cents"):
                    amount_cents = body["amount_cents"]
                    break
        return self._client.top_up(amount_cents or 2000)


@dataclass
class Message:
    """An inbound message delivered to an on_message handler."""

    id: str
    conversation_id: str
    connection_id: str
    customer_id: str
    agent_id: str
    channel: str
    sender: dict | None
    subject: str | None
    text: str | None
    html: str | None
    _client: "CommClient" = field(repr=False)
    # File attachments received with the message: each {"url"|"data", "mime_type",
    # "name", "size"}. Empty on channels/messages with no attachments.
    media: list[dict] = field(default_factory=list)

    def reply(
        self,
        text: str | None = None,
        html: str | None = None,
        blocks: list[dict] | None = None,
        media: list[dict] | None = None,
    ) -> dict:
        """Execute reply."""
        return self._client.reply(self.id, text=text, html=html, blocks=blocks, media=media)

    def react(self, emoji: str) -> dict:
        """Add an emoji reaction (tapback) to this message. Best-effort; no-op on
        channels without a reaction API (needs Capability.REACTIONS)."""
        return self._client.react(self.id, emoji)

    def typing(self) -> None:
        """Show a 'thinking…' typing indicator on the channel (Discord/Telegram;
        no-op where the platform has none). Fired automatically before your
        handler runs; call again during long work to keep it alive."""
        self._client.typing(self.id)

    def stream(self, edit_interval: float = 0.5) -> "StreamSession":
        """Start a streaming response. Returns a context manager that accepts
        token chunks and progressively updates the reply on channels that
        support message editing (Slack/Discord/Telegram). On channels that
        don't support editing (email/SMS/X), the final concatenated text is
        sent as a single reply when the context manager exits.

        Usage::

            @client.on_message
            def handle(msg):
                with msg.stream() as s:
                    for chunk in llm(msg.text):
                        s.append(chunk)
        """
        strategy = self._client._stream_strategy(self.connection_id)
        return StreamSession(
            message_id=self.id,
            client=self._client,
            strategy=strategy,
            edit_interval=edit_interval,
        )


@dataclass
class Interaction:
    """A button tap delivered to an on_interaction handler. `value` is the callback
    value set on the block button; `source_message` is the message it was on."""

    connection_id: str
    customer_id: str
    agent_id: str
    conversation_id: str | None
    value: str | None
    source_message: dict | None
    sender: dict | None
    _client: "CommClient" = field(repr=False)

    def reply(
        self,
        text: str | None = None,
        html: str | None = None,
        blocks: list[dict] | None = None,
        media: list[dict] | None = None,
    ) -> dict:
        """Reply in the thread the button lived in (replies to the source message)."""
        if not self.source_message:
            raise CommError(400, "interaction has no source message to reply to")
        return self._client.reply(
            self.source_message["id"], text=text, html=html, blocks=blocks, media=media
        )


@dataclass
class Reaction:
    """An emoji reaction delivered to an on_reaction handler. `action` is "added"
    or "removed"; `source_message` is the message that was reacted to."""

    connection_id: str
    customer_id: str
    agent_id: str
    emoji: str | None
    action: str
    source_message: dict | None
    sender: dict | None
    _client: "CommClient" = field(repr=False)


StreamStrategy = Literal["post_edit", "final_only"]


class StreamSession:
    """Context manager for streaming a response token-by-token.

    On channels that support editing own messages (``Capability.EDIT_OUTBOUND`` —
    Slack, Discord, Telegram), the session posts an initial reply and
    progressively edits it as chunks arrive (throttled to ``edit_interval``
    seconds to respect platform rate limits).

    On channels that cannot edit sent messages (email, SMS, X), the session
    silently accumulates all chunks and sends them as a single reply when the
    context manager exits.

    The finalizer always runs, even on exception, so partial content is never
    lost.
    """

    def __init__(
        self,
        message_id: str,
        client: "CommClient",
        strategy: StreamStrategy = "final_only",
        edit_interval: float = 0.5,
    ) -> None:
        """Execute __init__."""
        self._message_id = message_id
        self._client = client
        self._strategy: StreamStrategy = strategy
        self._edit_interval = edit_interval
        self._chunks: list[str] = []
        self._posted_id: str | None = None
        self._last_edit: float = 0.0
        self._finalized = False

    @property
    def text(self) -> str:
        """The full accumulated text so far."""
        return "".join(self._chunks)

    def append(self, chunk: str) -> None:
        """Append a token/chunk to the stream.

        On ``post_edit`` channels this may trigger an intermediate edit (rate-
        limited by ``edit_interval``). On ``final_only`` channels this is a
        no-op beyond accumulation.
        """
        if self._finalized:
            raise CommError(400, "stream already finalized")
        self._chunks.append(chunk)

        if self._strategy != "post_edit":
            return

        now = time.monotonic()
        if self._posted_id is None:
            # First chunk: post the initial reply
            try:
                result = self._client.reply(self._message_id, text=self.text)
                self._posted_id = result.get("id") or result.get("message_id")
            except Exception:
                logger.warning("stream initial post failed; falling back to final_only")
                
            if not self._posted_id:
                # The provider doesn't support predictable IDs or failed; fallback to final_only
                self._strategy = "final_only"
            self._last_edit = now
        elif now - self._last_edit >= self._edit_interval:
            # Throttled edit
            if self._posted_id:
                try:
                    self._client.edit_message(self._posted_id, text=self.text)
                except Exception:
                    logger.warning("stream edit failed; will retry on next chunk")
                self._last_edit = now

    def finalize(self) -> dict | None:
        """Send the final version. Called automatically by ``__exit__``."""
        if self._finalized:
            return None
        self._finalized = True
        full_text = self.text
        if not full_text:
            return None

        if self._strategy == "post_edit" and self._posted_id:
            # Final edit with the complete text
            try:
                return self._client.edit_message(self._posted_id, text=full_text)
            except Exception:
                logger.warning("final stream edit failed; sending as new reply")
                return self._client.reply(self._message_id, text=full_text)
        else:
            # final_only or post_edit where initial post somehow failed
            return self._client.reply(self._message_id, text=full_text)

    def __enter__(self) -> "StreamSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.finalize()
        return None  # don't suppress exceptions


class _MessageScheduler:
    """Process message events according to a per-conversation overlap policy."""

    def __init__(
        self,
        dispatch: Callable[[dict], None],
        strategy: ConcurrencyStrategy,
        debounce_ms: int,
    ) -> None:
        """Execute __init__."""
        if strategy not in {"queue", "debounce", "drop", "parallel"}:
            raise ValueError("concurrency must be one of: queue, debounce, drop, parallel")
        if debounce_ms < 0:
            raise ValueError("debounce_ms must be non-negative")
        self._dispatch = dispatch
        self._strategy = strategy
        self._debounce_seconds = debounce_ms / 1000
        self._executor = ThreadPoolExecutor(thread_name_prefix="caspian-listener")
        self._lock = Lock()
        self._queues: dict[str, deque[dict]] = {}
        self._running: set[str] = set()
        self._timers: dict[str, Timer] = {}
        self._pending: dict[str, dict] = {}
        self._closed = False

    @staticmethod
    def _conversation_key(event: dict) -> str:
        data = event.get("data") or {}
        message = data.get("message") or {}
        return str(
            message.get("conversation_id")
            or data.get("conversation_id")
            or message.get("id")
            or event.get("seq")
            or "unknown"
        )

    def submit(self, event: dict) -> None:
        """Execute submit."""
        if event.get("type") != "message.received":
            self._dispatch(event)
            return
        key = self._conversation_key(event)
        if self._strategy == "queue":
            self._submit_queue(key, event)
        elif self._strategy == "debounce":
            self._submit_debounce(key, event)
        elif self._strategy == "drop":
            self._submit_drop(key, event)
        else:
            self._submit_parallel(event)

    def _submit_parallel(self, event: dict) -> None:
        with self._lock:
            if not self._closed:
                self._executor.submit(self._safe_dispatch, event)

    def _submit_queue(self, key: str, event: dict) -> None:
        with self._lock:
            if self._closed:
                return
            self._queues.setdefault(key, deque()).append(event)
            if key not in self._running:
                self._running.add(key)
                self._executor.submit(self._drain_queue, key)

    def _drain_queue(self, key: str) -> None:
        while True:
            with self._lock:
                queue = self._queues.get(key)
                if not queue:
                    self._queues.pop(key, None)
                    self._running.discard(key)
                    return
                event = queue.popleft()
            self._safe_dispatch(event)

    def _submit_debounce(self, key: str, event: dict) -> None:
        with self._lock:
            if self._closed:
                return
            if timer := self._timers.get(key):
                timer.cancel()
            self._pending[key] = event
            self._timers.pop(key, None)
            if key not in self._running:
                self._start_debounce_timer(key)

    def _start_debounce_timer(self, key: str) -> None:
        event = self._pending[key]
        timer = Timer(self._debounce_seconds, self._fire_debounce, args=(key, event))
        timer.daemon = True
        self._timers[key] = timer
        timer.start()

    def _fire_debounce(self, key: str, event: dict) -> None:
        with self._lock:
            if self._closed or self._pending.get(key) is not event:
                return
            self._pending.pop(key)
            self._timers.pop(key, None)
            self._running.add(key)
            self._executor.submit(self._run_debounce, key, event)

    def _run_debounce(self, key: str, event: dict) -> None:
        while True:
            self._safe_dispatch(event)
            with self._lock:
                self._running.discard(key)
                next_event = None
                if self._closed:
                    next_event = self._pending.pop(key, None)
                    if timer := self._timers.pop(key, None):
                        timer.cancel()
                    if next_event is not None:
                        self._running.add(key)
                elif key in self._pending:
                    self._start_debounce_timer(key)
                if next_event is None:
                    return
                event = next_event

    def _submit_drop(self, key: str, event: dict) -> None:
        with self._lock:
            if self._closed or key in self._running:
                return
            self._running.add(key)
            self._executor.submit(self._run_drop, key, event)

    def _run_drop(self, key: str, event: dict) -> None:
        try:
            self._safe_dispatch(event)
        finally:
            with self._lock:
                self._running.discard(key)

    def _safe_dispatch(self, event: dict) -> None:
        try:
            self._dispatch(event)
        except Exception:
            logger.exception("event dispatch failed; continuing")

    def close(self) -> None:
        """Execute close."""
        with self._lock:
            if self._closed:
                return
            # ponytail: overlap state stays in this process. A shared state adapter
            # is the upgrade path if listen() later coordinates multiple workers.
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            for key, event in list(self._pending.items()):
                if key in self._running:
                    continue
                self._pending.pop(key)
                self._running.add(key)
                self._executor.submit(self._run_debounce, key, event)
            self._closed = True
        self._executor.shutdown(wait=True)


class CommClient:
    """CommClient implementation."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        http: httpx.Client | None = None,
        timeout: float = 30.0,
        state: StateAdapter | None = None,
    ) -> None:
        """Execute __init__."""
        api_key = _config(api_key, "CASPIAN_API_KEY")
        if not api_key:
            raise CommError(401, "No API key: pass api_key or set CASPIAN_API_KEY (env or ./.env)")
        base_url = _config(base_url, "CASPIAN_BASE_URL", "https://api.trycaspianai.com")
        self._api_key = api_key
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._handlers: list[Callable[[Message], None]] = []
        self._interaction_handlers: list[Callable[[Interaction], None]] = []
        self._reaction_handlers: list[Callable[[Reaction], None]] = []
        self._ack: str | None = None
        self.handlers: dict[str, list[Callable]] = {}
        self.state = state or InMemoryStateAdapter()
        self._strategy_cache: dict[str, str] = {}

    def close(self) -> None:
        """Execute close."""
        self._http.close()

    def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ):
        response = self._http.request(
            method,
            path,
            json=json,
            params=params,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
                detail = payload.get("detail", response.text) if isinstance(payload, dict) else payload
            except ValueError:
                detail = response.text
            # A paid channel needs a one-time developer sign-in first.
            if (
                response.status_code == 401
                and isinstance(detail, dict)
                and detail.get("reason") == "account_required"
            ):
                raise AccountRequiredError(response.status_code, detail, self)
            # A billing block (out of credit / spend cap) carries a structured
            # body; raise the typed error so callers can react in code.
            if (
                response.status_code in (402, 429)
                and isinstance(detail, dict)
                and detail.get("reason")
                in {"insufficient_credit", "monthly_cap_reached", "channel_cap_reached"}
            ):
                raise InsufficientCreditError(response.status_code, detail, self)
            raise CommError(response.status_code, str(detail))
        if response.status_code == 204:
            return None
        return response.json()

    def _get_text(self, path: str) -> str:
        response = self._http.get(path, headers={"Authorization": f"Bearer {self._api_key}"})
        if response.status_code >= 400:
            raise CommError(response.status_code, response.text)
        return response.text

    # Platform behaviour guides (opt-in)

    def behavior_prompt(self) -> str:
        """A ready-to-inject system-prompt block telling your agent how to behave on
        each channel you've connected (Slack threads, WhatsApp 24h window, SMS
        length, formatting, etc.). Append it to your agent's system prompt — or
        ignore it and write your own. Empty string if nothing is connected yet."""
        return self._get_text("/v1/behavior-prompt")

    def channel_guide(self, channel: str) -> str:
        """The behaviour guide for a single channel (e.g. "slack", "discord")."""
        return self._get_text(f"/v1/channels/{channel}/guide")

    # Resources

    def create_customer(self, name: str) -> dict:
        """Execute create_customer."""
        return self._request("POST", "/v1/customers", json={"name": name})

    def create_agent(self, name: str) -> dict:
        """Execute create_agent."""
        return self._request("POST", "/v1/agents", json={"name": name})

    def _connect(
        self,
        channel: str,
        customer_id: str | None = None,
        agent_id: str | None = None,
        display_name: str | None = None,
        capabilities: list[str] | None = None,
        wait: bool = True,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
        **channel_fields,
    ) -> dict:
        connection = self._request(
            "POST",
            f"/v1/connections/{channel}",
            json={
                "customer_id": customer_id,
                "agent_id": agent_id,
                "display_name": display_name,
                "capabilities": capabilities,
                **channel_fields,
            },
        )
        if not wait:
            return connection
        deadline = time.monotonic() + timeout
        while connection["status"] == "provisioning":
            if time.monotonic() >= deadline:
                raise CommError(408, f"connection {connection['id']} still provisioning")
            time.sleep(poll_interval)
            connection = self.get_connection(connection["id"])
        if connection["status"] == "failed":
            raise CommError(502, f"provisioning failed: {connection.get('error')}")
        return connection

    def connect_email(
        self,
        customer_id: str | None = None,
        agent_id: str | None = None,
        domain: str | None = None,
        username: str | None = None,
        **kwargs,
    ) -> dict:
        """Connect an email inbox.

        Pass username= to pick a readable mailbox name (e.g. "scout" ->
        scout@agents.trycaspianai.com); it works on the default platform domain
        or a verified custom domain (pass domain= too). If the name is taken the
        API returns 409 with a ``suggestions`` list of free alternatives.
        """
        return self._connect(
            "email", customer_id, agent_id, domain=domain, username=username, **kwargs
        )

    def connect_telegram(
        self,
        bot_token: str,
        customer_id: str | None = None,
        agent_id: str | None = None,
        **kwargs,
    ) -> dict:
        """Connect a Telegram bot. Get a token from @BotFather; we do the rest."""
        return self._connect("telegram", customer_id, agent_id, bot_token=bot_token, **kwargs)

    def add_domain(self, domain: str) -> dict:
        """Register a custom subdomain (e.g. agents.example.com). Returns the
        DNS records to add at the registrar; poll get_domain() until active."""
        return self._request("POST", "/v1/domains", json={"domain": domain})

    def list_domains(self) -> list[dict]:
        """Execute list_domains."""
        return self._request("GET", "/v1/domains")

    def get_domain(self, domain_id: str) -> dict:
        """Execute get_domain."""
        return self._request("GET", f"/v1/domains/{domain_id}")

    def connect_phone(
        self,
        customer_id: str | None = None,
        agent_id: str | None = None,
        provider=None,
        **kwargs,
    ) -> dict:
        """Connect an SMS/voice phone line. `provider` picks the backend when more
        than one is configured (e.g. gsm-modem, or a hosted provider); omit for
        the deployment default."""
        return self._connect("phone", customer_id, agent_id, provider=provider, **kwargs)

    def connect_whatsapp(self, customer_id=None, agent_id=None, provider=None, **kwargs) -> dict:
        """Connect a WhatsApp number. When more than one WhatsApp backend is
        configured, `provider` picks one explicitly. Omit to use the
        deployment's default WhatsApp provider."""
        return self._connect("whatsapp", customer_id, agent_id, provider=provider, **kwargs)

    def start_whatsapp_onboarding(
        self,
        customer_id=None,
        agent_id=None,
        display_name=None,
        capabilities=None,
    ) -> dict:
        """Begin WhatsApp onboarding for one of your customers (Caspian hosted).

        Returns ``{"session", "launcher_url", "expires_in"}``. Hand ``launcher_url``
        to whoever owns the WhatsApp Business account (open it, or embed it in your
        own UI): they click through a popup once and their number is provisioned
        onto this agent - no tokens to copy on your side. The API key never reaches
        the browser (the session token stands in for it).

        Omit customer_id/agent_id to onboard onto this project's default scope, or
        pass both to target a specific customer+agent. Poll get_connection()
        (or watch for a connection.active event) until it's active.
        """
        body: dict = {}
        if customer_id is not None:
            body["customer_id"] = customer_id
        if agent_id is not None:
            body["agent_id"] = agent_id
        if display_name is not None:
            body["display_name"] = display_name
        if capabilities is not None:
            body["capabilities"] = capabilities
        return self._request("POST", "/v1/connections/whatsapp/onboarding-session", json=body)

    def connect_imessage(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Connect an iMessage line (Caspian hosted)."""
        return self._connect("imessage", customer_id, agent_id, **kwargs)

    def connect_rcs(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Connect an RCS Business Messaging sender (Caspian hosted)."""
        return self._connect("rcs", customer_id, agent_id, **kwargs)

    def connect_discord(
        self,
        bot_token: str | None = None,
        webhook_url: str | None = None,
        username: str | None = None,
        avatar_url: str | None = None,
        customer_id=None,
        agent_id=None,
        **kwargs,
    ) -> dict:
        """Connect a Discord identity. Either a bot (`bot_token` from
        discord.com/developers) OR a channel `webhook_url` for a per-agent
        identity with a custom `username`/`avatar_url` (no bot needed)."""
        return self._connect(
            "discord",
            customer_id,
            agent_id,
            bot_token=bot_token,
            webhook_url=webhook_url,
            username=username,
            avatar_url=avatar_url,
            **kwargs,
        )

    def install_discord(self, customer_id=None, agent_id=None, display_name=None, **kwargs) -> dict:
        """One-click install of the gateway's shared Discord bot (no bot token).

        Returns a connection with an ``authorize_url``. Open it (or hand it to the
        developer), pick a Discord server, and the shared bot joins it; messages in
        that server route to this agent. Zero setup - no bot to create.

        Pass ``display_name`` to give the bot YOUR custom name in that server (e.g.
        "Acme Support") - it appears under that name instead of the shared bot's
        name. Use connect_discord(bot_token=...) instead if you want a fully
        separate bot (your own name AND avatar, member-list included)."""
        body = {
            "customer_id": customer_id,
            "agent_id": agent_id,
            "display_name": display_name,
            **kwargs,
        }
        return self._request("POST", "/v1/connections/discord/install", json=body)

    def connect_slack(
        self,
        slack_client_id: str | None = None,
        slack_client_secret: str | None = None,
        slack_signing_secret: str | None = None,
        customer_id=None,
        agent_id=None,
        **kwargs,
    ) -> dict:
        """Start a Slack install. Bring your own Slack app (create one at
        api.slack.com/apps and pass its client id/secret/signing secret) so the
        bot carries your brand. Returns a connection with an `authorize_url`; the
        workspace owner clicks it to approve, then the connection goes active."""
        return self._connect(
            "slack",
            customer_id,
            agent_id,
            wait=False,
            slack_client_id=slack_client_id,
            slack_client_secret=slack_client_secret,
            slack_signing_secret=slack_signing_secret,
            **kwargs,
        )

    def install_slack(
        self, customer_id=None, agent_id=None, display_name=None, icon_url=None, **kwargs
    ) -> dict:
        """One-click install of the gateway's shared Slack app (no app to create).

        Returns a connection with an ``authorize_url`` ("Add to Slack"). Open it
        (or hand it to the developer), pick a workspace, and the shared app
        installs there; messages in that workspace route to this agent. Zero setup
        - no Slack app to build. Pass ``display_name`` and ``icon_url`` to post
        under YOUR own name + icon (the plumbing stays invisible). Use
        connect_slack(slack_client_id=...) instead to bring your own Slack app."""
        body = {
            "customer_id": customer_id,
            "agent_id": agent_id,
            "display_name": display_name,
            "icon_url": icon_url,
            **kwargs,
        }
        return self._request("POST", "/v1/connections/slack/install", json=body)

    def connect_github(
        self,
        github_app_id: str,
        github_app_slug: str,
        github_private_key: str,
        github_webhook_secret: str,
        customer_id=None,
        agent_id=None,
        receive_mode: str = "mentions",
        **kwargs,
    ) -> dict:
        """Start installation of a bring-your-own GitHub App.

        The App must use the gateway's GitHub setup and webhook URLs, subscribe
        to ``issue_comment``, and have Issues read/write permission. Returns a
        connection with an ``authorize_url`` to install on selected repositories.
        """
        return self._connect(
            "github",
            customer_id,
            agent_id,
            wait=False,
            github_app_id=github_app_id,
            github_app_slug=github_app_slug,
            github_private_key=github_private_key,
            github_webhook_secret=github_webhook_secret,
            receive_mode=receive_mode,
            **kwargs,
        )

    def install_github(
        self,
        customer_id=None,
        agent_id=None,
        display_name=None,
        receive_mode: str = "mentions",
        **kwargs,
    ) -> dict:
        """One-click installation of the gateway's shared GitHub App."""
        body = {
            "customer_id": customer_id,
            "agent_id": agent_id,
            "display_name": display_name,
            "receive_mode": receive_mode,
            **kwargs,
        }
        return self._request("POST", "/v1/connections/github/install", json=body)

    def update_branding(self, connection_id: str, display_name=None, icon_url=None) -> dict:
        """Change the name/icon the agent posts under, after connecting - no
        re-install. Slack: takes effect on the next message; Discord shared bot:
        re-sets the per-server nickname. Pass either or both."""
        return self._request(
            "PATCH",
            f"/v1/connections/{connection_id}",
            json={"display_name": display_name, "icon_url": icon_url},
        )

    def connect_x(
        self,
        access_token: str,
        user_id: str,
        access_secret: str | None = None,
        username: str | None = None,
        customer_id=None,
        agent_id=None,
        **kwargs,
    ) -> dict:
        """Connect an X (Twitter) account as a reactive DM bot.

        Bring the account's OAuth tokens: `access_token` + `user_id` (the numeric
        id, embedded before the dash in an OAuth 1.0a access token), and
        `access_secret` for a bring-your-own account. People DM the account and
        the agent replies; the gateway polls for inbound DMs (no webhook to set
        up). Reactive only - it never cold-DMs. The account must be labelled
        "Automated" in X settings."""
        return self._connect(
            "x",
            customer_id,
            agent_id,
            access_token=access_token,
            user_id=user_id,
            access_secret=access_secret,
            username=username,
            **kwargs,
        )

    def install_x(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """One-click connect of an X account as a DM bot - no tokens to paste.

        Returns a connection with an ``authorize_url`` ("Sign in with X"). Open it
        (or hand it to the developer), authorize on X, and that account becomes the
        bot: people DM it, the agent replies. Uses the gateway's shared X app
        (OAuth 1.0a 3-legged), so there's no X app to create. Use
        connect_x(access_token=...) instead to bring your own account tokens."""
        body = {"customer_id": customer_id, "agent_id": agent_id, **kwargs}
        return self._request("POST", "/v1/connections/x/install", json=body)

    def connect_instagram(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Start an Instagram DM install (OAuth). Returns an `authorize_url`."""
        return self._connect("instagram", customer_id, agent_id, wait=False, **kwargs)

    def connect_facebook(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Start a Facebook Messenger install (OAuth). Returns an `authorize_url`."""
        return self._connect("facebook", customer_id, agent_id, wait=False, **kwargs)

    def get_connection(self, connection_id: str) -> dict:
        """Execute get_connection."""
        return self._request("GET", f"/v1/connections/{connection_id}")

    def list_conversations(self, connection_id: str | None = None) -> list[dict]:
        """Execute list_conversations."""
        params = {"connection_id": connection_id} if connection_id else None
        return self._request("GET", "/v1/conversations", params=params)

    def list_messages(self, conversation_id: str) -> list[dict]:
        """Execute list_messages."""
        return self._request("GET", f"/v1/conversations/{conversation_id}/messages")

    def reply(
        self,
        message_id: str,
        text: str | None = None,
        html: str | None = None,
        blocks: list[dict] | None = None,
        media: list[dict] | None = None,
    ) -> dict:
        """Reply on the channel the message arrived from.

        Pass ``blocks`` — a list of provider-neutral block dicts (heading, text,
        divider, image, fields, list, buttons, card) — to send a rich message.
        Channels that support rich layout (Slack, Discord, Telegram, email)
        render it natively; every other channel degrades to clean text
        automatically. See ``caspian_sdk.blocks`` for helper builders.

        Pass ``media`` — a list of ``{"url"|"data", "mime_type", "name"}`` dicts —
        to attach files (images/documents); channels that carry files send them
        natively and others fall back to the URL.
        """
        return self._request(
            "POST",
            f"/v1/messages/{message_id}/reply",
            json={"text": text, "html": html, "blocks": blocks, "media": media},
        )

    def react(self, message_id: str, emoji: str) -> dict:
        """Add an emoji reaction (tapback) to a message (needs Capability.REACTIONS
        — Slack/Telegram/Discord). Best-effort; a channel with no reaction API
        returns ``reacted=false`` rather than erroring."""
        return self._request("POST", f"/v1/messages/{message_id}/react", json={"emoji": emoji})

    def typing(self, message_id: str) -> dict:
        """Show a 'thinking…' indicator on the channel a message arrived on
        (Discord/Telegram; no-op where unsupported). Best-effort."""
        return self._request("POST", f"/v1/messages/{message_id}/typing")

    def edit_message(
        self,
        message_id: str,
        text: str | None = None,
        html: str | None = None,
        blocks: list[dict] | None = None,
    ) -> dict:
        """Edit an outbound message previously sent by this agent.

        Only works on channels with ``Capability.EDIT_OUTBOUND`` (Slack, Discord,
        Telegram). Used internally by ``StreamSession`` for post+edit streaming;
        also available for manual edits/corrections.
        """
        return self._request(
            "PATCH",
            f"/v1/messages/{message_id}",
            json={"text": text, "html": html, "blocks": blocks},
        )

    def invalidate_strategy_cache(self, connection_id: str) -> None:
        """Invalidate the cached streaming strategy for a connection."""
        self._strategy_cache.pop(connection_id, None)

    def _stream_strategy(self, connection_id: str) -> "StreamStrategy":
        """Determine the streaming strategy for a connection by checking its
        channel capabilities. Returns ``post_edit`` if the channel supports
        editing own messages, ``final_only`` otherwise."""
        if connection_id in self._strategy_cache:
            return self._strategy_cache[connection_id]
            
        strategy = "final_only"
        try:
            conn = self.get_connection(connection_id)
            capabilities = conn.get("capabilities") or []
            if "edit_outbound" in capabilities:
                strategy = "post_edit"
        except Exception:
            logger.warning("streaming strategy lookup failed; falling back to final_only")
            return strategy
            
        self._strategy_cache[connection_id] = strategy
        return strategy

    def set_webhook(self, url: str, secret: str | None = None) -> dict:
        """Receive events by push instead of (or alongside) polling."""
        return self._request("PUT", "/v1/webhook", json={"url": url, "secret": secret})

    def get_webhook(self) -> dict:
        """Execute get_webhook."""
        return self._request("GET", "/v1/webhook")

    def channels(self) -> list[dict]:
        """Configured transports and their capabilities."""
        return self._request("GET", "/v1/channels")

    # Account sign-in (one-time, required before paid channels)

    def login(self, poll_interval: float | None = None, timeout: float = 600.0) -> dict:
        """Sign the developer in once to open a billing account for this project.

        Paid channels (X, WhatsApp, iMessage) require a real account before any
        spend. This prints a URL for the developer to open in a browser and blocks
        until they approve with Google. The project you've already built with is
        carried over - same API key, nothing lost. After this, add credit with
        ``top_up()`` and connect paid channels freely; the agent needs no further
        human sign-in.
        """
        start = self._request("POST", "/v1/auth/device/start", json={"api_key": self._api_key})
        url = start.get("verification_uri_complete") or start.get("verification_uri")
        interval = poll_interval or start.get("interval", 5)
        print(
            "\n  Sign in to Caspian to enable paid channels (one-time):\n"
            f"    {url}\n"
            "  Waiting for the developer to approve in the browser...\n",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._request(
                "POST", "/v1/auth/device/token", json={"device_code": start["device_code"]}
            )
            status = result.get("status")
            if status == "approved":
                print(
                    "  Signed in. Add credit to start using paid channels.",
                    file=sys.stderr,
                    flush=True,
                )
                return result
            if status in ("expired", "not_found"):
                raise CommError(408, f"device login {status}")
            time.sleep(interval)
        raise CommError(408, "device login timed out")

    # Billing (pay-as-you-go credit)

    def billing(self) -> dict:
        """Current credit balance, spend, spend caps, and autopay state. Paid
        channels (e.g. WhatsApp, X, iMessage) draw down this balance; free
        channels (email, Telegram, Discord, Slack) never do."""
        return self._request("GET", "/v1/billing")

    def balance_cents(self) -> int:
        """Shortcut for the current credit balance in cents."""
        return self.billing()["balance_cents"]

    def top_up(self, amount_cents: int = 2000) -> dict:
        """Mint a Stripe-hosted checkout link to add credit. Returns
        ``{"checkout_url", "session_id", "amount_cents", ...}`` - open the URL
        (or hand it to whoever holds the card). Credit lands seconds after
        payment; poll ``billing()`` or watch for the ``billing.credited`` event.
        Minimum 100 cents ($1)."""
        return self._request("POST", "/v1/billing/topup", json={"amount_cents": amount_cents})

    def set_spend_limits(
        self, monthly_cap_cents: int | None = None, channel_caps: dict | None = None
    ) -> dict:
        """Cap spend so autopay/credit can't run away. ``monthly_cap_cents`` caps
        total monthly spend; ``channel_caps`` caps per channel (e.g.
        {"whatsapp": 5000}). Returns the updated billing state."""
        body: dict = {}
        if monthly_cap_cents is not None:
            body["monthly_cap_cents"] = monthly_cap_cents
        if channel_caps is not None:
            body["channel_caps"] = channel_caps
        return self._request("PUT", "/v1/billing/limits", json=body)

    def set_autopay(
        self,
        enabled: bool = True,
        threshold_cents: int | None = None,
        topup_cents: int | None = None,
        monthly_cap_cents: int | None = None,
    ) -> dict:
        """Auto-refill the balance from a saved card when it drops below
        ``threshold_cents`` (adds ``topup_cents``). Requires a card on file
        (complete one ``top_up()`` checkout first) and a ``monthly_cap_cents`` -
        an uncapped auto-replenishing budget is not allowed. Pass
        ``enabled=False`` to turn it off."""
        return self._request(
            "PUT",
            "/v1/billing/autopay",
            json={
                "enabled": enabled,
                "threshold_cents": threshold_cents,
                "topup_cents": topup_cents,
                "monthly_cap_cents": monthly_cap_cents,
            },
        )

    def send_message(
        self,
        conversation_id: str,
        text: str | None = None,
        html: str | None = None,
        blocks: list[dict] | None = None,
        media: list[dict] | None = None,
    ) -> dict:
        """Proactively send into an existing conversation (needs Capability.SEND).

        Pass ``blocks`` — a list of provider-neutral block dicts — for a rich
        message that renders natively on Slack/Discord/Telegram/email and
        degrades to clean text elsewhere. Pass ``media`` to attach files. See
        ``caspian_sdk.blocks``.
        """
        return self._request(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            json={"text": text, "html": html, "blocks": blocks, "media": media},
        )

    def initiate(self, connection_id: str, recipient: str, text: str) -> dict:
        """Cold-start a conversation (needs Capability.INITIATE — user account)."""
        return self._request(
            "POST",
            f"/v1/connections/{connection_id}/initiate",
            json={"recipient": recipient, "text": text},
        )

    def backfill(self, conversation_id: str, limit: int = 50) -> dict:
        """Pull history from before the connection (needs Capability.BACKFILL)."""
        return self._request(
            "POST", f"/v1/conversations/{conversation_id}/backfill", json={"limit": limit}
        )

    def test_email(
        self,
        text: str = "Hello from the comm test sender.",
        subject: str = "Test email",
        connection_id: str | None = None,
    ) -> dict:
        """Execute test_email."""
        body: dict = {"text": text, "subject": subject}
        if connection_id:
            body["connection_id"] = connection_id
        return self._request("POST", "/v1/test-emails", json=body)

    def events(self, after_seq: int = 0, limit: int = 100, type: str | None = None) -> list[dict]:
        """Execute events."""
        params: dict = {"after_seq": after_seq, "limit": limit}
        if type:
            params["type"] = type
        return self._request("GET", "/v1/events", params=params)

    # Event handling

    def on_message(self, handler: Callable[[Message], None]) -> Callable[[Message], None]:
        """Execute on_message."""
        self._handlers.append(handler)
        return handler

    def on_interaction(
        self, handler: Callable[["Interaction"], None]
    ) -> Callable[["Interaction"], None]:
        """Register a handler for button taps (interaction.received). The same
        handler answers taps from every channel that supports interactive
        buttons (Slack, Discord, Telegram)."""
        self._interaction_handlers.append(handler)
        return handler

    def on_reaction(self, handler: Callable[["Reaction"], None]) -> Callable[["Reaction"], None]:
        """Register a handler for emoji reactions (reaction.received)."""
        self._reaction_handlers.append(handler)
        return handler

    def _dispatch_event(self, event: dict) -> None:
        """Run handlers for one event. A handler that raises is logged and
        swallowed so one bad message can never stop the listener."""
        event_type = event.get("type")
        if event_type == "interaction.received":
            self._dispatch_interaction(event["data"])
            return
        if event_type == "reaction.received":
            self._dispatch_reaction(event["data"])
            return
        if event_type != "message.received":
            return
        message = self._build_message(event["data"])
        if self._handlers:
            # Show a 'thinking…' indicator up front so the human sees the agent is
            # working while the handler runs. Best-effort; never blocks dispatch.
            try:
                message.typing()
            except Exception:
                pass
            # Optional instant acknowledgement (listen(ack=...)) so the human gets
            # an immediate reply on channels with no typing indicator (X, SMS,
            # email). Best-effort; the real answer follows from the handler.
            if self._ack:
                try:
                    message.reply(self._ack)
                except InsufficientCreditError as exc:
                    self._warn_out_of_credit(exc)
                except Exception:
                    logger.exception("ack reply failed for message %s", message.id)
        for handler in self._handlers:
            try:
                handler(message)
            except AccountRequiredError as exc:
                # Paid channel used before the developer signed in. Surface the
                # one-time sign-in prompt loudly (e.g. in Claude Code).
                self._warn_account_required(exc)
            except InsufficientCreditError as exc:
                # The agent tried to reply on a paid channel but the project is
                # out of credit / capped. Make it loud on the CLI so the operator
                # (e.g. running this in Claude Code) sees it and can top up.
                self._warn_out_of_credit(exc)
            except Exception:
                logger.exception("on_message handler failed for message %s; continuing", message.id)

    def _warn_account_required(self, exc: "AccountRequiredError") -> None:
        """Print a prominent, rate-limited banner when a paid action needs sign-in."""
        now = time.monotonic()
        if now - self._last_credit_warning < 60:
            return
        self._last_credit_warning = now
        lines = [
            "",
            "  ┌─────────────────────────────────────────────────────────────┐",
            "  │  Caspian: SIGN-IN REQUIRED for paid channels                 │",
            "  └─────────────────────────────────────────────────────────────┘",
            f"  {exc.message}",
            "  Run:  comm login          (or client.login() in code)",
            "",
        ]
        print("\n".join(lines), file=sys.stderr, flush=True)

    def _warn_out_of_credit(self, exc: "InsufficientCreditError") -> None:
        """Print a prominent, rate-limited banner when a paid reply is blocked."""
        now = time.monotonic()
        if now - self._last_credit_warning < 60:
            return
        self._last_credit_warning = now
        balance = exc.balance_cents
        bal = f"${balance / 100:.2f}" if isinstance(balance, int) else "unknown"
        dash = next(
            (o.get("url") for o in exc.payment_options if o.get("url")),
            "https://dashboard.trycaspianai.com",
        )
        lines = [
            "",
            "  ┌─────────────────────────────────────────────────────────────┐",
            "  │  Caspian: OUT OF CREDIT - your agent could not reply         │",
            "  └─────────────────────────────────────────────────────────────┘",
            f"  {exc.message}",
            f"  Balance: {bal}",
            f"  Add credit in the dashboard:  {dash}",
            "",
        ]
        print("\n".join(lines), file=sys.stderr, flush=True)

    def dispatch_pending(self, after_seq: int = 0) -> int:
        """Process all currently available events once. Returns the last seen seq.

        Handler exceptions are caught per message, so this always drains the
        queue and advances the cursor even if some handlers fail.
        """
        last_seq = after_seq
        while True:
            batch = self.events(after_seq=last_seq)
            if not batch:
                return last_seq
            for event in batch:
                last_seq = event["seq"]
                self._dispatch_event(event)

    def listen(
        self,
        from_seq: int | None = None,
        poll_interval: float = 1.0,
        max_backoff: float = 30.0,
        ack: str | None = None,
        concurrency: ConcurrencyStrategy = "queue",
        debounce_ms: int = 500,
    ) -> None:
        """Poll the event stream forever, dispatching inbound messages to handlers.

        Resilient by design: a handler that raises is logged and skipped, and a
        failed poll (network blip, gateway restart) is retried with exponential
        backoff. This loop is meant to run for the lifetime of the agent and
        never exits on error — only KeyboardInterrupt / SIGINT stops it.

        Pass ``ack`` to send an instant acknowledgement reply (e.g. "On it, one
        moment…") the moment a message arrives, before your handler runs. Useful
        on channels with no typing indicator (X, SMS, email) so the human knows
        the agent is working while it thinks; the real answer follows.

        ``concurrency`` controls messages that overlap in one conversation:
        ``queue`` preserves order, ``debounce`` keeps the latest message,
        ``drop`` ignores new messages while a handler runs, and ``parallel`` runs
        every message immediately. Different conversations can run at the same
        time. ``queue`` is the default.
        """
        if ack is not None:
            self._ack = ack
        scheduler = _MessageScheduler(self._dispatch_event, concurrency, debounce_ms)
        try:
            seq = self._latest_seq() if from_seq is None else from_seq
            backoff = poll_interval
            while True:
                try:
                    batch = self.events(after_seq=seq)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    logger.warning("gateway poll failed; retrying in %.1fs", backoff, exc_info=True)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                    continue
                backoff = poll_interval
                if not batch:
                    time.sleep(poll_interval)
                    continue
                for event in batch:
                    scheduler.submit(event)
                    seq = event["seq"]  # advance after the scheduler accepts the event
        finally:
            scheduler.close()

    def handle_webhook(self, body: str | bytes, signature: str, secret: str) -> None:
        """Process a single event pushed by the gateway (serverless mode).

        Verifies the HMAC-SHA256 signature, enforces idempotency within this
        invocation, and routes the event to your registered handlers (e.g.
        ``@client.on_message``). Handlers run synchronously in the calling thread.

        Args:
            body: The raw HTTP request body.
            signature: The signature header from the gateway.
            secret: Your configured webhook secret.

        Raises:
            WebhookVerificationError: If the signature is invalid.
        """
        if isinstance(body, str):
            body = body.encode("utf-8")

        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookVerificationError()

        try:
            event = json.loads(body)
        except ValueError as exc:
            raise CommError(400, "invalid JSON payload") from exc

        if not isinstance(event, dict):
            raise CommError(400, "invalid JSON payload")

        event_id = event.get("id")
        if event_id and self.state.seen(event_id):
            return  # already handled in this invocation or by another instance

        data = event.get("data")
        if isinstance(data, dict):
            conv_id = data.get("message", {}).get("conversation_id") or data.get("conversation_id") or "default"
        else:
            conv_id = "default"
        with self.state.lock(conv_id):
            self._dispatch_event(event)

    def _latest_seq(self) -> int:
        """Newest seq at startup, retrying transient failures instead of crashing."""
        while True:
            try:
                seq = 0
                while True:
                    batch = self.events(after_seq=seq, limit=500)
                    if not batch:
                        return seq
                    seq = batch[-1]["seq"]
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.warning("could not read starting cursor; retrying in 2s", exc_info=True)
                time.sleep(2.0)

    def _dispatch_interaction(self, data: dict) -> None:
        interaction = Interaction(
            connection_id=data.get("connection_id", ""),
            customer_id=data.get("customer_id", ""),
            agent_id=data.get("agent_id", ""),
            conversation_id=data.get("conversation_id"),
            value=data.get("value"),
            source_message=data.get("source_message"),
            sender=data.get("sender"),
            _client=self,
        )
        for handler in self._interaction_handlers:
            try:
                handler(interaction)
            except InsufficientCreditError as exc:
                self._warn_out_of_credit(exc)
            except AccountRequiredError as exc:
                self._warn_account_required(exc)
            except Exception:
                logger.exception("on_interaction handler failed; continuing")

    def _dispatch_reaction(self, data: dict) -> None:
        reaction = Reaction(
            connection_id=data.get("connection_id", ""),
            customer_id=data.get("customer_id", ""),
            agent_id=data.get("agent_id", ""),
            emoji=data.get("emoji"),
            action=data.get("action", "added"),
            source_message=data.get("source_message"),
            sender=data.get("sender"),
            _client=self,
        )
        for handler in self._reaction_handlers:
            try:
                handler(reaction)
            except Exception:
                logger.exception("on_reaction handler failed; continuing")

    def _build_message(self, data: dict) -> Message:
        message = data["message"]
        return Message(
            id=message["id"],
            conversation_id=message["conversation_id"],
            connection_id=message["connection_id"],
            customer_id=data.get("customer_id", ""),
            agent_id=data.get("agent_id", ""),
            channel=message.get("channel", "email"),
            sender=message.get("sender"),
            subject=message.get("subject"),
            text=message.get("text"),
            html=message.get("html"),
            media=message.get("media") or [],
            _client=self,
        )
