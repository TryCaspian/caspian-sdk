from datetime import datetime

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CustomerOut(BaseModel):
    id: str
    name: str
    created_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AgentOut(BaseModel):
    id: str
    name: str
    created_at: datetime


class EmailConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    domain: str | None = None
    username: str | None = None


class DomainCreate(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


class DomainOut(BaseModel):
    id: str
    domain: str
    status: str
    dns_records: list[dict]
    created_at: datetime


class TelegramConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    bot_token: str | None = None


class ChannelConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    # Optional: pick a specific provider when the channel has more than one
    # (e.g. whatsapp -> "twilio-whatsapp" or "meta-whatsapp"). Defaults to the
    # first provider configured for the channel.
    provider: str | None = None
    # Optional branding for shared-app installs (Slack): the icon the agent posts
    # under. display_name above is the posting name.
    icon_url: str | None = None


class DiscordConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    # Identity: a bot token OR a channel webhook URL (with optional custom
    # display name/avatar). At least one is required.
    bot_token: str | None = None
    webhook_url: str | None = None
    username: str | None = None
    avatar_url: str | None = None


class SlackConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    # Bring-your-own Slack app (the developer creates it; we just wire it).
    # Omit all three to use the gateway's shared app, if configured.
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_signing_secret: str | None = None
    # Bring-your-own tokens via Socket Mode: paste an existing app's bot token
    # (xoxb-) + app-level token (xapp-, scope connections:write). No OAuth, no
    # public webhook — the gateway holds a WebSocket to Slack for inbound. Use
    # this to onboard an app that already works (e.g. a Socket Mode app) with no
    # change on the Slack side.
    slack_bot_token: str | None = None
    slack_app_token: str | None = None


class XConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    # The connected X account (already "Automated"-labelled, OAuth-connected)
    # brings its own OAuth 2.0 user access token + numeric user id.
    access_token: str | None = None
    access_secret: str | None = None  # OAuth 1.0a token secret (bring-your-own account)
    user_id: str | None = None
    username: str | None = None  # @screen_name, for display
    
class BlueskyConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    identifier: str
    app_password: str


class LinearConnectionCreate(BaseModel):
    organization_id: str
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    provider: str | None = None
    api_key: str | None = None
    webhook_secret: str | None = None


class ZulipConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    bot_email: str | None = None
    bot_api_key: str | None = None
    bot_token: str | None = None
    server_url: str | None = None


class PhoneConnectionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    # Optional: pick a specific provider when the channel has more than one
    # (e.g. phone -> "twilio" / "telnyx"). Defaults to the first
    # provider configured for the channel.
    provider: str | None = None
    # Bring-your-own SMS credentials (the developer's own CPaaS account + number).
    # Twilio: account_sid + auth_token + from_number. Telnyx: api_key + from_number.
    account_sid: str | None = None
    auth_token: str | None = None
    api_key: str | None = None
    from_number: str | None = None
    messaging_profile_id: str | None = None


class ConnectionBrandingUpdate(BaseModel):
    # Change the name/icon the agent posts under, after connect (Slack: applied on
    # the next message; Discord shared bot: re-sets the per-server nickname).
    display_name: str | None = None
    icon_url: str | None = None


class SandboxProjectCreate(BaseModel):
    name: str | None = None


class SandboxProjectOut(BaseModel):
    project_id: str
    api_key: str


class TestEmailCreate(BaseModel):
    connection_id: str | None = None
    subject: str = "Test email"
    text: str = "Hello from the comm test sender."


class TestEmailOut(BaseModel):
    to: str
    status: str


class ConnectionOut(BaseModel):
    id: str
    channel: str
    capabilities: list[str]
    status: str
    address: str | None
    customer_id: str
    agent_id: str
    error: str | None
    created_at: datetime
    authorize_url: str | None = None



class Participant(BaseModel):
    address: str
    name: str | None = None


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    connection_id: str
    channel: str
    direction: str
    status: str
    sender: Participant | None
    recipients: list[Participant]
    subject: str | None
    text: str | None
    html: str | None
    media: list[dict] = []
    chat_type: str | None = None
    edited: bool = False
    auto_generated: bool = False
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    connection_id: str
    subject: str | None
    created_at: datetime


class ReplyCreate(BaseModel):
    text: str | None = None
    html: str | None = None
    # Provider-neutral rich blocks; rendered natively per channel, flattened to
    # text/html on channels that can't do cards. See providers/blocks.py.
    blocks: list[dict] | None = None
    # File attachments: each {"url"|"data", "mime_type", "name", "size"}.
    media: list[dict] | None = None


class EditCreate(BaseModel):
    text: str


class MessageCreate(BaseModel):
    text: str | None = None
    html: str | None = None
    blocks: list[dict] | None = None
    media: list[dict] | None = None


class InitiateCreate(BaseModel):
    recipient: str = Field(min_length=1)
    text: str | None = Field(default=None, min_length=1)
    blocks: list[dict] | None = None
    media: list[dict] | None = None


class ReactCreate(BaseModel):
    # A single emoji to add to a message (e.g. "👍" or a Slack short name like
    # "thumbsup"). The gateway maps it to each provider's reaction API.
    emoji: str = Field(min_length=1, max_length=64)


class WebhookConfig(BaseModel):
    url: str
    secret: str | None = None


class WebhookOut(BaseModel):
    url: str | None
    configured: bool


class BackfillCreate(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


class EventOut(BaseModel):
    id: str
    seq: int
    type: str
    occurred_at: datetime
    data: dict


class WhatsAppOnboardingSessionCreate(BaseModel):
    customer_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None


class WhatsAppOnboardingSessionOut(BaseModel):
    session: str
    launcher_url: str
    expires_in: int


class EmbeddedSignupIn(BaseModel):
    session: str
    code: str
    phone_number_id: str
    waba_id: str
