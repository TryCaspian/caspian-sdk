"""Network-safety helpers.

- ``client_ip``: the real client IP behind Cloudflare (CF rewrites the socket
  peer to an edge IP, so trust ``CF-Connecting-IP``).
- SSRF guard: reject webhook URLs that resolve to non-public addresses so a
  developer (or an anonymous sandbox key) can't make the gateway POST to
  ``169.254.169.254`` / ``localhost`` / internal ranges.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """Best-effort real client IP.

    Behind Cloudflare ``request.client.host`` is a CF edge IP, so per-IP limits
    would collapse to a single bucket. Cloudflare sets ``CF-Connecting-IP`` and
    overwrites any client-supplied value, so prefer it; then the first
    ``X-Forwarded-For`` hop; then the socket peer.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolves_to_private(host: str) -> bool:
    """True if the host resolves to any non-public address. A host that doesn't
    resolve is NOT flagged (delivery just fails harmlessly - it's not an SSRF
    vector), which also keeps offline/test hostnames working."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def is_public_url(url: str) -> bool:
    """Non-raising check for the worker: is this URL safe to POST to?"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return not _resolves_to_private(parsed.hostname)


def assert_public_url(url: str) -> None:
    """Request-path guard: 422 if the URL isn't a public http(s) endpoint."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="webhook url must be a valid http(s) URL")
    if _resolves_to_private(parsed.hostname):
        raise HTTPException(
            status_code=422, detail="webhook url must resolve to a public address"
        )
