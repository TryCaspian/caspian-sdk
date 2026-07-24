from importlib.metadata import entry_points

from .base import ChannelProvider
from .config import Settings

# Extra providers register a factory under this entry-point group. The entry
# point's name is the provider name; the factory is called with Settings and
# returns a ChannelProvider.
PLUGIN_GROUP = "caspian.providers"

# Channels that exist in the Caspian API but have no open-source provider.
# Self-hosted deployments get a clear error instead of a silent unknown-name.
HOSTED_ONLY = "phone/SMS, WhatsApp, RCS, iMessage, FaceTime, and voice"


def _build_one(name: str, settings: Settings) -> ChannelProvider:
    if name == "fake":
        from .fake import FakeEmailProvider

        return FakeEmailProvider()
    if name == "fake-telegram":
        from .fake_telegram import FakeTelegramProvider

        return FakeTelegramProvider()
    if name == "ses":
        from .ses import SESEmailProvider

        return SESEmailProvider(
            region=settings.ses_region,
            domain=settings.ses_domain,
            s3_bucket=settings.ses_s3_bucket,
            topic_arn=settings.ses_topic_arn,
            verify_sns=settings.ses_verify_sns,
            rule_set=settings.ses_rule_set,
            rule_name=settings.ses_rule_name,
        )
    if name == "telegram":
        from .telegram import TelegramProvider

        return TelegramProvider(
            webhook_base=settings.telegram_webhook_base,
            base_url=settings.telegram_base_url,
        )

    if name == "zulip":
        from .zulip import ZulipProvider

        return ZulipProvider(
            base_url=settings.zulip_base_url,
        )

    if name == "fake-telegram-user":
        from .fake_telegram_user import FakeTelegramUserProvider

        return FakeTelegramUserProvider()
    if name == "telegram-user":
        from .telegram_user import TelegramUserProvider

        return TelegramUserProvider(
            session=settings.telegram_user_session,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
    if name == "fake-modem":
        from .fake_modem import FakeModemProvider

        return FakeModemProvider()
    if name == "gsm-modem":
        from .modem import GsmModemProvider

        return GsmModemProvider(
            serial_port=settings.modem_serial_port,
            msisdn=settings.modem_msisdn,
        )
    if name == "google-meet":
        import json as _json
        import pathlib as _pathlib

        from .gmeet import GoogleMeetProvider

        raw = settings.gmeet_sa_json.strip()
        # A path to the key file, or the inline JSON itself.
        if raw and not raw.lstrip().startswith("{"):
            raw = _pathlib.Path(raw).read_text()
        sa_info = _json.loads(raw) if raw else {}
        return GoogleMeetProvider(
            sa_info=sa_info,
            impersonate=settings.gmeet_impersonate,
            join_url=settings.gmeet_join_url,
            webhook_secret=settings.gmeet_webhook_secret,
        )
    if name == "fake-discord":
        from .fake_social import FakeDiscordProvider

        return FakeDiscordProvider()
    if name == "discord":
        from .discord import DiscordProvider

        return DiscordProvider(
            base_url=settings.discord_base_url,
            shared_bot_token=settings.discord_bot_token,
        )
    if name == "fake-slack":
        from .fake_social import FakeSlackProvider

        return FakeSlackProvider()
    if name == "slack":
        import json as _json

        from .slack import SlackProvider

        pool = _json.loads(settings.slack_apps) if settings.slack_apps.strip() else None
        return SlackProvider(
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret,
            signing_secret=settings.slack_signing_secret,
            scopes=settings.slack_scopes,
            apps=pool,
        )
    if name == "x":
        from .x import XProvider

        return XProvider(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            access_token=settings.x_access_token,
            access_secret=settings.x_access_secret,
            user_id=settings.x_user_id,
            webhook_secret=settings.x_webhook_secret,
            base_url=settings.x_base_url,
        )
    if name == "fake-instagram":
        from .fake_social import FakeInstagramProvider

        return FakeInstagramProvider()
    if name == "instagram":
        from .messenger import InstagramProvider

        return InstagramProvider(
            page_id=settings.instagram_page_id,
            access_token=settings.instagram_access_token,
            app_secret=settings.instagram_app_secret,
            verify_token=settings.instagram_verify_token,
            base_url=f"https://graph.facebook.com/{settings.graph_version}",
        )
    if name == "fake-facebook":
        from .fake_social import FakeFacebookProvider

        return FakeFacebookProvider()
    if name == "facebook":
        from .messenger import FacebookProvider

        return FacebookProvider(
            page_id=settings.facebook_page_id,
            access_token=settings.facebook_access_token,
            app_secret=settings.facebook_app_secret,
            verify_token=settings.facebook_verify_token,
            base_url=f"https://graph.facebook.com/{settings.graph_version}",
        )
    plugin = _build_plugin(name, settings)
    if plugin is not None:
        return plugin
    raise ValueError(
        f"Unknown provider: {name!r}. Providers for {HOSTED_ONLY} are available "
        f"on Caspian hosted (https://trycaspianai.com), or install a package "
        f"that registers {name!r} under the {PLUGIN_GROUP!r} entry-point group."
    )


def _build_plugin(name: str, settings: Settings) -> ChannelProvider | None:
    for ep in entry_points(group=PLUGIN_GROUP):
        if ep.name == name:
            factory = ep.load()
            return factory(settings)
    return None


def build_providers(settings: Settings) -> dict[str, ChannelProvider]:
    """Build every configured provider, keyed by provider name.

    COMM_PROVIDERS is a comma-separated list; it falls back to the original
    single COMM_PROVIDER setting so existing deployments keep working.
    """
    names = [n.strip() for n in (settings.providers or settings.provider).split(",") if n.strip()]
    providers: dict[str, ChannelProvider] = {}
    for name in names:
        provider = _build_one(name, settings)
        # Multiple providers MAY serve one channel. They're keyed by unique
        # provider name; each connection stores its own provider, inbound routes
        # by the webhook URL's provider name, and outbound resolves by
        # connection.provider - so they never cross. The connect call picks
        # which one (see _provider_for_channel).
        if provider.name in providers:
            raise ValueError(f"Provider {provider.name!r} configured more than once")
        providers[provider.name] = provider
    if not providers:
        raise ValueError("No providers configured (set COMM_PROVIDERS or COMM_PROVIDER)")
    return providers
