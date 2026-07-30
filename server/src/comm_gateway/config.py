from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMM_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./comm.db"
    provider: str = "fake"
    providers: str = ""  # comma-separated; overrides `provider` when set
    bootstrap_api_key: str = "comm_test_replace_me"
    inline_worker: bool = True
    credentials_key: str = ""  # Fernet key for at-rest credential encryption
    # SSM SecureString param holding the Fernet key (keeps it out of .env)
    credentials_key_ssm_param: str = ""
    credentials_key_ssm_region: str = "ap-south-1"
    host: str = "127.0.0.1"
    port: int = 8000

    telegram_webhook_base: str = ""
    telegram_base_url: str = "https://api.telegram.org"
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

    telnyx_api_key: str = ""
    telnyx_from_number: str = ""
    telnyx_messaging_profile_id: str = ""
    telnyx_public_key: str = ""
    telnyx_base_url: str = "https://api.telnyx.com"

    # Managed-phone upstream (SIM-backed numbers). Bring your own compatible
    # endpoint + key; blank base_url disables the channel. No vendor hardcoded.
    caspian_phone_api_key: str = ""
    caspian_phone_webhook_secret: str = ""
    caspian_phone_base_url: str = ""

    # GitHub App credentials. The private key may be an inline PEM or a path to a
    # .pem file; comments on issues/PRs that @-mention the App become messages.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: str = ""
    github_webhook_secret: str = ""
    github_api_base: str = "https://api.github.com"

    teams_messaging_endpoint: str = ""
    # Accept unauthenticated Bot Framework Emulator traffic (channelId
    # "emulator", localhost serviceUrl). Local development only.
    teams_allow_emulator: bool = False
    teams_connector_base_url: str = "https://smba.trafficmanager.net/amer"
    teams_token_url: str = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    teams_openid_config_url: str = "https://login.botframework.com/v1/.well-known/openidconfiguration"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_whatsapp_from: str = "+14155238886"  # Twilio WhatsApp sandbox by default
    # Caspian-owned Twilio WhatsApp sender numbers (E.164, comma-separated), all
    # under the one Twilio account. When set, each agent is handed its own number
    # from this pool (Option 1a); empty ⇒ everyone shares twilio_whatsapp_from.
    twilio_whatsapp_pool: str = ""
    twilio_rcs_messaging_service_sid: str = ""
    meta_wa_phone_number_id: str = ""
    meta_wa_access_token: str = ""
    meta_wa_app_secret: str = ""
    meta_wa_verify_token: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_es_config_id: str = ""  # Embedded Signup configuration id
    meta_graph_version: str = "v21.0"
    twilio_provision_country: str = "US"
    twilio_provision_area_code: str = ""
    twilio_inbound_webhook_url: str = ""

    # Voice (phone calls) on Twilio Voice. Reuses twilio_account_sid/auth_token.
    voice_from_number: str = ""  # shared voice line; blank -> commission per agent
    voice_conversationrelay_url: str = ""  # external realtime-AI WebSocket the call connects to
    voice_inbound_webhook_url: str = ""  # URL Twilio POSTs inbound calls to (for signature verify)

    modem_serial_port: str = ""
    modem_msisdn: str = ""

    # Self-hosted iMessage via a BlueBubbles server on a Mac mini (channel
    # "imessage"; deployment owns one Mac mini / Apple ID, so this is config, not
    # per-connection credentials). macmini_webhook_secret is opt-in: verification
    # is enforced only when it is set.
    macmini_bluebubbles_url: str = ""
    macmini_bluebubbles_password: str = ""
    macmini_imessage_handle: str = ""
    macmini_webhook_secret: str = ""

    discord_base_url: str = "https://discord.com/api/v10"
    # Shared "Caspian" Discord bot for one-click install (OAuth). Developers add
    # this ONE bot to their server; messages route by guild_id to their agent.
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

    instagram_page_id: str = ""
    instagram_access_token: str = ""
    instagram_app_secret: str = ""
    instagram_verify_token: str = ""
    facebook_page_id: str = ""
    facebook_access_token: str = ""
    facebook_app_secret: str = ""
    facebook_verify_token: str = ""

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
    # Bluesky deployment settings. Connected accounts provide their own
    # identifier and app password; these settings configure the shared API
    # endpoint and optional webhook verification secret.
    bluesky_base_url: str = "https://bsky.social"
    bluesky_webhook_secret: str = ""
    # Stripe (pay-as-you-go credit; live keys live in SSM SecureString, resolved
    # at startup — only the parameter NAMES sit in .env).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_secret_key_ssm_param: str = ""
    stripe_webhook_secret_ssm_param: str = ""
    stripe_base_url: str = "https://api.stripe.com"
    billing_currency: str = "usd"
    # Where Stripe checkout returns and where developers add credit. Blank by
    # default so self-host never points users at someone else's dashboard; set
    # COMM_BILLING_* to your own pages if you enable billing.
    billing_success_url: str = ""
    billing_cancel_url: str = ""
    billing_low_balance_cents: int = 100  # emit billing.low_balance below this
    billing_dashboard_url: str = ""

    public_base_url: str = ""  # https base for OAuth redirects, e.g. https://api.example.com

    # Dashboard: the developer dashboard authenticates with Supabase (Google) and
    # calls GET /v1/usage. The gateway validates the Supabase access token against
    # this project and maps the signed-in email to a Caspian project.
    supabase_url: str = ""  # https://<ref>.supabase.co
    supabase_anon_key: str = ""  # to call Supabase /auth/v1/user for token validation
    dashboard_links: str = ""  # JSON {email: project_id} — demo mapping until self-serve onboarding

    # Optional, opt-out product telemetry (PostHog). Off unless you set a key.
    # Self-host gets zero telemetry by default; point it at your own PostHog if
    # you want it. Metadata only, never message content. Set COMM_TELEMETRY=false
    # (or leave the key blank) to disable entirely.
    telemetry: bool = True
    posthog_key: str = ""  # phc_... write-only project key; blank = disabled
    posthog_host: str = "https://us.i.posthog.com"