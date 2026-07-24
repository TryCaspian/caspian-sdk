from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment-level adapter configuration (env vars prefixed COMM_).

    Only channel-adapter knobs live here. Per-connection credentials (a bot
    token, a page token) are supplied by the caller at connect time.
    """

    model_config = SettingsConfigDict(env_prefix="COMM_", env_file=".env", extra="ignore")

    provider: str = "fake"
    providers: str = ""  # comma-separated; overrides `provider` when set

    telegram_webhook_base: str = ""
    telegram_base_url: str = "https://api.telegram.org"
    
    zulip_base_url: str = "https://zulip.com/api/v1"

    telegram_user_session: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    ses_region: str = "ap-south-1"
    ses_domain: str = ""
    ses_s3_bucket: str = ""
    ses_topic_arn: str = ""
    ses_verify_sns: bool = True
    ses_rule_set: str = ""
    ses_rule_name: str = ""

    # Google Meet (channel "gmeet"). A service account with domain-wide delegation
    # impersonates a Workspace user, since Meet spaces are user-owned. gmeet_sa_json
    # is a PATH to the service-account key file, or the inline JSON itself.
    gmeet_sa_json: str = ""  # path to the SA key file, or inline JSON
    gmeet_impersonate: str = ""  # Workspace user email to act as
    gmeet_join_url: str = ""  # external realtime endpoint that joins the meeting as the persona
    gmeet_webhook_secret: str = ""  # optional shared secret on Workspace Events pushes

    modem_serial_port: str = ""
    modem_msisdn: str = ""

    discord_base_url: str = "https://discord.com/api/v10"
    # Shared Discord bot for one-click install (OAuth). Developers add this ONE
    # bot to their server; messages route by guild_id to their agent.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_bot_token: str = ""
    # Permissions integer for the invite: View Channels (1024) + Send Messages
    # (2048) + Read Message History (65536) + Change Nickname (67108864, lets the
    # bot show a per-developer name in each server) = 67177472.
    discord_bot_permissions: str = "67177472"

    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    slack_scopes: str = (
        "chat:write,chat:write.customize,channels:history,im:history,app_mentions:read"
    )
    # Pool of shared Slack apps (for the coalition case: two developers' agents in
    # ONE workspace need distinct apps, since Slack allows one install of an app
    # per workspace). JSON list of {app_id, client_id, client_secret,
    # signing_secret}. If empty, falls back to the single slack_* app above.
    slack_apps: str = ""

    # Instagram DM / Facebook Messenger via the developer's own Meta app + Page
    # (Graph API). Page id + access token identify the Page the agent answers as.
    instagram_page_id: str = ""
    instagram_access_token: str = ""
    instagram_app_secret: str = ""
    instagram_verify_token: str = ""
    facebook_page_id: str = ""
    facebook_access_token: str = ""
    facebook_app_secret: str = ""
    facebook_verify_token: str = ""
    graph_version: str = "v21.0"

    # X (Twitter) app credentials. Reactive-DM + post only; the connected
    # account brings its own OAuth user access token + user id at connect time.
    x_api_key: str = ""
    x_api_secret: str = ""  # consumer secret; verifies webhooks + signs CRC
    x_bearer_token: str = ""  # app-only bearer (optional; not used for user actions)
    x_access_token: str = ""  # deployment fallback account's OAuth user token
    x_access_secret: str = ""  # OAuth 1.0a fallback secret (optional)
    x_user_id: str = ""  # deployment fallback account's numeric user id
    x_webhook_secret: str = ""  # overrides x_api_secret for CRC/signature if set
    x_base_url: str = "https://api.x.com"
    x_dm_poll_interval: float = 10.0  # seconds between DM polls per connection
