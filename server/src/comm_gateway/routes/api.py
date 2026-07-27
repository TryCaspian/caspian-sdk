import re
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_project, get_session, hash_key
from ..billing import ensure_credit
from ..crypto import encrypt_credentials, read_credentials, write_credentials
from ..ids import new_id
from ..jobs import enqueue
from ..models import (
    Agent,
    ApiKey,
    Connection,
    Conversation,
    Customer,
    Domain,
    Event,
    Message,
    Project,
    ProviderEvent,
    utcnow,
)
from ..net import assert_public_url, client_ip
from ..providers import blocks as blocks_render
from ..providers.base import ALL_CAPABILITIES, BASELINE_CAPABILITIES, Capability
from ..schemas import (
    AgentCreate,
    AgentOut,
    BackfillCreate,
    ChannelConnectionCreate,
    ConnectionBrandingUpdate,
    ConnectionOut,
    ConversationOut,
    CustomerCreate,
    CustomerOut,
    DiscordConnectionCreate,
    EmailConnectionCreate,
    EventOut,
    InitiateCreate,
    MessageCreate,
    MessageOut,
    PhoneConnectionCreate,
    ReactCreate,
    ReplyCreate,
    SandboxProjectCreate,
    SandboxProjectOut,
    SlackConnectionCreate,
    TelegramConnectionCreate,
    TestEmailCreate,
    TestEmailOut,
    WebhookConfig,
    WebhookOut,
    XConnectionCreate,
    BlueskyConnectionCreate
)
from ..serialize import (
    agent_out,
    connection_out,
    conversation_out,
    customer_out,
    event_out,
    message_out,
)

router = APIRouter(prefix="/v1")

SANDBOX_RATE_LIMIT = 5
SANDBOX_RATE_WINDOW = 3600.0
_sandbox_requests: dict[str, list[float]] = {}

CONVERSATION_DAILY_REPLY_CAP = 25


@router.post("/projects/sandbox", response_model=SandboxProjectOut, status_code=201)
def create_sandbox_project(
    body: SandboxProjectCreate,
    request: Request,
    session: Session = Depends(get_session),
):
    """Mint an instant sandbox project and API key. No signup required."""
    ip = client_ip(request)  # CF-Connecting-IP behind Cloudflare, not the edge IP
    now = time.monotonic()
    window = [t for t in _sandbox_requests.get(ip, []) if now - t < SANDBOX_RATE_WINDOW]
    if not window:
        # Every timestamp expired: drop the key instead of leaking an empty list
        # (one per distinct client IP that ever hit this endpoint).
        _sandbox_requests.pop(ip, None)
    if len(window) >= SANDBOX_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Sandbox rate limit reached, try again later")
    window.append(now)
    _sandbox_requests[ip] = window

    api_key = f"comm_sandbox_{secrets.token_hex(20)}"
    project = Project(id=new_id("proj"), name=body.name or "sandbox")
    session.add(project)
    session.flush()  # insert the project before its FK children (Postgres enforces FKs)
    session.add(ApiKey(id=new_id("key"), project_id=project.id, key_hash=hash_key(api_key)))
    session.commit()
    from ..analytics import capture

    capture(project.id, "gateway.project_created", {"source": "sandbox"})
    return {"project_id": project.id, "api_key": api_key}


def _default_scope(session: Session, project: Project) -> tuple[Customer, Agent]:
    customer = session.execute(
        select(Customer).where(Customer.project_id == project.id, Customer.name == "default")
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(id=new_id("cus"), project_id=project.id, name="default")
        session.add(customer)
    agent = session.execute(
        select(Agent).where(Agent.project_id == project.id, Agent.name == "default")
    ).scalar_one_or_none()
    if agent is None:
        agent = Agent(id=new_id("agt"), project_id=project.id, name="default")
        session.add(agent)
    return customer, agent


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(
    body: CustomerCreate,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    customer = Customer(id=new_id("cus"), project_id=project.id, name=body.name)
    session.add(customer)
    session.commit()
    return customer_out(customer)


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Customer).where(Customer.project_id == project.id).order_by(Customer.created_at)
    ).scalars()
    return [customer_out(c) for c in rows]


@router.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(
    body: AgentCreate,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    agent = Agent(id=new_id("agt"), project_id=project.id, name=body.name)
    session.add(agent)
    session.commit()
    return agent_out(agent)


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Agent).where(Agent.project_id == project.id).order_by(Agent.created_at)
    ).scalars()
    return [agent_out(a) for a in rows]


def _provider_for_channel(request: Request, channel: str, provider_name: str | None = None):
    """Pick the provider that will back a new connection on `channel`.

    A channel may have more than one provider configured (e.g. whatsapp served by
    both twilio-whatsapp and meta-whatsapp). `provider_name` selects one explicitly;
    otherwise the first configured for the channel (COMM_PROVIDERS order) is the
    default, which keeps single-provider deployments behaving exactly as before.
    """
    matches = [p for p in request.app.state.providers.values() if p.channel == channel]
    if not matches:
        raise HTTPException(
            status_code=400, detail=f"No provider configured for channel {channel!r}"
        )
    if provider_name:
        for provider in matches:
            if provider.name == provider_name:
                return provider
        available = ", ".join(p.name for p in matches)
        raise HTTPException(
            status_code=422,
            detail=f"Provider {provider_name!r} is not configured for channel {channel!r} "
            f"(available: {available})",
        )
    return matches[0]


def _resolve_manifest(provider, requested: list[str] | None) -> list[str]:
    """Turn an agent's requested capability manifest into a granted set.

    Like a Slack app manifest: the agent declares which capabilities it wants.
    Omitting the manifest grants everything the transport supports. A requested
    capability that is unknown, or that this transport can't provide, is a 422 —
    an agent can never be granted more than the channel physically supports.
    Baseline receive/reply are always granted.
    """
    ceiling = provider.capabilities
    if requested is None:
        return sorted(ceiling)
    unknown = sorted(set(requested) - ALL_CAPABILITIES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown capabilities: {unknown}")
    beyond = sorted(set(requested) - ceiling)
    if beyond:
        raise HTTPException(
            status_code=422,
            detail=f"Channel {provider.channel!r} cannot provide: {beyond}",
        )
    granted = set(requested) | (BASELINE_CAPABILITIES & ceiling)
    return sorted(granted)


def _connect_credentials(provider, body) -> dict:
    """Collect the credentials this transport requires from the connect body.

    Transports we fully own (email) require none. Transports built on a
    developer-owned resource (a Telegram bot) declare required fields; a
    missing one is a 422 with the exact field name.
    """
    credentials: dict = {}
    for field in getattr(provider, "connect_credentials", ()):
        value = getattr(body, field, None)
        if not value:
            raise HTTPException(
                status_code=422,
                detail=f"Channel {provider.channel!r} requires {field!r} to connect",
            )
        credentials[field] = value
    for field in getattr(provider, "optional_connect_credentials", ()):
        value = getattr(body, field, None)
        if value and field not in credentials:
            credentials[field] = value
    return credentials


def _bot_resource_id(channel: str, bot_token: str) -> str | None:
    """Derive the stable provider id a bot token points at, per platform.

    Telegram tokens are `<id>:<hash>`; Discord tokens are three base64 parts
    whose first part decodes to the application id. This lets one bot map to
    exactly one connection regardless of platform.
    """
    if channel == "telegram":
        return bot_token.split(":", 1)[0] if ":" in bot_token else None
    if channel == "discord":
        import base64

        first = bot_token.split(".", 1)[0]
        try:
            return base64.b64decode(first + "=" * (-len(first) % 4)).decode()
        except Exception:
            return None
    return None


def _free_whatsapp_number(session: Session, provider) -> str | None:
    """A Caspian-owned Twilio WhatsApp sender not already held by a live
    connection (Option 1a per-agent allocation)."""
    used = set(
        session.execute(
            select(Connection.provider_resource_id).where(
                Connection.provider == provider.name,
                Connection.status.in_(["provisioning", "active"]),
            )
        ).scalars()
    )
    for number in provider.pool_numbers:
        if number not in used:
            return number
    return None


_USERNAME_RE = re.compile(r"[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?")


def _email_taken(session, address: str, customer_id: str) -> bool:
    """True if another customer already holds this email address (live/provisioning)."""
    taken = session.execute(
        select(Connection).where(
            Connection.address == address,
            Connection.status.in_(["provisioning", "active"]),
        )
    ).scalars().first()
    return taken is not None and taken.customer_id != customer_id


def _email_free(session, address: str) -> bool:
    return session.execute(
        select(Connection.id).where(
            Connection.address == address,
            Connection.status.in_(["provisioning", "active"]),
        )
    ).first() is None


def _suggest_email_usernames(session, base: str, domain: str, limit: int = 5) -> list[str]:
    """Readable, still-free alternatives when a chosen mailbox name is taken.

    Deliberately no random hex - numeric and word variations a human would
    actually pick (scout1, scout-ai, get-scout), filtered to ones still free.
    """
    candidates = (
        [f"{base}{n}" for n in (1, 2, 3)]
        + [f"{base}-{s}" for s in ("ai", "bot", "hq", "team", "app")]
        + [f"{p}-{base}" for p in ("get", "hey", "my")]
    )
    out: list[str] = []
    for c in candidates:
        if _USERNAME_RE.fullmatch(c) and _email_free(session, f"{c}@{domain}"):
            out.append(c)
        if len(out) >= limit:
            break
    return out


def _create_connection(request, session, project, body, channel: str) -> dict:
    # Resolve scope: default customer/agent when both are omitted (zero-config),
    # otherwise both must be provided and must exist.
    if body.customer_id is None and body.agent_id is None:
        customer, agent = _default_scope(session, project)
    else:
        if body.customer_id is None or body.agent_id is None:
            raise HTTPException(
                status_code=422, detail="Provide both customer_id and agent_id, or neither"
            )
        customer = session.get(Customer, body.customer_id)
        if customer is None or customer.project_id != project.id:
            raise HTTPException(status_code=404, detail="Customer not found")
        agent = session.get(Agent, body.agent_id)
        if agent is None or agent.project_id != project.id:
            raise HTTPException(status_code=404, detail="Agent not found")

    provider = _provider_for_channel(request, channel, getattr(body, "provider", None))
    # Paid channels are gated on prepaid credit, not on an account: no signup,
    # no dashboard. A 402 carries payment_options so the caller can fix it.
    ensure_credit(request, session, project, channel)
    credentials = _connect_credentials(provider, body)

    # OAuth channels (Slack, Instagram, Facebook): the token comes from the
    # install callback, not the connect body. Reuse a live connection for the
    # scope, else create a pending one and hand back the authorize URL.
    if getattr(provider, "oauth", False):
        existing = session.execute(
            select(Connection).where(
                Connection.project_id == project.id,
                Connection.customer_id == customer.id,
                Connection.agent_id == agent.id,
                Connection.channel == channel,
                Connection.provider == provider.name,
                Connection.status.in_(["pending_oauth", "provisioning", "active"]),
            )
        ).scalars().first()
        if existing is not None:
            out = connection_out(existing)
            if existing.status == "pending_oauth":
                out["authorize_url"] = read_credentials(existing).get("authorize_url")
            return out
        # bring-your-own app credentials (developer creates their own Slack app)
        app_creds = {
            k: getattr(body, k)
            for k in ("slack_client_id", "slack_client_secret", "slack_signing_secret")
            if getattr(body, k, None)
        }
        has_own = len(app_creds) == 3
        has_shared = bool(getattr(provider, "client_id", ""))
        if not has_own and not has_shared:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Provide slack_client_id, slack_client_secret and slack_signing_secret "
                    "(create a Slack app at api.slack.com/apps), or configure a shared app."
                ),
            )
        state = secrets.token_urlsafe(24)
        base = request.app.state.settings.public_base_url or str(request.base_url).rstrip("/")
        redirect_uri = f"{base}/v1/oauth/{provider.name}/callback"
        authorize_url = provider.authorize_url(redirect_uri, state, app=app_creds or None)
        granted = _resolve_manifest(provider, getattr(body, "capabilities", None))
        connection = Connection(
            id=new_id("conn"),
            project_id=project.id,
            customer_id=customer.id,
            agent_id=agent.id,
            channel=channel,
            capabilities=granted,
            status="pending_oauth",
            provider=provider.name,
            provider_credentials=encrypt_credentials({
                "oauth_state": state,
                "redirect_uri": redirect_uri,
                "authorize_url": authorize_url,
                **app_creds,
            }),
        )
        session.add(connection)
        session.commit()
        out = connection_out(connection)
        out["authorize_url"] = authorize_url
        return out

    # optional custom domain (email): must be owned by this project and active
    domain = getattr(body, "domain", None)
    if domain:
        domain = domain.strip().lower()
        owned = session.execute(
            select(Domain).where(Domain.domain == domain, Domain.project_id == project.id)
        ).scalar_one_or_none()
        if owned is None:
            raise HTTPException(status_code=404, detail="Domain not found; add it via /v1/domains")
        if owned.status != "active":
            raise HTTPException(
                status_code=409,
                detail="Domain is not verified yet; add its DNS records and re-check",
            )

    # Exact mailbox name the developer picks, e.g. scout@<your-agent-domain>.
    # Works on the default platform domain (a readable, human-chosen name) or a
    # verified custom domain. Globally unique; on a clash we hand back free
    # suggestions instead of a bare 409 so the agent can offer a choice.
    # Only email uses `username` this way; on Discord it's the webhook display name.
    username = getattr(body, "username", None) if channel == "email" else None
    if username:
        username = username.strip().lower()
        if not _USERNAME_RE.fullmatch(username):
            raise HTTPException(status_code=422, detail="Not a valid email username")
        target_domain = (
            domain
            or getattr(provider, "default_domain", None)
            or request.app.state.settings.ses_domain
        )
        if _email_taken(session, f"{username}@{target_domain}", customer.id):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"{username}@{target_domain} is already taken",
                    "suggestions": _suggest_email_usernames(session, username, target_domain),
                },
            )

    # A developer-supplied bot token / webhook pins the connection up front.
    resource_id = None
    if "bot_token" in credentials:
        resource_id = _bot_resource_id(channel, credentials["bot_token"])
        if resource_id is None:
            raise HTTPException(status_code=422, detail="bot_token is not a valid token")
        credentials["webhook_secret"] = secrets.token_hex(16)
    elif channel == "discord" and credentials.get("webhook_url"):
        from ..providers.discord import webhook_id_from_url

        try:
            resource_id = webhook_id_from_url(credentials["webhook_url"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif channel == "discord":
        raise HTTPException(
            status_code=422, detail="discord needs a bot_token or a webhook_url"
        )
    elif channel == "x" and credentials.get("user_id"):
        # Pin the connection to the account's numeric user id up front: the
        # Account Activity webhook routes inbound by for_user_id, so one X
        # account maps to exactly one connection (one bot = one connection).
        resource_id = credentials["user_id"]
    elif (
        channel == "whatsapp"
        and "from_number" not in credentials
        and getattr(provider, "pool_numbers", [])
    ):
        # Option 1a: hand this agent its own Caspian-owned Twilio WhatsApp sender
        # from the pool, zero customer effort. Pin it as the resource id and stash
        # it in credentials so provision/send use it. No pool ⇒ fall through to the
        # shared-number default (resource_id set by provision), unchanged.
        number = _free_whatsapp_number(session, provider)
        if number is None:
            raise HTTPException(
                status_code=409, detail="No WhatsApp numbers available in the pool"
            )
        credentials["from_number"] = number
        resource_id = number

    # connect is idempotent: reuse a live connection for this scope + channel,
    # but only on the currently configured provider (and same bot, if pinned)
    reuse = select(Connection).where(
        Connection.project_id == project.id,
        Connection.customer_id == customer.id,
        Connection.agent_id == agent.id,
        Connection.channel == channel,
        Connection.provider == provider.name,
        Connection.status.in_(["provisioning", "active"]),
    )
    if resource_id is not None:
        reuse = reuse.where(Connection.provider_resource_id == resource_id)
    reuse = reuse.where(Connection.domain == domain if domain else Connection.domain.is_(None))
    existing = session.execute(reuse).scalars().first()
    if existing is not None:
        return connection_out(existing)

    if resource_id is not None:
        # one bot = one connection, across all projects: inbound updates route
        # by bot id, so a shared bot would leak messages between developers
        taken = session.execute(
            select(Connection).where(
                Connection.provider == provider.name,
                Connection.provider_resource_id == resource_id,
                Connection.status.in_(["provisioning", "active"]),
            )
        ).scalars().first()
        if taken is not None:
            raise HTTPException(
                status_code=409,
                detail="This bot is already connected; each connection needs its own bot",
            )

    granted = _resolve_manifest(provider, getattr(body, "capabilities", None))

    connection = Connection(
        id=new_id("conn"),
        project_id=project.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=channel,
        capabilities=granted,
        status="provisioning",
        provider=provider.name,
        provider_resource_id=resource_id,
        provider_credentials=encrypt_credentials(credentials or None),
        domain=domain,
    )
    session.add(connection)
    enqueue(
        session,
        "provision_connection",
        {
            "connection_id": connection.id,
            "display_name": body.display_name,
            "domain": domain,
            "username": username,
        },
    )
    session.commit()
    return connection_out(connection)


def _require_granted(connection: Connection, capability: str) -> None:
    if capability not in (connection.capabilities or []):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Connection does not grant {capability!r}; "
                "add it to the connection's capability manifest"
            ),
        )


@router.post("/connections/email", response_model=ConnectionOut, status_code=201)
def create_email_connection(
    body: EmailConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="email")


@router.post("/connections/telegram", response_model=ConnectionOut, status_code=201)
def create_telegram_connection(
    body: TelegramConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="telegram")


@router.post("/connections/phone", response_model=ConnectionOut, status_code=201)
def create_phone_connection(
    body: PhoneConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="phone")


@router.post("/connections/voice", response_model=ConnectionOut, status_code=201)
def create_voice_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="voice")


@router.post("/connections/x", response_model=ConnectionOut, status_code=201)
def create_x_connection(
    body: XConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Connect an X (Twitter) account for posting + reactive DMs.

    The account must already exist, be labelled "Automated", and have granted
    an OAuth 2.0 user access token (scopes: tweet.write, dm.read, dm.write, ...).
    Supply that token plus the account's numeric user_id. Reactive-DM only: the
    agent replies to inbound DMs and can post tweets, never cold-DMs.
    """
    return _create_connection(request, session, project, body, channel="x")

@router.post("/connections/bluesky", response_model=ConnectionOut, status_code=201)
def create_bluesky_connection(
    body: BlueskyConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(
        request,
        session,
        project,
        body,
        channel="bluesky",
    )

@router.post("/connections/x/install", response_model=ConnectionOut, status_code=201)
def install_x(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """One-click connect of an X account as a reactive DM bot (no token pasting).

    Returns a connection with an ``authorize_url`` ("Sign in with X"). The
    developer opens it, authorizes on X, and the callback stores the account's
    non-expiring tokens; the account becomes the bot (people DM it, the agent
    replies). Uses the gateway's shared X app via OAuth 1.0a 3-legged."""
    provider = _provider_for_channel(request, "x")
    if not getattr(provider, "_consumer_key", ""):
        raise HTTPException(
            status_code=400,
            detail="Shared X app is not configured on this gateway "
            "(set COMM_X_API_KEY / COMM_X_API_SECRET). Use connect_x(access_token=...) "
            "to bring your own account tokens instead.",
        )
    # Gate BEFORE minting the X authorize link (but after confirming the channel
    # is even available here): X is paid, so require the one-time sign-in (401)
    # and credit (402) up front, not after the developer authorizes on X.
    ensure_credit(request, session, project, "x")
    if body.customer_id is None and body.agent_id is None:
        customer, agent = _default_scope(session, project)
    else:
        if body.customer_id is None or body.agent_id is None:
            raise HTTPException(
                status_code=422, detail="Provide both customer_id and agent_id, or neither"
            )
        customer = session.get(Customer, body.customer_id)
        agent = session.get(Agent, body.agent_id)
        if customer is None or customer.project_id != project.id or agent is None \
                or agent.project_id != project.id:
            raise HTTPException(status_code=404, detail="Customer or agent not found")

    base = request.app.state.settings.public_base_url or str(request.base_url).rstrip("/")
    callback_url = f"{base}/v1/oauth/x/callback"
    tok = provider.oauth_request_token(callback_url)
    creds = {"oauth_token": tok["oauth_token"],
             "oauth_token_secret": tok["oauth_token_secret"],
             "authorize_url": tok["authorize_url"]}
    connection = Connection(
        id=new_id("conn"),
        project_id=project.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="x",
        capabilities=_resolve_manifest(provider, getattr(body, "capabilities", None)),
        status="pending_oauth",
        provider=provider.name,
        provider_credentials=encrypt_credentials(creds),
    )
    session.add(connection)
    session.commit()
    out = connection_out(connection)
    out["authorize_url"] = tok["authorize_url"]
    return out


@router.post("/connections/whatsapp", response_model=ConnectionOut, status_code=201)
def create_whatsapp_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="whatsapp")


@router.post("/connections/imessage", response_model=ConnectionOut, status_code=201)
def create_imessage_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="imessage")


@router.post("/connections/rcs", response_model=ConnectionOut, status_code=201)
def create_rcs_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="rcs")


@router.post("/connections/slack", response_model=ConnectionOut, status_code=201)
def create_slack_connection(
    body: SlackConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="slack")


@router.post("/connections/slack/install", response_model=ConnectionOut, status_code=201)
def install_shared_slack(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """One-click install of the gateway's shared Slack app (no app to create).

    Returns a connection with an authorize_url ("Add to Slack"). The developer
    opens it, picks their workspace, and the shared app installs; the callback
    maps that workspace (team) to this connection. Pass display_name/icon_url to
    post under the developer's own brand (chat:write.customize)."""
    provider = _provider_for_channel(request, "slack")
    if not getattr(provider, "client_id", ""):
        raise HTTPException(
            status_code=400,
            detail="Shared Slack app is not configured on this gateway "
            "(set COMM_SLACK_CLIENT_ID / COMM_SLACK_CLIENT_SECRET / "
            "COMM_SLACK_SIGNING_SECRET). Use connect_slack(slack_client_id=...) to "
            "bring your own app instead.",
        )
    if body.customer_id is None and body.agent_id is None:
        customer, agent = _default_scope(session, project)
    else:
        if body.customer_id is None or body.agent_id is None:
            raise HTTPException(
                status_code=422, detail="Provide both customer_id and agent_id, or neither"
            )
        customer = session.get(Customer, body.customer_id)
        agent = session.get(Agent, body.agent_id)
        if customer is None or customer.project_id != project.id or agent is None \
                or agent.project_id != project.id:
            raise HTTPException(status_code=404, detail="Customer or agent not found")

    pool_index = _assign_slack_pool_index(session, provider, project)
    connection, authorize_url = _slack_pending_install(
        request, provider, project, customer.id, agent.id,
        pool_index=pool_index, display_name=body.display_name, icon_url=body.icon_url,
        capabilities=getattr(body, "capabilities", None),
    )
    session.add(connection)
    session.commit()
    out = connection_out(connection)
    out["authorize_url"] = authorize_url
    return out


def _assign_slack_pool_index(session, provider, project) -> int:
    """Pick which pool app THIS developer (project) uses. Coalition case: two
    developers whose agents land in ONE workspace must use DIFFERENT apps (Slack
    allows one install of an app per workspace, and re-installing the same app
    would clobber the first). So each project gets a consistent app, round-robined
    across projects, so distinct developers get distinct apps."""
    if provider.pool_size() <= 1:
        return 0
    # Reuse this project's existing assignment (a project always uses one app).
    existing = session.execute(
        select(Connection).where(
            Connection.provider == provider.name,
            Connection.project_id == project.id,
        ).order_by(Connection.created_at)
    ).scalars().first()
    if existing is not None:
        idx = read_credentials(existing).get("pool_index")
        if idx is not None:
            return int(idx)
    # New project: round-robin by how many distinct projects already use Slack.
    distinct = session.execute(
        select(func.count(func.distinct(Connection.project_id))).where(
            Connection.provider == provider.name
        )
    ).scalar() or 0
    return distinct % provider.pool_size()


def _slack_pending_install(request, provider, project, customer_id, agent_id, *,
                           pool_index, display_name=None, icon_url=None, capabilities=None):
    """Create a pending Slack install connection pinned to pool app `pool_index`.

    Stores the chosen app's creds so exchange/verify use the right app, plus the
    pool index so the OAuth callback can fall back to the next app on a collision
    (two agents in one workspace need distinct apps). Returns (connection, url)."""
    app = provider.app_at(pool_index)
    state = secrets.token_urlsafe(24)
    base = request.app.state.settings.public_base_url or str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/v1/oauth/{provider.name}/callback"
    creds = {
        "oauth_state": state,
        "redirect_uri": redirect_uri,
        "pool_index": pool_index,
        "slack_client_id": app.get("client_id", ""),
        "slack_client_secret": app.get("client_secret", ""),
        "slack_signing_secret": app.get("signing_secret", ""),
        "slack_app_id": app.get("app_id", ""),
    }
    if display_name:
        creds["display_name"] = display_name
    if icon_url:
        creds["icon_url"] = icon_url
    authorize_url = provider.authorize_url(redirect_uri, state, app=creds)
    creds["authorize_url"] = authorize_url
    connection = Connection(
        id=new_id("conn"),
        project_id=project.id,
        customer_id=customer_id,
        agent_id=agent_id,
        channel="slack",
        capabilities=_resolve_manifest(provider, capabilities),
        status="pending_oauth",
        provider=provider.name,
        provider_credentials=encrypt_credentials(creds),
    )
    return connection, authorize_url


@router.post("/connections/discord", response_model=ConnectionOut, status_code=201)
def create_discord_connection(
    body: DiscordConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="discord")


@router.post("/connections/discord/install", response_model=ConnectionOut, status_code=201)
def install_shared_discord(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """One-click install of the shared Caspian Discord bot.

    Returns a connection with an authorize_url. The developer opens it, picks their
    server, and Discord adds the shared bot; the callback maps that server (guild)
    to this connection. No bot token to create - the whole point of this path.
    """
    settings = request.app.state.settings
    if not (settings.discord_client_id and settings.discord_bot_token):
        raise HTTPException(
            status_code=400,
            detail="Shared Discord bot is not configured on this gateway "
            "(set COMM_DISCORD_CLIENT_ID / COMM_DISCORD_BOT_TOKEN). Use "
            "connect_discord(bot_token=...) to bring your own bot instead.",
        )
    if body.customer_id is None and body.agent_id is None:
        customer, agent = _default_scope(session, project)
    else:
        if body.customer_id is None or body.agent_id is None:
            raise HTTPException(
                status_code=422, detail="Provide both customer_id and agent_id, or neither"
            )
        customer = session.get(Customer, body.customer_id)
        agent = session.get(Agent, body.agent_id)
        if customer is None or customer.project_id != project.id or agent is None \
                or agent.project_id != project.id:
            raise HTTPException(status_code=404, detail="Customer or agent not found")

    from ..providers.discord import install_url

    provider = _provider_for_channel(request, "discord")
    state = secrets.token_urlsafe(24)
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/v1/oauth/discord/callback"
    authorize_url = install_url(
        settings.discord_base_url, settings.discord_client_id,
        settings.discord_bot_permissions, redirect_uri, state,
    )
    connection = Connection(
        id=new_id("conn"),
        project_id=project.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="discord",
        capabilities=_resolve_manifest(provider, getattr(body, "capabilities", None)),
        status="pending_oauth",
        provider="discord",
        provider_credentials=encrypt_credentials({
            "oauth_state": state,
            "shared_bot": True,
            # The developer's custom name for the bot in their server (per-server
            # nickname on the shared bot). Applied when the bot joins the server.
            **({"nickname": body.display_name} if body.display_name else {}),
        }),
    )
    session.add(connection)
    session.commit()
    out = connection_out(connection)
    out["authorize_url"] = authorize_url
    return out


@router.post("/connections/instagram", response_model=ConnectionOut, status_code=201)
def create_instagram_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="instagram")


@router.post("/connections/facebook", response_model=ConnectionOut, status_code=201)
def create_facebook_connection(
    body: ChannelConnectionCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    return _create_connection(request, session, project, body, channel="facebook")


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
def update_connection_branding(
    connection_id: str,
    body: ConnectionBrandingUpdate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Change the name/icon the agent posts under, after connect.

    Slack: applied on the next message (per-message identity). Discord shared bot:
    re-sets the bot's per-server nickname so the name changes in the server too."""
    connection = session.get(Connection, connection_id)
    if connection is None or connection.project_id != project.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    creds = read_credentials(connection)
    if body.display_name is not None:
        creds["display_name"] = body.display_name
    if body.icon_url is not None:
        creds["icon_url"] = body.icon_url
    write_credentials(connection, creds)
    session.commit()

    # Discord shared bot: the name is a per-server nickname, so re-apply it.
    settings = request.app.state.settings
    if (body.display_name and creds.get("shared_bot") and connection.provider == "discord"
            and connection.provider_resource_id and settings.discord_bot_token):
        from ..providers.discord import set_bot_nickname
        try:
            set_bot_nickname(settings.discord_base_url, settings.discord_bot_token,
                             connection.provider_resource_id, body.display_name)
        except Exception:
            pass  # best-effort; the stored name still updates for future sends
    return connection_out(connection)


@router.delete("/connections/{connection_id}", response_model=ConnectionOut, status_code=202)
def release_connection(
    connection_id: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Deprovision a connection — releases a commissioned number back to the carrier."""
    connection = session.get(Connection, connection_id)
    if connection is None or connection.project_id != project.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.status != "released":
        connection.status = "releasing"
        enqueue(session, "release_connection", {"connection_id": connection.id})
        session.commit()
    return connection_out(connection)


@router.put("/webhook", response_model=WebhookOut)
def set_webhook(
    body: WebhookConfig,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Register a URL to receive all events by push (in addition to polling)."""
    assert_public_url(body.url)  # SSRF guard: no internal/loopback/link-local targets
    project.webhook_url = body.url
    project.webhook_secret = body.secret
    session.commit()
    return {"url": project.webhook_url, "configured": True}


@router.get("/webhook", response_model=WebhookOut)
def get_webhook(project: Project = Depends(get_project)):
    return {"url": project.webhook_url, "configured": bool(project.webhook_url)}


@router.delete("/webhook", response_model=WebhookOut, status_code=200)
def delete_webhook(
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    project.webhook_url = None
    project.webhook_secret = None
    session.commit()
    return {"url": None, "configured": False}


@router.get("/channels")
def list_channels(request: Request):
    """Configured transports and what each can actually do.

    Callers should check capabilities before offering an operation rather than
    assuming every channel behaves like every other.
    """
    from ..channel_guides import setup_for

    return [
        {
            "channel": provider.channel,
            "provider": provider.name,
            "capabilities": sorted(provider.capabilities),
            # What a developer must supply at connect (surfaced so onboarding is
            # self-serve: a client can render exactly what each channel needs).
            "required_credentials": list(
                getattr(provider, "connect_credentials", ())
            ),
            "optional_credentials": list(
                getattr(provider, "optional_connect_credentials", ())
            ),
            "setup": setup_for(provider.channel),
        }
        for provider in request.app.state.providers.values()
    ]


@router.post("/connections/{connection_id}/initiate", status_code=202)
def initiate_conversation(
    connection_id: str,
    body: InitiateCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Cold-start a conversation with a recipient who has never messaged us.

    Requires a transport with Capability.INITIATE (a Telegram user account, not
    the Bot API). Returns 202; watch for the resulting message.sent event.
    """
    connection = session.get(Connection, connection_id)
    if connection is None or connection.project_id != project.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    _require_granted(connection, Capability.INITIATE)
    text = body.text or (blocks_render.to_text(body.blocks) if body.blocks else None)
    if not text and not body.blocks and not body.media:
        raise HTTPException(status_code=422, detail="Initiate requires text, blocks or media")
    ensure_credit(request, session, project, connection.channel)
    enqueue(
        session,
        "initiate",
        {"connection_id": connection.id, "recipient": body.recipient,
         "text": text, "blocks": body.blocks, "media": body.media},
    )
    session.commit()
    return {"connection_id": connection.id, "recipient": body.recipient, "status": "queued"}


@router.post("/conversations/{conversation_id}/backfill", status_code=202)
def backfill_conversation(
    conversation_id: str,
    body: BackfillCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Pull history from before the connection existed.

    Requires a transport with Capability.BACKFILL. Emits message.backfilled
    events (distinct from live message.received so consumers don't re-trigger).
    """
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    connection = session.get(Connection, conversation.connection_id)
    _require_granted(connection, Capability.BACKFILL)
    enqueue(
        session,
        "backfill",
        {"conversation_id": conversation.id, "limit": body.limit},
    )
    session.commit()
    return {"conversation_id": conversation.id, "status": "queued"}


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Connection)
        .where(Connection.project_id == project.id)
        .order_by(Connection.created_at)
    ).scalars()
    return [connection_out(c) for c in rows]


@router.get("/connections/{connection_id}", response_model=ConnectionOut)
def get_connection(
    connection_id: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    connection = session.get(Connection, connection_id)
    if connection is None or connection.project_id != project.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection_out(connection)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    connection_id: str | None = None,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    query = select(Conversation).where(Conversation.project_id == project.id)
    if connection_id:
        query = query.where(Conversation.connection_id == connection_id)
    rows = session.execute(query.order_by(Conversation.created_at)).scalars()
    return [conversation_out(c) for c in rows]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_conversation_messages(
    conversation_id: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
    ).scalars()
    return [message_out(m) for m in rows]


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=201,
)
def send_conversation_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Proactively send into an existing conversation.

    The counterparty must already have an open conversation (on the Bot API a
    user must have messaged the agent first). Cold-starting a brand new
    conversation needs a transport with Capability.INITIATE.
    """
    if not body.text and not body.html and not body.blocks and not body.media:
        raise HTTPException(
            status_code=422, detail="Message requires text, html, blocks or media"
        )
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    connection = session.get(Connection, conversation.connection_id)
    _require_granted(connection, Capability.SEND)
    ensure_credit(request, session, project, connection.channel)

    # Flatten blocks to text/html so history, email, and text-only channels have a
    # rendering with no provider changes; the raw blocks ride the job payload for
    # channels that render them natively.
    text = body.text or (blocks_render.to_text(body.blocks) if body.blocks else None)
    html = body.html or (blocks_render.to_html(body.blocks) if body.blocks else None)
    message = Message(
        id=new_id("msg"),
        project_id=project.id,
        conversation_id=conversation.id,
        connection_id=connection.id,
        channel=connection.channel,
        direction="outbound",
        status="queued",
        sender_address=connection.address,
        subject=conversation.subject,
        text=text,
        html=html,
        media=body.media or [],
    )
    session.add(message)
    enqueue(session, "send_message", {"message_id": message.id, "blocks": body.blocks})
    session.commit()
    return message_out(message)


@router.get("/messages/{message_id}", response_model=MessageOut)
def get_message(
    message_id: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    message = session.get(Message, message_id)
    if message is None or message.project_id != project.id:
        raise HTTPException(status_code=404, detail="Message not found")
    return message_out(message)


@router.post("/messages/{message_id}/typing", status_code=202)
def message_typing(
    message_id: str,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Show a 'thinking…' typing indicator on the channel this message arrived on.

    Best-effort: called by the SDK the moment an inbound message is dispatched, so
    the human sees the agent is working while the handler (LLM) runs. Channels
    without a native typing signal (Slack/X/email) are a silent no-op."""
    target = session.get(Message, message_id)
    if target is None or target.project_id != project.id:
        raise HTTPException(status_code=404, detail="Message not found")
    connection = session.get(Connection, target.connection_id)
    conversation = session.get(Conversation, target.conversation_id)
    thread = conversation.provider_thread_id if conversation else None
    provider = request.app.state.providers.get(connection.provider) if connection else None
    sent = False
    if provider is not None and thread and hasattr(provider, "typing"):
        try:
            provider.typing(thread, read_credentials(connection))
            sent = True
        except Exception:
            pass  # best-effort; never block the conversation on a typing hint
    return {"ok": True, "typing_sent": sent}


@router.post("/messages/{message_id}/react", status_code=202)
def react_to_message(
    message_id: str,
    body: ReactCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Add an emoji reaction (tapback) to a message.

    Needs a transport with Capability.REACTIONS (Slack/Telegram/Discord). Reacting
    to an inbound message tapbacks the human's message; reacting to an outbound one
    reacts to the agent's own. Best-effort and channel-native; a channel with no
    reaction API returns reacted=false rather than erroring the caller.
    """
    target = session.get(Message, message_id)
    if target is None or target.project_id != project.id:
        raise HTTPException(status_code=404, detail="Message not found")
    if not target.provider_message_id:
        raise HTTPException(status_code=400, detail="Message has no provider id to react to")
    connection = session.get(Connection, target.connection_id)
    _require_granted(connection, Capability.REACTIONS)
    provider = request.app.state.providers.get(connection.provider) if connection else None
    reacted = False
    if provider is not None and hasattr(provider, "react"):
        try:
            provider.react(
                target.provider_message_id, body.emoji, read_credentials(connection)
            )
            reacted = True
        except Exception:
            pass  # best-effort; never fail the caller on a reaction
    return {"ok": True, "reacted": reacted}


@router.post("/messages/{message_id}/reply", response_model=MessageOut, status_code=201)
def reply_to_message(
    message_id: str,
    body: ReplyCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    if not body.text and not body.html and not body.blocks and not body.media:
        raise HTTPException(status_code=422, detail="Reply requires text, html, blocks or media")
    target = session.get(Message, message_id)
    if target is None or target.project_id != project.id:
        raise HTTPException(status_code=404, detail="Message not found")
    ensure_credit(request, session, project, target.channel)
    if target.direction != "inbound" or not target.provider_message_id:
        raise HTTPException(status_code=400, detail="Can only reply to an inbound message")
    if target.auto_generated:
        raise HTTPException(
            status_code=400,
            detail="Refusing to reply to an auto-generated message (loop prevention)",
        )
    recent_outbound = session.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == target.conversation_id,
            Message.direction == "outbound",
            Message.created_at > utcnow() - timedelta(hours=24),
        )
    ).scalar_one()
    if recent_outbound >= CONVERSATION_DAILY_REPLY_CAP:
        raise HTTPException(
            status_code=429,
            detail="Conversation reply cap reached for the last 24 hours (loop prevention)",
        )

    connection = session.get(Connection, target.connection_id)
    subject = None
    if target.subject:
        already_reply = target.subject.lower().startswith("re:")
        subject = target.subject if already_reply else f"Re: {target.subject}"
    reply = Message(
        id=new_id("msg"),
        project_id=project.id,
        conversation_id=target.conversation_id,
        connection_id=target.connection_id,
        channel=target.channel,
        direction="outbound",
        status="queued",
        sender_address=connection.address,
        recipients=[{"address": target.sender_address, "name": target.sender_name}],
        subject=subject,
        text=body.text or (blocks_render.to_text(body.blocks) if body.blocks else None),
        html=body.html or (blocks_render.to_html(body.blocks) if body.blocks else None),
        media=body.media or [],
        in_reply_to_id=target.id,
    )
    session.add(reply)
    enqueue(session, "send_reply", {"message_id": reply.id, "blocks": body.blocks})
    session.commit()
    return message_out(reply)


@router.post("/test-emails", response_model=TestEmailOut, status_code=202)
def send_test_email(
    body: TestEmailCreate,
    request: Request,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    """Deliver a test email to one of the project's connections."""
    if body.connection_id:
        connection = session.get(Connection, body.connection_id)
        if connection is None or connection.project_id != project.id:
            raise HTTPException(status_code=404, detail="Connection not found")
    else:
        connection = session.execute(
            select(Connection)
            .where(Connection.project_id == project.id, Connection.status == "active")
            .order_by(Connection.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if connection is None:
            raise HTTPException(status_code=404, detail="No active connection in this project")
    if connection.status != "active":
        raise HTTPException(status_code=400, detail="Connection is not active")

    provider = request.app.state.providers.get(connection.provider)
    if provider is None or not hasattr(provider, "send_test_email"):
        raise HTTPException(
            status_code=422,
            detail=f"Channel {connection.channel!r} does not support test emails",
        )
    injected = provider.send_test_email(
        connection.provider_resource_id, connection.address, body.subject, body.text
    )
    if injected is not None:
        provider_event = ProviderEvent(
            id=new_id("pev"),
            provider=provider.name,
            external_event_id=injected.external_event_id,
            event_type="message.received",
            payload=injected.to_payload(),
        )
        session.add(provider_event)
        enqueue(session, "process_provider_event", {"provider_event_id": provider_event.id})
        session.commit()
    return {"to": connection.address, "status": "delivering"}


@router.get("/events", response_model=list[EventOut])
def list_events(
    after_seq: int = 0,
    limit: int = Query(default=100, ge=1, le=500),
    type: str | None = None,
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
):
    query = select(Event).where(Event.project_id == project.id, Event.seq > after_seq)
    if type:
        query = query.where(Event.type == type)
    rows = session.execute(query.order_by(Event.seq).limit(limit)).scalars()
    return [event_out(e) for e in rows]
