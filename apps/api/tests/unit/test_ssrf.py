"""Unit tests for relayiq.services.ssrf (callback-URL SSRF validation).

No network access: every hostname-based case injects a fake resolver, and cases that
must fail before resolution use a resolver that would blow the test up if called.
"""

from __future__ import annotations

import pytest

from relayiq.services.ssrf import UrlCheck, validate_callback_url

PUBLIC_V4 = "93.184.216.34"  # example.com
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def _resolve_public(host: str) -> list[str]:
    return [PUBLIC_V4, PUBLIC_V6]


def _resolve_private(host: str) -> list[str]:
    return ["10.0.0.5"]


def _resolve_mixed(host: str) -> list[str]:
    return [PUBLIC_V4, "10.0.0.5"]  # one poisoned record must reject the host


def _resolve_fail(host: str) -> list[str]:
    raise OSError("name resolution failed")


def _resolve_never(host: str) -> list[str]:
    raise AssertionError(f"resolver must not be called for {host!r}")


class TestSchemesAndStructure:
    def test_https_public_url_ok(self) -> None:
        result = validate_callback_url("https://hooks.example.com/relay", resolver=_resolve_public)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_http_public_url_ok(self) -> None:
        result = validate_callback_url("http://hooks.example.com/relay", resolver=_resolve_public)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_ftp_rejected(self) -> None:
        result = validate_callback_url("ftp://hooks.example.com/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="bad_scheme")

    def test_custom_allowed_schemes_restrict_http(self) -> None:
        result = validate_callback_url(
            "http://hooks.example.com/relay", allowed_schemes=("https",), resolver=_resolve_never
        )
        assert result == UrlCheck(ok=False, reason="bad_scheme")

    @pytest.mark.parametrize("url", [None, "", "   ", "not a url", "hooks.example.com/relay", "https://"])
    def test_structurally_invalid_rejected(self, url: str | None) -> None:
        result = validate_callback_url(url, resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="invalid_url")

    def test_malformed_ipv6_bracket_never_raises(self) -> None:
        result = validate_callback_url("https://[::1", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="invalid_url")

    def test_overlong_hostname_rejected(self) -> None:
        host = ".".join(["a" * 63] * 4) + ".example.com"  # > 253 chars
        result = validate_callback_url(f"https://{host}/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="invalid_url")

    def test_userinfo_rejected(self) -> None:
        result = validate_callback_url("https://user:pass@hooks.example.com/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="userinfo_not_allowed")


class TestPorts:
    def test_port_22_rejected(self) -> None:
        result = validate_callback_url("https://hooks.example.com:22/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="bad_port")

    def test_port_8080_ok(self) -> None:
        result = validate_callback_url("http://hooks.example.com:8080/relay", resolver=_resolve_public)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_port_443_ok(self) -> None:
        result = validate_callback_url("https://hooks.example.com:443/relay", resolver=_resolve_public)
        assert result == UrlCheck(ok=True, reason="ok")

    @pytest.mark.parametrize("port", ["abc", "99999"])
    def test_unparseable_port_rejected(self, port: str) -> None:
        result = validate_callback_url(f"https://hooks.example.com:{port}/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="bad_port")


class TestBlockedHostnames:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost.",  # trailing dot must not bypass the blocklist
            "sub.localhost",
            "vault.internal",
            "printer.local",
            "metadata.google.internal",
        ],
    )
    def test_blocked_names_rejected(self, host: str) -> None:
        result = validate_callback_url(f"https://{host}/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="host_blocked")


class TestIpLiterals:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "[::1]",
            "10.1.2.3",
            "172.16.9.9",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata endpoint
            "169.254.10.10",  # other link-local
            "[::ffff:10.0.0.1]",  # IPv4-mapped IPv6 judged as 10.0.0.1
            "[::]",  # unspecified
            "[fe80::1]",  # v6 link-local
            "2130706433",  # decimal literal for 127.0.0.1
        ],
    )
    def test_private_literals_rejected(self, host: str) -> None:
        result = validate_callback_url(f"https://{host}:8000/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=False, reason="private_address")

    def test_public_v4_literal_ok(self) -> None:
        result = validate_callback_url(f"https://{PUBLIC_V4}/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_public_v6_literal_ok(self) -> None:
        result = validate_callback_url(f"https://[{PUBLIC_V6}]/relay", resolver=_resolve_never)
        assert result == UrlCheck(ok=True, reason="ok")


class TestResolution:
    def test_hostname_resolving_private_rejected(self) -> None:
        result = validate_callback_url("https://cb.partner.example/relay", resolver=_resolve_private)
        assert result == UrlCheck(ok=False, reason="private_address")

    def test_one_private_record_poisons_host(self) -> None:
        result = validate_callback_url("https://cb.partner.example/relay", resolver=_resolve_mixed)
        assert result == UrlCheck(ok=False, reason="private_address")

    def test_hostname_resolving_public_ok(self) -> None:
        result = validate_callback_url("https://cb.partner.example/relay", resolver=_resolve_public)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_resolver_exception_rejected(self) -> None:
        result = validate_callback_url("https://cb.partner.example/relay", resolver=_resolve_fail)
        assert result == UrlCheck(ok=False, reason="resolution_failed")

    def test_resolver_empty_result_rejected(self) -> None:
        result = validate_callback_url("https://cb.partner.example/relay", resolver=lambda host: [])
        assert result == UrlCheck(ok=False, reason="resolution_failed")

    def test_resolver_garbage_address_rejected(self) -> None:
        result = validate_callback_url(
            "https://cb.partner.example/relay", resolver=lambda host: ["not-an-ip"]
        )
        assert result == UrlCheck(ok=False, reason="resolution_failed")


class TestAllowPrivate:
    def test_allow_private_permits_localhost(self) -> None:
        result = validate_callback_url("http://localhost:8000/relay", allow_private=True)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_allow_private_permits_loopback_literal(self) -> None:
        result = validate_callback_url("http://127.0.0.1:8000/relay", allow_private=True)
        assert result == UrlCheck(ok=True, reason="ok")

    def test_allow_private_skips_resolution(self) -> None:
        result = validate_callback_url(
            "http://api.docker-network.example:8000/relay", allow_private=True, resolver=_resolve_never
        )
        assert result == UrlCheck(ok=True, reason="ok")

    def test_allow_private_still_enforces_scheme_and_port(self) -> None:
        bad_scheme = validate_callback_url("ftp://localhost/relay", allow_private=True)
        assert bad_scheme == UrlCheck(ok=False, reason="bad_scheme")
        bad_port = validate_callback_url("http://localhost:22/relay", allow_private=True)
        assert bad_port == UrlCheck(ok=False, reason="bad_port")
