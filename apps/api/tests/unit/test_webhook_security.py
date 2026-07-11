"""Unit tests for relayiq.services.webhook_security (Stripe-style HMAC verification)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from relayiq.services import webhook_security as ws
from relayiq.services.webhook_security import (
    VerificationResult,
    build_signature_header,
    parse_signature_header,
    sign_payload,
    verify_webhook,
)

SECRET = "whsec_test_primary"
OLD_SECRET = "whsec_test_rotated_out"
NOW = 1_752_192_000  # fixed reference clock for deterministic tests
BODY = b'{"event":"contact.updated","id":"c_123"}'


def _header(secret: str, timestamp: int, body: bytes) -> str:
    return build_signature_header(secret, timestamp, body)


class TestSignPayload:
    def test_matches_reference_hmac(self) -> None:
        expected = hmac.new(
            SECRET.encode("utf-8"), f"{NOW}.".encode() + BODY, hashlib.sha256
        ).hexdigest()
        assert sign_payload(SECRET, NOW, BODY) == expected

    def test_is_hex_sha256_digest(self) -> None:
        digest = sign_payload(SECRET, NOW, BODY)
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")

    def test_different_timestamp_changes_signature(self) -> None:
        assert sign_payload(SECRET, NOW, BODY) != sign_payload(SECRET, NOW + 1, BODY)


class TestBuildAndParseHeader:
    def test_round_trip(self) -> None:
        header = build_signature_header(SECRET, NOW, BODY)
        timestamp, candidates = parse_signature_header(header)
        assert timestamp == NOW
        assert candidates == [sign_payload(SECRET, NOW, BODY)]

    def test_header_format(self) -> None:
        header = build_signature_header(SECRET, NOW, BODY)
        assert header == f"t={NOW},v1={sign_payload(SECRET, NOW, BODY)}"

    @pytest.mark.parametrize(
        "garbage",
        ["", "garbage", "t,v1", "=,=", "t=abc", "t=abc,v1=", ",,,", "t==,v1", "🙂"],
    )
    def test_garbage_returns_none_and_empty(self, garbage: str) -> None:
        assert parse_signature_header(garbage) == (None, [])

    def test_none_returns_none_and_empty(self) -> None:
        assert parse_signature_header(None) == (None, [])

    def test_multiple_v1_entries_collected(self) -> None:
        timestamp, candidates = parse_signature_header(f"t={NOW},v1=aaa,v1=bbb")
        assert timestamp == NOW
        assert candidates == ["aaa", "bbb"]

    def test_whitespace_and_unknown_keys_tolerated(self) -> None:
        timestamp, candidates = parse_signature_header(f" t = {NOW} , v0=zzz , v1 = abc ")
        assert timestamp == NOW
        assert candidates == ["abc"]

    def test_first_valid_timestamp_wins(self) -> None:
        timestamp, _ = parse_signature_header(f"t={NOW},t={NOW + 999},v1=abc")
        assert timestamp == NOW


class TestVerifyWebhook:
    def test_valid_signature(self) -> None:
        result = verify_webhook(_header(SECRET, NOW, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=NOW)

    def test_invalid_signature(self) -> None:
        header = f"t={NOW},v1={'0' * 64}"
        result = verify_webhook(header, BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="invalid_signature", timestamp=NOW)

    def test_modified_body_after_signing(self) -> None:
        header = _header(SECRET, NOW, BODY)
        tampered = BODY.replace(b"c_123", b"c_666")
        result = verify_webhook(header, tampered, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="invalid_signature", timestamp=NOW)

    def test_wrong_secret_is_invalid_signature(self) -> None:
        header = _header(SECRET, NOW, BODY)
        result = verify_webhook(header, BODY, ["some_other_secret"], now=NOW)
        assert not result.ok
        assert result.reason == "invalid_signature"

    def test_missing_header(self) -> None:
        result = verify_webhook(None, BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="missing_signature", timestamp=None)

    def test_empty_header_is_missing(self) -> None:
        result = verify_webhook("", BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="missing_signature", timestamp=None)

    @pytest.mark.parametrize(
        "header",
        ["garbage", f"t={NOW}", "v1=abc", "t=notanint,v1=abc", f"t={NOW},v1="],
    )
    def test_malformed_header(self, header: str) -> None:
        result = verify_webhook(header, BODY, [SECRET], now=NOW)
        assert not result.ok
        assert result.reason == "malformed_header"

    def test_malformed_header_never_raises_on_unicode_signature(self) -> None:
        header = f"t={NOW},v1=🙂🙂🙂"
        result = verify_webhook(header, BODY, [SECRET], now=NOW)
        assert not result.ok
        assert result.reason == "invalid_signature"

    def test_stale_timestamp_outside_window(self) -> None:
        ts = NOW - 301
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="stale_timestamp", timestamp=ts)

    def test_future_timestamp(self) -> None:
        ts = NOW + 61
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="future_timestamp", timestamp=ts)

    def test_duplicate_v1_entries_second_matches(self) -> None:
        good = sign_payload(SECRET, NOW, BODY)
        header = f"t={NOW},v1={'f' * 64},v1={good}"
        result = verify_webhook(header, BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=NOW)

    def test_secret_rotation_old_secret_still_verifies(self) -> None:
        header = _header(OLD_SECRET, NOW, BODY)
        result = verify_webhook(header, BODY, [SECRET, OLD_SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=NOW)

    def test_result_never_leaks_which_secret_matched(self) -> None:
        via_new = verify_webhook(_header(SECRET, NOW, BODY), BODY, [SECRET, OLD_SECRET], now=NOW)
        via_old = verify_webhook(_header(OLD_SECRET, NOW, BODY), BODY, [SECRET, OLD_SECRET], now=NOW)
        assert via_new == via_old

    def test_empty_secrets_list_is_invalid_signature(self) -> None:
        result = verify_webhook(_header(SECRET, NOW, BODY), BODY, [], now=NOW)
        assert not result.ok
        assert result.reason == "invalid_signature"

    def test_unicode_body_bytes_round_trip(self) -> None:
        body = '{"name":"Zoë 🚀 – naïve façade"}'.encode()
        result = verify_webhook(_header(SECRET, NOW, body), body, [SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=NOW)

    def test_unicode_body_tamper_detected(self) -> None:
        body = '{"name":"Zoë 🚀"}'.encode()
        tampered = '{"name":"Zoë 🚀!"}'.encode()
        result = verify_webhook(_header(SECRET, NOW, body), tampered, [SECRET], now=NOW)
        assert not result.ok
        assert result.reason == "invalid_signature"

    def test_now_defaults_to_wall_clock(self) -> None:
        ts = int(time.time())
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET])
        assert result.ok
        assert result.reason == "ok"


class TestConstantTimeComparison:
    @pytest.fixture
    def compare_digest_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[bytes, bytes]]:
        calls: list[tuple[bytes, bytes]] = []
        real_compare_digest = hmac.compare_digest

        def recording_compare_digest(a: bytes, b: bytes) -> bool:
            calls.append((a, b))
            return real_compare_digest(a, b)

        monkeypatch.setattr(hmac, "compare_digest", recording_compare_digest)
        return calls

    def test_valid_verify_goes_through_compare_digest(
        self, compare_digest_calls: list[tuple[bytes, bytes]]
    ) -> None:
        result = verify_webhook(_header(SECRET, NOW, BODY), BODY, [SECRET], now=NOW)
        assert result.ok
        assert len(compare_digest_calls) >= 1

    def test_invalid_verify_goes_through_compare_digest(
        self, compare_digest_calls: list[tuple[bytes, bytes]]
    ) -> None:
        result = verify_webhook(f"t={NOW},v1={'0' * 64}", BODY, [SECRET], now=NOW)
        assert not result.ok
        assert len(compare_digest_calls) >= 1

    def test_all_secret_candidate_pairs_compared_without_short_circuit(
        self, compare_digest_calls: list[tuple[bytes, bytes]]
    ) -> None:
        good = sign_payload(SECRET, NOW, BODY)  # matches the FIRST secret and FIRST candidate
        header = f"t={NOW},v1={good},v1={'a' * 64}"
        result = verify_webhook(header, BODY, [SECRET, OLD_SECRET], now=NOW)
        assert result.ok
        assert len(compare_digest_calls) == 4  # 2 secrets x 2 candidates, no early exit

    def test_length_mismatch_still_uses_compare_digest(
        self, compare_digest_calls: list[tuple[bytes, bytes]]
    ) -> None:
        result = verify_webhook(f"t={NOW},v1=abc", BODY, [SECRET], now=NOW)
        assert not result.ok
        assert result.reason == "invalid_signature"
        assert len(compare_digest_calls) == 1

    def test_module_does_not_use_naive_equality_for_digests(self) -> None:
        import inspect

        source = inspect.getsource(ws.verify_webhook)
        assert "compare_digest" in source
        assert "expected ==" not in source
        assert "== candidate" not in source


class TestReplayWindowBoundaries:
    def test_exactly_at_replay_window_edge_is_accepted(self) -> None:
        ts = NOW - 300
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=ts)

    def test_one_second_past_replay_window_is_stale(self) -> None:
        ts = NOW - 301
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="stale_timestamp", timestamp=ts)

    def test_exactly_at_future_tolerance_edge_is_accepted(self) -> None:
        ts = NOW + 60
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=True, reason="ok", timestamp=ts)

    def test_one_second_past_future_tolerance_is_rejected(self) -> None:
        ts = NOW + 61
        result = verify_webhook(_header(SECRET, ts, BODY), BODY, [SECRET], now=NOW)
        assert result == VerificationResult(ok=False, reason="future_timestamp", timestamp=ts)

    def test_custom_replay_window_respected(self) -> None:
        header = _header(SECRET, NOW - 10, BODY)
        ok = verify_webhook(header, BODY, [SECRET], now=NOW, replay_window_seconds=10)
        stale = verify_webhook(header, BODY, [SECRET], now=NOW, replay_window_seconds=9)
        assert ok.reason == "ok"
        assert stale.reason == "stale_timestamp"

    def test_stale_check_beats_signature_check(self) -> None:
        # A stale timestamp with a garbage signature reports staleness, not invalid_signature.
        header = f"t={NOW - 301},v1={'0' * 64}"
        result = verify_webhook(header, BODY, [SECRET], now=NOW)
        assert result.reason == "stale_timestamp"
