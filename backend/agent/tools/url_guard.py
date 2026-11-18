"""Outbound URL checks for the api_call tool (server-side request forgery guard)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class BlockedURLError(ValueError):
    """Raised when a URL points at a non-public address or uses a disallowed scheme."""


def check_public_url(url: str) -> str:
    """Return the URL if it is http(s) and every address its host resolves to is public.

    Blocks loopback, private, link-local (cloud metadata), multicast, reserved and
    unspecified ranges, for IPv4 and IPv6. The check resolves DNS once, so a host that
    changes its answer between this check and the request (DNS rebinding) is not covered;
    redirects are not followed by the caller for the same reason.
    """
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in {"http", "https"}:
        raise BlockedURLError(f"Scheme not allowed: {parts.scheme or 'none'}")
    if not parts.hostname:
        raise BlockedURLError("URL has no host")
    if parts.username or parts.password:
        raise BlockedURLError("Credentials in URLs are not allowed")

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Host does not resolve: {parts.hostname}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            raise BlockedURLError(f"Address not allowed: {address}")
    return url
