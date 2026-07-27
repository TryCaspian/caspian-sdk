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
