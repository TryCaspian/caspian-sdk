from ..config import Settings
from .base import ChannelProvider


def _build_one(name: str, settings: Settings) -> ChannelProvider:
    if name == "fake":
        from .fakes.fake import FakeEmailProvider

        return FakeEmailProvider()
    if name == "fake-telegram":
        from .fakes.fake_telegram import FakeTelegramProvider

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
    if name == "fake-telegram-user":
        from .fakes.fake_telegram_user import FakeTelegramUserProvider

        return FakeTelegramUserProvider()
    if name == "telegram-user":
        from .telegram_user import TelegramUserProvider

        return TelegramUserProvider(
            session=settings.telegram_user_session,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
    if name == "fake-phone":
        from .fakes.fake_phone import FakePhoneProvider

        return FakePhoneProvider()
    if name == "telnyx":
        from .phone import TelnyxPhoneProvider

        return TelnyxPhoneProvider(
            api_key=settings.telnyx_api_key,
            from_number=settings.telnyx_from_number,
            messaging_profile_id=settings.telnyx_messaging_profile_id,
            public_key=settings.telnyx_public_key,
            base_url=settings.telnyx_base_url,
        )
    if name == "fake-modem":
        from .fakes.fake_modem import FakeModemProvider

        return FakeModemProvider()
    if name == "gsm-modem":
        from .modem import GsmModemProvider

        return GsmModemProvider(
            serial_port=settings.modem_serial_port,
            msisdn=settings.modem_msisdn,
        )
    if name == "fake-caspian-phone":
        from .fakes.fake_caspian_phone import FakeCaspianPhoneProvider

        return FakeCaspianPhoneProvider()
    if name == "caspian-phone":
        from .caspian_phone import CaspianPhoneProvider

        return CaspianPhoneProvider(
            api_key=settings.caspian_phone_api_key,
            webhook_secret=settings.caspian_phone_webhook_secret,
            base_url=settings.caspian_phone_base_url,
        )
    if name == "twilio":
        from .twilio_phone import TwilioPhoneProvider

        return TwilioPhoneProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
            provision_country=settings.twilio_provision_country,
            provision_area_code=settings.twilio_provision_area_code,
            inbound_webhook_url=settings.twilio_inbound_webhook_url,
            verify_url=settings.twilio_inbound_webhook_url,
        )
    if name == "twilio-voice":
        from .voice import VoiceProvider

        return VoiceProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.voice_from_number,
            conversationrelay_url=settings.voice_conversationrelay_url,
            provision_country=settings.twilio_provision_country,
            provision_area_code=settings.twilio_provision_area_code,
            inbound_webhook_url=settings.voice_inbound_webhook_url,
            verify_url=settings.voice_inbound_webhook_url,
        )
    if name == "fake-whatsapp":
        from .fakes.fake_channels import FakeWhatsAppProvider

        return FakeWhatsAppProvider()
    if name == "twilio-whatsapp":
        from .twilio_whatsapp import TwilioWhatsAppProvider

        return TwilioWhatsAppProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_whatsapp_from,
            pool=settings.twilio_whatsapp_pool,
            verify_url=settings.twilio_inbound_webhook_url,
        )
    if name == "meta-whatsapp":
        from .meta_whatsapp import MetaWhatsAppProvider

        return MetaWhatsAppProvider(
            app_secret=settings.meta_app_secret or settings.meta_wa_app_secret,
            verify_token=settings.meta_wa_verify_token,
            phone_number_id=settings.meta_wa_phone_number_id,
            access_token=settings.meta_wa_access_token,
            base_url=f"https://graph.facebook.com/{settings.meta_graph_version}",
        )
    if name == "fake-rcs":
        from .fakes.fake_channels import FakeRcsProvider

        return FakeRcsProvider()
    if name == "twilio-rcs":
        from .twilio_rcs import TwilioRcsProvider

        return TwilioRcsProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            messaging_service_sid=settings.twilio_rcs_messaging_service_sid,
            verify_url=settings.twilio_inbound_webhook_url,
        )
    if name == "fake-caspian-imessage":
        from .fakes.fake_channels import FakeCaspianPhoneIMessageProvider

        return FakeCaspianPhoneIMessageProvider()
    if name == "caspian-imessage":
        from .caspian_phone_imessage import CaspianPhoneIMessageProvider

        return CaspianPhoneIMessageProvider(
            api_key=settings.caspian_phone_api_key,
            webhook_secret=settings.caspian_phone_webhook_secret,
            base_url=settings.caspian_phone_base_url,
        )
    if name == "macmini-imessage":
        from .macmini_imessage import MacMiniIMessageProvider

        return MacMiniIMessageProvider(
            base_url=settings.macmini_bluebubbles_url,
            password=settings.macmini_bluebubbles_password,
            handle=settings.macmini_imessage_handle,
            webhook_secret=settings.macmini_webhook_secret,
        )
    if name == "fake-zulip":
        from .fakes.fake_zulip import FakeZulipProvider

        return FakeZulipProvider()
    if name == "zulip":
        from .zulip import ZulipProvider

        return ZulipProvider(
            webhook_base=settings.zulip_webhook_base,
        )
    if name == "fake-bluesky":
        from .fakes.fake_social import FakeBlueskyProvider

        return FakeBlueskyProvider()
    if name == "bluesky":
        from .bluesky import BlueskyProvider

        return BlueskyProvider(
            base_url=settings.bluesky_base_url,
            webhook_secret=settings.bluesky_webhook_secret,
        )
    if name == "fake-discord":
        from .fakes.fake_social import FakeDiscordProvider

        return FakeDiscordProvider()
    if name == "discord":
        from .discord import DiscordProvider

        return DiscordProvider(
            base_url=settings.discord_base_url,
            shared_bot_token=settings.discord_bot_token,
        )
    if name == "fake-slack":
        from .fakes.fake_social import FakeSlackProvider

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
    if name == "fake-instagram":
        from .fakes.fake_social import FakeInstagramProvider

        return FakeInstagramProvider()
    if name == "instagram":
        from .meta_messaging import InstagramProvider

        return InstagramProvider(
            page_id=settings.instagram_page_id,
            access_token=settings.instagram_access_token,
            app_secret=settings.instagram_app_secret,
            verify_token=settings.instagram_verify_token,
            base_url=f"https://graph.facebook.com/{settings.meta_graph_version}",
        )
    if name == "fake-facebook":
        from .fakes.fake_social import FakeFacebookProvider

        return FakeFacebookProvider()
    if name == "facebook":
        from .meta_messaging import FacebookProvider

        return FacebookProvider(
            page_id=settings.facebook_page_id,
            access_token=settings.facebook_access_token,
            app_secret=settings.facebook_app_secret,
            verify_token=settings.facebook_verify_token,
            base_url=f"https://graph.facebook.com/{settings.meta_graph_version}",
        )
    if name == "fake-github":
        from .fakes.fake_github import FakeGitHubProvider

        return FakeGitHubProvider()
    if name == "github":
        from .github import GitHubProvider

        return GitHubProvider(
            app_id=settings.github_app_id,
            app_slug=settings.github_app_slug,
            private_key=settings.github_private_key,
            webhook_secret=settings.github_webhook_secret,
            base_url=settings.github_api_base,
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
    if name == "fake-linear":
        from .fakes.fake_linear import FakeLinearProvider

        return FakeLinearProvider()
    if name == "linear":
        from .linear import LinearProvider

        return LinearProvider(
            client_id=settings.linear_client_id,
            client_secret=settings.linear_client_secret,
            webhook_secret=settings.linear_webhook_secret,
            base_url=settings.linear_base_url,
        )
    # Plugin providers: third-party or private channel packages can register
    # their own builders under the "caspian.providers" entry-point group, so a
    # new channel can be added without forking. Each builder has the signature
    # build(name, settings).
    from importlib.metadata import entry_points

    for ep in entry_points(group="caspian.providers"):
        if ep.name == name:
            return ep.load()(name, settings)
    raise ValueError(f"Unknown provider: {name}")


def build_providers(settings: Settings) -> dict[str, ChannelProvider]:
    """Build every configured provider, keyed by provider name.

    COMM_PROVIDERS is a comma-separated list; it falls back to the original
    single COMM_PROVIDER setting so existing deployments keep working.
    """
    names = [n.strip() for n in (settings.providers or settings.provider).split(",") if n.strip()]
    providers: dict[str, ChannelProvider] = {}
    for name in names:
        provider = _build_one(name, settings)
        # Multiple providers MAY serve one channel (e.g. twilio-whatsapp +
        # meta-whatsapp). They're keyed by unique provider name; each connection
        # stores its own provider, inbound routes by the webhook URL's provider
        # name, and outbound resolves by connection.provider - so they never
        # cross. The connect call picks which one (see _provider_for_channel).
        if provider.name in providers:
            raise ValueError(f"Provider {provider.name!r} configured more than once")
        providers[provider.name] = provider
    if not providers:
        raise ValueError("No providers configured (set COMM_PROVIDERS or COMM_PROVIDER)")
    return providers
