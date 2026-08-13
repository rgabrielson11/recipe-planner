"""
URL safety checks — used before the server (or Mealie, on our behalf)
fetches a user-supplied URL, to avoid SSRF against internal/LAN services.

Recipe Planner has no auth layer (LAN-only by design — see docker-compose.yml),
so any endpoint that takes a URL and causes a server-side fetch (directly, or
indirectly via the Mealie importer) is reachable by anyone on the network.
Without validation, a client could hand it something like
http://192.168.1.1/admin or http://169.254.169.254/latest/meta-data/ and get
the app (or Mealie, which sits on the same trusted LAN) to fetch it.

This only blocks the obvious cases (non-http(s) schemes, loopback, private /
link-local / reserved IP ranges, and hostnames that resolve to them). It is
not a substitute for real network segmentation, but it closes the easy path.
"""

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the safety check."""


def _is_disallowed_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # can't parse it — treat as unsafe
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    """
    Raises UnsafeUrlError if `url` is not a plain public http(s) URL.

    Checks, in order:
      1. Scheme must be http or https (blocks file:, javascript:, data:, etc.)
      2. Hostname must be present.
      3. Hostname must resolve, and every resolved address must be a public
         (non-private, non-loopback, non-link-local, non-reserved) IP.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise UnsafeUrlError(f"Could not parse URL: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(
            f"URL scheme '{parsed.scheme}' is not allowed — only http:// and https:// URLs are accepted"
        )

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no hostname")

    # If the hostname is itself a literal IP, check it directly.
    try:
        literal_ip = ipaddress.ip_address(hostname)
        if _is_disallowed_ip(str(literal_ip)):
            raise UnsafeUrlError(f"URL points at a non-public IP address ({hostname})")
        return
    except ValueError:
        pass  # not a literal IP — fall through to DNS resolution

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"Could not resolve host '{hostname}': {e}") from e

    resolved_ips = {info[4][0] for info in addr_infos}
    if not resolved_ips:
        raise UnsafeUrlError(f"Host '{hostname}' did not resolve to any address")

    for ip_str in resolved_ips:
        if _is_disallowed_ip(ip_str):
            raise UnsafeUrlError(
                f"Host '{hostname}' resolves to a non-public address ({ip_str}) — refusing to fetch"
            )
