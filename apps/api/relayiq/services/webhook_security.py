"""Webhook HMAC signature verification (Stripe-style).

Signed payload is ``b"{timestamp}." + raw_body`` and the signature is the hex HMAC-SHA256
digest of that payload keyed with the utf-8 encoded secret. The signature header format is
``t=<unix_ts>,v1=<hex>`` and may carry multiple ``v1`` entries.

Security notes:
- All digest comparisons go through :func:`hmac.compare_digest` (constant-time).
- Every configured secret is tried against every ``v1`` candidate (rotation support) and the
  result never reveals which secret matched.
- Pure functions only: no logging, no side effects, and malformed input never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

DEFAULT_REPLAY_WINDOW_SECONDS = 300
FUTURE_TOLERANCE_SECONDS = 60

_TIMESTAMP_KEY = "t"
_SIGNATURE_KEY = "v1"


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a webhook signature verification.

    ``reason`` is one of: "ok", "missing_signature", "malformed_header",
    "invalid_signature", "stale_timestamp", "future_timestamp".
    """

    ok: bool
    reason: str
    timestamp: int | None


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """Return the hex HMAC-SHA256 digest of ``b"{timestamp}." + body`` keyed with ``secret``."""
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def build_signature_header(secret: str, timestamp: int, body: bytes) -> str:
    """Build a ``t=<unix_ts>,v1=<hex>`` signature header for an outgoing webhook."""
    signature = sign_payload(secret, timestamp, body)
    return f"{_TIMESTAMP_KEY}={timestamp},{_SIGNATURE_KEY}={signature}"


def parse_signature_header(header: str | None) -> tuple[int | None, list[str]]:
    """Parse a signature header into ``(timestamp, v1_candidates)``.

    Tolerant: unknown keys are ignored, whitespace around parts is stripped, the first valid
    ``t`` wins, and garbage input yields ``(None, [])`` — this function never raises.
    """
    if not header:
        return (None, [])

    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == _TIMESTAMP_KEY and timestamp is None:
            try:
                timestamp = int(value)
            except ValueError:
                continue
        elif key == _SIGNATURE_KEY and value:
            candidates.append(value)
    return (timestamp, candidates)


def verify_webhook(
    signature_header: str | None,
    body: bytes,
    secrets: list[str],
    now: int | None = None,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> VerificationResult:
    """Verify a webhook signature header against the raw request body.

    Tries every secret (newest first, for rotation) against every ``v1`` candidate using
    constant-time comparison, without short-circuiting, and never reports which secret matched.
    Timestamps older than ``replay_window_seconds`` or more than ``FUTURE_TOLERANCE_SECONDS``
    in the future are rejected. Always returns a :class:`VerificationResult`; never raises.
    """
    if not signature_header:
        return VerificationResult(ok=False, reason="missing_signature", timestamp=None)

    timestamp, candidates = parse_signature_header(signature_header)
    if timestamp is None or not candidates:
        return VerificationResult(ok=False, reason="malformed_header", timestamp=timestamp)

    current_time = int(time.time()) if now is None else now
    if current_time - timestamp > replay_window_seconds:
        return VerificationResult(ok=False, reason="stale_timestamp", timestamp=timestamp)
    if timestamp - current_time > FUTURE_TOLERANCE_SECONDS:
        return VerificationResult(ok=False, reason="future_timestamp", timestamp=timestamp)

    # Compare every (secret, candidate) pair without short-circuiting so timing reveals
    # neither which secret matched nor which candidate matched.
    matched = False
    for secret in secrets:
        expected = sign_payload(secret, timestamp, body).encode("ascii")
        for candidate in candidates:
            if hmac.compare_digest(expected, candidate.encode("utf-8")):
                matched = True

    if matched:
        return VerificationResult(ok=True, reason="ok", timestamp=timestamp)
    return VerificationResult(ok=False, reason="invalid_signature", timestamp=timestamp)
