"""Shared adapter ritual: pack(), verify helpers, thread codec, planned calls.

These tests describe the kit. Channel files only supply parse + plan.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode

from caspian.adapters.pack import from_response, pack
from caspian.adapters.plan import http_form, http_json, smtp, twiml
from caspian.adapters.thread import decode_thread, encode_thread
from caspian.adapters.verify import (
    header_equals,
    hmac_b64,
    hmac_hex,
    hmac_slack,
    twilio_sig,
    unsigned,
)
from caspian.catalog import capabilities_of
from caspian.core.commands import Post
from caspian.core.ports import Connection, RawInbound, Result, Sent
from caspian.core.types import ConnectionId, Message, ThreadId


def _conn(channel: str = "linear", **config: str) -> Connection:
    return Connection(id=ConnectionId("c1"), channel=channel, config=dict(config))


def _empty_parse(_raw: RawInbound) -> Result:
    return Result.ok([])


def _echo_plan(cmd: object, _conn: Connection) -> Result:
    text = getattr(cmd, "text", "")
    return Result.ok(Sent(raw={"text": text, "transport": "noop", "native": "echo"}))


class TestThreadCodec:
    def test_encode_joins_channel_and_parts(self) -> None:
        assert encode_thread("linear", "issue-9") == "linear:issue-9"
        assert encode_thread("slack", "C1", "123.0") == "slack:C1:123.0"
        assert encode_thread("slack", "C1", "") == "slack:C1"

    def test_decode_drops_channel_prefix(self) -> None:
        assert decode_thread(ThreadId("linear:issue-9")) == ("issue-9",)
        assert decode_thread(ThreadId("slack:C1:123.0")) == ("C1", "123.0")


class TestVerifyFailClosed:
    def test_hmac_hex_rejects_missing_secret(self) -> None:
        check = hmac_hex(header="Linear-Signature", secret_key="webhook_secret")
        assert check(RawInbound(body=b"{}"), _conn()) is False

    def test_hmac_hex_accepts_matching_signature(self) -> None:
        body = b'{"type":"Comment"}'
        secret = "whsec"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        check = hmac_hex(header="Linear-Signature", secret_key="webhook_secret")
        good = RawInbound(body=body, headers={"Linear-Signature": sig})
        bad = RawInbound(body=body, headers={"Linear-Signature": "nope"})
        conn = _conn(webhook_secret=secret)
        assert check(good, conn) is True
        assert check(bad, conn) is False

    def test_header_equals_rejects_missing_secret(self) -> None:
        check = header_equals(
            header="X-Telegram-Bot-Api-Secret-Token", secret_key="webhook_secret"
        )
        assert check(RawInbound(body=b"{}"), _conn()) is False

    def test_twilio_sig_rejects_missing_credentials(self) -> None:
        assert twilio_sig(RawInbound(body=b"From=%2B1"), _conn()) is False

    def test_twilio_sig_accepts_valid_form(self) -> None:
        auth_token = "token"
        webhook_url = "https://example.com/sms"
        form = {"From": ["+1"], "Body": ["hi"]}
        body = urlencode(form, doseq=True).encode()
        payload = webhook_url + "BodyhiFrom+1"
        digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode()
        raw = RawInbound(body=body, headers={"X-Twilio-Signature": expected})
        conn = _conn(auth_token=auth_token, webhook_url=webhook_url)
        assert twilio_sig(raw, conn) is True

    def test_hmac_b64_matches_x_style_signature(self) -> None:
        body = b"{}"
        secret = "consumer"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        got = "sha256=" + base64.b64encode(digest).decode()
        check = hmac_b64(
            header="X-Twitter-Webhooks-Signature", secret_key="consumer_secret"
        )
        raw = RawInbound(body=body, headers={"X-Twitter-Webhooks-Signature": got})
        assert check(raw, _conn(consumer_secret=secret)) is True
        assert check(raw, _conn()) is False

    def test_hmac_slack_uses_timestamp_base(self) -> None:
        secret = "s3cr3t"
        body = b'{"type":"event_callback"}'
        timestamp = "123"
        base = f"v0:{timestamp}:{body.decode()}"
        digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        raw = RawInbound(
            body=body,
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": f"v0={digest}",
            },
        )
        assert hmac_slack(raw, _conn(signing_secret=secret)) is True
        assert hmac_slack(raw, _conn()) is False

    def test_unsigned_is_explicit(self) -> None:
        assert unsigned(RawInbound(body=b"{}"), _conn()) is True


class TestPack:
    def test_fills_overlap_capabilities_and_thread_from_catalog(self) -> None:
        Adapter = pack(
            channel="linear",
            parse=_empty_parse,
            plan=_echo_plan,
            verify=unsigned,
        )
        adapter = Adapter()
        event = Message(
            thread_id=ThreadId("linear:issue-9"), text="hi", chat_kind="channel"
        )
        assert adapter.name == "linear"
        assert adapter.capabilities() == capabilities_of("linear")
        assert adapter.overlap_key(event) == "linear:issue-9"
        assert adapter.encode_thread("issue-9") == "linear:issue-9"
        assert adapter.decode_thread(ThreadId("linear:issue-9")) == ("issue-9",)

    def test_execute_applies_format_to_post_text(self) -> None:
        Adapter = pack(
            channel="slack",
            parse=_empty_parse,
            plan=_echo_plan,
            verify=unsigned,
            format=lambda text: text.replace("&", "&amp;"),
        )
        result = Adapter().execute(
            Post(thread_id=ThreadId("slack:C1"), text="a & b"),
            _conn("slack"),
        )
        assert result.is_ok
        assert result.value.raw["text"] == "a &amp; b"

    def test_verify_is_on_the_packed_adapter(self) -> None:
        Adapter = pack(
            channel="linear",
            parse=_empty_parse,
            plan=_echo_plan,
            verify=hmac_hex(header="Linear-Signature", secret_key="webhook_secret"),
        )
        assert Adapter().verify(RawInbound(body=b"{}"), _conn()) is False

    def test_posted_id_reads_the_response_path(self) -> None:
        telegram = from_response("result", "message_id")
        slack = from_response("ts")
        assert (
            telegram(Sent(raw={"response": {"ok": True, "result": {"message_id": 9}}}))
            == "9"
        )
        assert slack(Sent(raw={"response": {"ts": "1.2"}})) == "1.2"
        assert telegram(Sent(raw={"status": 200})) == ""

    def test_packed_posted_id_defaults_empty(self) -> None:
        Adapter = pack(
            channel="linear",
            parse=_empty_parse,
            plan=_echo_plan,
            verify=unsigned,
        )
        assert Adapter().posted_id(Sent(raw={"response": {"id": "x"}})) == ""


class TestPlannedCall:
    def test_http_json_is_a_typed_plan_not_a_free_dict(self) -> None:
        sent = http_json(
            url="https://api.linear.app/graphql",
            json={"query": "q"},
            headers={"Authorization": "k"},
            native="commentCreate",
        )
        assert sent.raw["transport"] == "http_json"
        assert sent.raw["method"] == "POST"
        assert sent.raw["url"] == "https://api.linear.app/graphql"
        assert sent.raw["native"] == "commentCreate"

    def test_other_transports_set_their_kind(self) -> None:
        assert http_form(url="https://x", form={"a": "1"}, native="sms").raw[
            "transport"
        ] == "http_form"
        assert smtp(email={"to": "a@b.c"}, native="sendmail").raw["transport"] == "smtp"
        assert twiml(markup="<Response/>", native="say").raw["transport"] == "twiml"
