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

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger("caspian_sdk")


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
    return explicit or os.environ.get(env_key) or _dotenv().get(env_key) or default


class CommError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


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

    def reply(self, text: str | None = None, html: str | None = None) -> dict:
        return self._client.reply(self.id, text=text, html=html)

    def typing(self) -> None:
        """Show a 'thinking…' typing indicator on the channel (Discord/Telegram;
        no-op where the platform has none). Fired automatically before your
        handler runs; call again during long work to keep it alive."""
        self._client.typing(self.id)


class CommClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        http: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        api_key = _config(api_key, "COMM_API_KEY")
        if not api_key:
            raise CommError(401, "No API key: pass api_key or set COMM_API_KEY (env or ./.env)")
        base_url = _config(base_url, "COMM_BASE_URL", "http://127.0.0.1:8000")
        self._api_key = api_key
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._handlers: list[Callable[[Message], None]] = []
        self._ack: str | None = None

    def close(self) -> None:
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
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise CommError(response.status_code, str(detail))
        if response.status_code == 204:
            return None
        return response.json()

    def _get_text(self, path: str) -> str:
        response = self._http.get(
            path, headers={"Authorization": f"Bearer {self._api_key}"}
        )
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
        return self._request("POST", "/v1/customers", json={"name": name})

    def create_agent(self, name: str) -> dict:
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
        return self._request("GET", "/v1/domains")

    def get_domain(self, domain_id: str) -> dict:
        return self._request("GET", f"/v1/domains/{domain_id}")

    def connect_phone(
        self, customer_id: str | None = None, agent_id: str | None = None,
        provider=None, **kwargs,
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
        self, customer_id=None, agent_id=None, display_name=None, capabilities=None,
    ) -> dict:
        """Begin WhatsApp onboarding for one of your customers (Caspian hosted).

        Returns ``{"session", "launcher_url", "expires_in"}``. Hand ``launcher_url``
        to whoever owns the WhatsApp Business account (open it, or embed it in your
        own UI): they click through Meta's popup once and their number is provisioned
        onto this agent - no tokens to copy, no Meta console steps on your side. The
        API key never reaches the browser (the session token stands in for it).

        Omit customer_id/agent_id to onboard onto this project's default scope, or
        pass both to target a specific customer+agent. Poll list_connections() /
        get_connection() (or watch for a connection.active event) until it's active.
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
        return self._request(
            "POST", "/v1/connections/whatsapp/onboarding-session", json=body
        )

    def connect_imessage(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Connect an iMessage line (Caspian hosted)."""
        return self._connect("imessage", customer_id, agent_id, **kwargs)

    def connect_rcs(self, customer_id=None, agent_id=None, **kwargs) -> dict:
        """Connect an RCS Business Messaging sender (Caspian hosted)."""
        return self._connect("rcs", customer_id, agent_id, **kwargs)

    def connect_discord(
        self, bot_token: str | None = None, webhook_url: str | None = None,
        username: str | None = None, avatar_url: str | None = None,
        customer_id=None, agent_id=None, **kwargs,
    ) -> dict:
        """Connect a Discord identity. Either a bot (`bot_token` from
        discord.com/developers) OR a channel `webhook_url` for a per-agent
        identity with a custom `username`/`avatar_url` (no bot needed)."""
        return self._connect(
            "discord", customer_id, agent_id, bot_token=bot_token,
            webhook_url=webhook_url, username=username, avatar_url=avatar_url, **kwargs,
        )

    def install_discord(self, customer_id=None, agent_id=None, display_name=None,
                        **kwargs) -> dict:
        """One-click install of the gateway's shared Discord bot (no bot token).

        Returns a connection with an ``authorize_url``. Open it (or hand it to the
        developer), pick a Discord server, and the shared bot joins it; messages in
        that server route to this agent. Zero setup - no bot to create.

        Pass ``display_name`` to give the bot YOUR custom name in that server (e.g.
        "Acme Support") - it appears under that name instead of the shared bot's
        name. Use connect_discord(bot_token=...) instead if you want a fully
        separate bot (your own name AND avatar, member-list included)."""
        body = {"customer_id": customer_id, "agent_id": agent_id,
                "display_name": display_name, **kwargs}
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
            "slack", customer_id, agent_id, wait=False,
            slack_client_id=slack_client_id,
            slack_client_secret=slack_client_secret,
            slack_signing_secret=slack_signing_secret,
            **kwargs,
        )

    def install_slack(self, customer_id=None, agent_id=None, display_name=None,
                      icon_url=None, **kwargs) -> dict:
        """One-click install of the gateway's shared Slack app (no app to create).

        Returns a connection with an ``authorize_url`` ("Add to Slack"). Open it
        (or hand it to the developer), pick a workspace, and the shared app
        installs there; messages in that workspace route to this agent. Zero setup
        - no Slack app to build. Pass ``display_name`` and ``icon_url`` to post
        under YOUR own name + icon (the plumbing stays invisible). Use
        connect_slack(slack_client_id=...) instead to bring your own Slack app."""
        body = {"customer_id": customer_id, "agent_id": agent_id,
                "display_name": display_name, "icon_url": icon_url, **kwargs}
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
            "PATCH", f"/v1/connections/{connection_id}",
            json={"display_name": display_name, "icon_url": icon_url},
        )

    def connect_x(
        self, access_token: str, user_id: str, access_secret: str | None = None,
        username: str | None = None, customer_id=None, agent_id=None, **kwargs,
    ) -> dict:
        """Connect an X (Twitter) account as a reactive DM bot.

        Bring the account's OAuth tokens: `access_token` + `user_id` (the numeric
        id, embedded before the dash in an OAuth 1.0a access token), and
        `access_secret` for a bring-your-own account. People DM the account and
        the agent replies; the gateway polls for inbound DMs (no webhook to set
        up). Reactive only - it never cold-DMs. The account must be labelled
        "Automated" in X settings."""
        return self._connect(
            "x", customer_id, agent_id, access_token=access_token, user_id=user_id,
            access_secret=access_secret, username=username, **kwargs,
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
        return self._request("GET", f"/v1/connections/{connection_id}")

    def list_conversations(self, connection_id: str | None = None) -> list[dict]:
        params = {"connection_id": connection_id} if connection_id else None
        return self._request("GET", "/v1/conversations", params=params)

    def list_messages(self, conversation_id: str) -> list[dict]:
        return self._request("GET", f"/v1/conversations/{conversation_id}/messages")

    def reply(self, message_id: str, text: str | None = None, html: str | None = None) -> dict:
        return self._request(
            "POST", f"/v1/messages/{message_id}/reply", json={"text": text, "html": html}
        )

    def typing(self, message_id: str) -> dict:
        """Show a 'thinking…' indicator on the channel a message arrived on
        (Discord/Telegram; no-op where unsupported). Best-effort."""
        return self._request("POST", f"/v1/messages/{message_id}/typing")

    def set_webhook(self, url: str, secret: str | None = None) -> dict:
        """Receive events by push instead of (or alongside) polling."""
        return self._request("PUT", "/v1/webhook", json={"url": url, "secret": secret})

    def get_webhook(self) -> dict:
        return self._request("GET", "/v1/webhook")

    def channels(self) -> list[dict]:
        """Configured transports and their capabilities."""
        return self._request("GET", "/v1/channels")

    def send_message(
        self, conversation_id: str, text: str | None = None, html: str | None = None
    ) -> dict:
        """Proactively send into an existing conversation (needs Capability.SEND)."""
        return self._request(
            "POST",
            f"/v1/conversations/{conversation_id}/messages",
            json={"text": text, "html": html},
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
        body: dict = {"text": text, "subject": subject}
        if connection_id:
            body["connection_id"] = connection_id
        return self._request("POST", "/v1/test-emails", json=body)

    def events(self, after_seq: int = 0, limit: int = 100, type: str | None = None) -> list[dict]:
        params: dict = {"after_seq": after_seq, "limit": limit}
        if type:
            params["type"] = type
        return self._request("GET", "/v1/events", params=params)

    # Event handling

    def on_message(self, handler: Callable[[Message], None]) -> Callable[[Message], None]:
        self._handlers.append(handler)
        return handler

    def _dispatch_event(self, event: dict) -> None:
        """Run handlers for one event. A handler that raises is logged and
        swallowed so one bad message can never stop the listener."""
        if event.get("type") != "message.received":
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
                except Exception:
                    logger.exception("ack reply failed for message %s", message.id)
        for handler in self._handlers:
            try:
                handler(message)
            except Exception:
                logger.exception(
                    "on_message handler failed for message %s; continuing", message.id
                )

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
        """
        if ack is not None:
            self._ack = ack
        seq = self._latest_seq() if from_seq is None else from_seq
        backoff = poll_interval
        while True:
            try:
                batch = self.events(after_seq=seq)
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.warning(
                    "gateway poll failed; retrying in %.1fs", backoff, exc_info=True
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
            backoff = poll_interval
            if not batch:
                time.sleep(poll_interval)
                continue
            for event in batch:
                self._dispatch_event(event)
                seq = event["seq"]  # advance only after the dispatch attempt

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
            _client=self,
        )
