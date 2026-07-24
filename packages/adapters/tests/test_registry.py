"""Provider registry: building, multi-provider config, plugin fallback error."""

import pytest
from caspian_adapters import Settings, build_providers


def test_build_fake_provider_by_default():
    providers = build_providers(Settings(provider="fake"))
    assert "fake" in providers
    assert providers["fake"].channel == "email"


def test_providers_list_builds_each():
    providers = build_providers(
        Settings(
            providers=(
                "fake,fake-telegram,fake-discord,fake-slack,fake-github,fake-instagram"
            )
        )
    )
    assert set(providers) == {"fake", "fake-telegram", "fake-discord", "fake-slack",
                             "fake-github", "fake-instagram"}
    assert providers["fake-instagram"].channel == "instagram"
    assert providers["fake-github"].channel == "github"


def test_instagram_and_facebook_build_from_settings():
    providers = build_providers(Settings(
        providers="instagram,facebook",
        instagram_page_id="123", instagram_access_token="tok",
        instagram_app_secret="sec", instagram_verify_token="vt",
        facebook_page_id="456", facebook_access_token="tok2",
        facebook_app_secret="sec2", facebook_verify_token="vt2",
    ))
    assert providers["instagram"].channel == "instagram"
    assert providers["facebook"].channel == "facebook"


def test_teams_builds_from_settings():
    providers = build_providers(Settings(providers="fake-teams,teams"))
    assert providers["fake-teams"].channel == "teams"
    assert providers["teams"].channel == "teams"


def test_unknown_provider_points_to_plugins_and_hosted():
    with pytest.raises(ValueError) as excinfo:
        build_providers(Settings(providers="some-hosted-channel"))
    message = str(excinfo.value)
    assert "caspian.providers" in message
    assert "hosted" in message.lower()


def test_duplicate_provider_rejected():
    with pytest.raises(ValueError, match="more than once"):
        build_providers(Settings(providers="fake,fake"))


def test_empty_config_rejected():
    with pytest.raises(ValueError, match="No providers configured"):
        build_providers(Settings(provider="", providers=""))
