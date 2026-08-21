"""Stdlib HTTP front for cx.handle. Not part of the public SDK."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from caspian import Caspian
from caspian.core.ports import Result


def challenge_response(
    query: dict[str, list[str]],
    *,
    verify_token: str,
) -> tuple[bytes, int, str]:
    mode = (query.get("hub.mode") or [""])[0]
    token = (query.get("hub.verify_token") or [""])[0]
    challenge = (query.get("hub.challenge") or [""])[0]
    if mode == "subscribe" and verify_token and token == verify_token:
        return challenge.encode(), 200, "text/plain"
    return b"", 403, "text/plain"


def crc_response(
    token: str,
    *,
    consumer_secret: str,
) -> tuple[bytes, int, str]:
    if not consumer_secret:
        return b"", 403, "text/plain"
    digest = hmac.new(consumer_secret.encode(), token.encode(), hashlib.sha256).digest()
    payload = json.dumps(
        {"response_token": "sha256=" + base64.b64encode(digest).decode()}
    )
    return payload.encode(), 200, "application/json"


def _twiml_of(results: list[Result]) -> str:
    for result in results:
        if result.is_ok and isinstance(result.value.raw, dict):
            markup = result.value.raw.get("twiml")
            if isinstance(markup, str) and markup:
                return markup
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def serve(
    cx: Caspian,
    channel: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    verify_token: str = "",
    twiml: bool = False,
    consumer_secret: str = "",
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            crc_token = (query.get("crc_token") or [""])[0]
            if crc_token:
                body, status, content_type = crc_response(
                    crc_token, consumer_secret=consumer_secret
                )
            else:
                body, status, content_type = challenge_response(
                    query, verify_token=verify_token
                )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            headers = {k: v for k, v in self.headers.items()}
            results = cx.handle(channel, raw, headers)
            for result in results:
                if not result.is_ok:
                    print(result.error, flush=True)
            out = _twiml_of(results).encode() if twiml else b""
            self.send_response(200)
            if twiml:
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            if out:
                self.wfile.write(out)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            print(format % args, flush=True)

    HTTPServer((host, port), Handler).serve_forever()
