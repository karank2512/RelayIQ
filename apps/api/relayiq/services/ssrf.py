"""SSRF protection for user-supplied callback URLs.

Design notes
------------
* **Port policy**: only 80, 443 and 8000-8999 are accepted. 80/443 are the standard
  HTTP(S) ports, and 8000-8999 covers the common alternate app-server range (uvicorn
  and gunicorn defaults, 8080, 8443, ...). Everything else is rejected to prevent
  cross-protocol SSRF against internal services (22/SSH, 25/SMTP, 5432/Postgres,
  6379/Redis, ...).
* **DNS rebinding**: hostnames are resolved at validation time and *every* resolved
  address must be public, but validate-then-fetch is inherently TOCTOU-racy. The
  fetch layer MUST pin the address it validated (connect to the resolved IP, sending
  the hostname via Host/SNI) or re-run this check against the address it actually
  connects to.
* **IP encodings**: canonical v4/v6 literals (including bracketed v6 and IPv4-mapped
  v6 such as ``::ffff:10.0.0.1``) are parsed with :mod:`ipaddress`. Bare-integer
  hostnames ("2130706433" == 127.0.0.1) are additionally converted via
  ``ipaddress.ip_address(int(...))`` because many HTTP clients accept that form.
  Exotic dotted hex/octal spellings ("0x7f.0.0.1", "0177.0.0.1") do not parse as
  literals and fall through to DNS resolution, where whatever they resolve to is
  vetted like any other hostname.
* **allow_private=True** is for development/test configs only: it skips the hostname
  blocklist, the private-address checks, and DNS resolution entirely so callbacks to
  localhost or docker-network hosts validate. Scheme, userinfo, and port rules still
  apply.

The validator never raises: every outcome is reported as a :class:`UrlCheck`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]
"""Maps a hostname to the list of addresses it resolves to. May raise on failure."""

_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443}) | frozenset(range(8000, 9000))

# Names that must never be dialled regardless of what DNS says about them.
_BLOCKED_HOSTS: frozenset[str] = frozenset({"localhost", "metadata.google.internal"})
_BLOCKED_SUFFIXES: tuple[str, ...] = (".localhost", ".internal", ".local")

# RFC 1035: a full domain name is limited to 253 visible characters.
_MAX_HOSTNAME_LEN = 253

# Cloud metadata endpoint (AWS/GCP/Azure). Link-local already covers it, but the
# address is dangerous enough to warrant an explicit, grep-able rule.
_METADATA_V4 = ipaddress.IPv4Address("169.254.169.254")


@dataclass(frozen=True)
class UrlCheck:
    """Outcome of a callback-URL validation.

    ``reason`` is one of: ``ok``, ``invalid_url``, ``bad_scheme``,
    ``userinfo_not_allowed``, ``bad_port``, ``private_address``,
    ``resolution_failed``, ``host_blocked``.
    """

    ok: bool
    reason: str


def validate_callback_url(
    url: str | None,
    *,
    allow_private: bool = False,
    allowed_schemes: tuple[str, ...] = ("https", "http"),
    resolver: Resolver | None = None,
) -> UrlCheck:
    """Validate a user-supplied callback URL against SSRF. Never raises."""
    try:
        return _validate(url, allow_private=allow_private, allowed_schemes=allowed_schemes, resolver=resolver)
    except Exception:
        # Contract: never raise. Anything unexpected means the URL could not be
        # vetted, so it is rejected.
        return UrlCheck(ok=False, reason="invalid_url")


def _validate(
    url: str | None,
    *,
    allow_private: bool,
    allowed_schemes: tuple[str, ...],
    resolver: Resolver | None,
) -> UrlCheck:
    if url is None or not url.strip():
        return UrlCheck(ok=False, reason="invalid_url")

    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return UrlCheck(ok=False, reason="invalid_url")

    if not parsed.scheme:
        return UrlCheck(ok=False, reason="invalid_url")
    if parsed.scheme not in {scheme.lower() for scheme in allowed_schemes}:
        return UrlCheck(ok=False, reason="bad_scheme")

    # urlsplit().hostname is lowercased and has IPv6 brackets stripped.
    host = parsed.hostname
    if not host:
        return UrlCheck(ok=False, reason="invalid_url")

    # Embedded credentials ("https://user:pass@host/") are a classic parser-confusion
    # vector and have no legitimate use in a callback URL.
    if "@" in parsed.netloc:
        return UrlCheck(ok=False, reason="userinfo_not_allowed")

    try:
        port = parsed.port  # raises ValueError for non-numeric / out-of-range ports
    except ValueError:
        return UrlCheck(ok=False, reason="bad_port")
    if port is not None and port not in _ALLOWED_PORTS:
        return UrlCheck(ok=False, reason="bad_port")

    host = host.rstrip(".")  # "localhost." must match the blocklist like "localhost"
    if not host or len(host) > _MAX_HOSTNAME_LEN:
        return UrlCheck(ok=False, reason="invalid_url")

    ip = _parse_ip_literal(host)
    if ip is not None:
        if not allow_private and _is_forbidden_ip(ip):
            return UrlCheck(ok=False, reason="private_address")
        return UrlCheck(ok=True, reason="ok")

    if allow_private:
        # Dev/test escape hatch: private targets are acceptable, so neither the name
        # blocklist nor DNS resolution buys anything (dev hosts often will not even
        # resolve from the API host).
        return UrlCheck(ok=True, reason="ok")

    if _is_blocked_name(host):
        return UrlCheck(ok=False, reason="host_blocked")

    resolve = resolver if resolver is not None else _default_resolver
    try:
        addresses = resolve(host)
    except Exception:
        return UrlCheck(ok=False, reason="resolution_failed")
    if not addresses:
        return UrlCheck(ok=False, reason="resolution_failed")

    for raw in addresses:
        try:
            resolved = ipaddress.ip_address(raw)
        except ValueError:
            # An address we cannot parse is an address we cannot vouch for.
            return UrlCheck(ok=False, reason="resolution_failed")
        # Conservative DNS-rebinding stance: one bad A/AAAA record poisons the host.
        if _is_forbidden_ip(resolved):
            return UrlCheck(ok=False, reason="private_address")

    return UrlCheck(ok=True, reason="ok")


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the address if ``host`` is an IP literal, else None."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # Bare-integer hosts ("2130706433" == 127.0.0.1) are accepted by many HTTP
    # clients, so they must be evaluated as literals rather than resolved.
    if host.isascii() and host.isdigit():
        try:
            return ipaddress.ip_address(int(host))
        except ValueError:
            return None
    return None


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if the address must not be dialled from the callback dispatcher."""
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:  # e.g. ::ffff:10.0.0.1 is judged as 10.0.0.1
            ip = mapped
    return (
        ip == _METADATA_V4
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_blocked_name(host: str) -> bool:
    return host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES)


def _default_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its A/AAAA addresses via the system resolver."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]
