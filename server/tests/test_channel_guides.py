"""Per-channel behaviour guides: single-channel endpoint + project-scoped combined."""


def test_single_channel_guide(client):
    r = client.get("/v1/channels/slack/guide")
    assert r.status_code == 200
    assert "Slack" in r.text and "thread" in r.text.lower()
    assert client.get("/v1/channels/nope/guide").status_code == 404


def test_behavior_prompt_reflects_connected_channels(client, run_jobs):
    # Nothing connected yet -> empty (safe to append unconditionally).
    assert client.get("/v1/behavior-prompt").text == ""

    # Connect email -> active -> the combined prompt includes the email guide.
    client.post("/v1/connections/email", json={})
    run_jobs()
    body = client.get("/v1/behavior-prompt").text
    assert "How to reply on each channel" in body
    assert "## Email" in body
    # A channel the project did NOT connect is not included.
    assert "## Slack" not in body


def test_setup_guides_teach_the_current_sdk_api():
    """Setup guides are served to coding agents in the `setup` field of
    GET /v1/channels, so stale 0.6.x text makes them write code that cannot
    work. Asserted against the source dict, not the endpoint, so the guard
    holds regardless of which providers a given deployment enables."""
    from comm_gateway.channel_guides import _SETUP

    assert _SETUP, "expected at least one channel setup guide"
    for channel, text in _SETUP.items():
        for stale in (
            "caspian_sdk",
            "CommClient",
            "client.connect_",
            "client.create_customer",
            "client.create_agent",
            "client.listen(",
            "message.reply(",
        ):
            assert stale not in text, f"stale 0.6.x API in {channel} setup guide: {stale}"
        assert "from caspian import Caspian" in text, channel
        assert "cx.channels.add(" in text, channel
