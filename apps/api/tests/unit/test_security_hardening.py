"""Unit tests for the production-hardening layer: config fail-fast validation and the
Redis-backed rate limiter."""

import time

import fakeredis
import pytest

from relayiq.api.routers.webhooks import _resolve_webhook_secrets
from relayiq.config import ProductionConfigError, Settings, validate_production_settings
from relayiq.services.ratelimit import RateLimiter


class _FakeTenant:
    def __init__(self, settings):
        self.settings = settings


class TestWebhookSecretResolution:
    """Fail-closed per-tenant secret selection — a tenant that opts in NEVER falls back
    to the global secret, even when its own list is empty or malformed."""

    GLOBAL = ["GLOBAL_SECRET"]

    def test_no_tenant_uses_global(self):
        assert _resolve_webhook_secrets(None, self.GLOBAL) == self.GLOBAL

    def test_tenant_without_key_uses_global(self):
        assert _resolve_webhook_secrets(_FakeTenant({}), self.GLOBAL) == self.GLOBAL

    def test_tenant_with_real_secrets_ignores_global(self):
        t = _FakeTenant({"webhook_secrets": ["s1", "s2"]})
        assert _resolve_webhook_secrets(t, self.GLOBAL) == ["s1", "s2"]

    def test_opted_in_empty_list_rejects_all_no_global_fallback(self):
        # The critical fail-closed case: explicit [] must NOT downgrade to global.
        t = _FakeTenant({"webhook_secrets": []})
        assert _resolve_webhook_secrets(t, self.GLOBAL) == []

    def test_string_misconfig_is_not_char_split(self):
        t = _FakeTenant({"webhook_secrets": "abc"})
        assert _resolve_webhook_secrets(t, self.GLOBAL) == []  # not ['a','b','c']

    def test_non_string_members_dropped(self):
        t = _FakeTenant({"webhook_secrets": ["good", "", None, 123, "also-good"]})
        assert _resolve_webhook_secrets(t, self.GLOBAL) == ["good", "also-good"]

    def test_null_settings_uses_global(self):
        assert _resolve_webhook_secrets(_FakeTenant(None), self.GLOBAL) == self.GLOBAL

GOOD = dict(
    RELAYIQ_ENV="production",
    RELAYIQ_JWT_SECRET="x" * 48,
    RELAYIQ_WEBHOOK_SECRETS="w" * 48,
    DATABASE_URL="postgresql+psycopg://relayiq:strong-unique-password@db.internal:5432/relayiq",
    RELAYIQ_CORS_ORIGINS="https://app.example.com",
    RELAYIQ_METRICS_TOKEN="m" * 48,
)


def make_settings(**overrides) -> Settings:
    env = {**GOOD, **overrides}
    return Settings(_env_file=None, **{k: v for k, v in env.items()})  # type: ignore[call-arg]


class TestProductionConfigValidation:
    def test_good_config_passes(self):
        validate_production_settings(make_settings())

    def test_dev_jwt_secret_rejected(self):
        s = make_settings(RELAYIQ_JWT_SECRET="dev_only_jwt_secret_do_not_use_in_prod")
        with pytest.raises(ProductionConfigError, match="JWT_SECRET"):
            validate_production_settings(s)

    def test_short_jwt_secret_rejected(self):
        with pytest.raises(ProductionConfigError, match="at least 32"):
            validate_production_settings(make_settings(RELAYIQ_JWT_SECRET="short"))

    def test_dev_webhook_secret_rejected(self):
        s = make_settings(RELAYIQ_WEBHOOK_SECRETS=f"{'w' * 48},dev_only_webhook_secret")
        with pytest.raises(ProductionConfigError, match="WEBHOOK_SECRETS"):
            validate_production_settings(s)

    def test_dev_database_password_rejected(self):
        s = make_settings(
            DATABASE_URL="postgresql+psycopg://relayiq:relayiq_dev_password@db:5432/relayiq"
        )
        with pytest.raises(ProductionConfigError, match="development password"):
            validate_production_settings(s)

    def test_localhost_cors_rejected(self):
        s = make_settings(RELAYIQ_CORS_ORIGINS="http://localhost:5173")
        with pytest.raises(ProductionConfigError, match="localhost"):
            validate_production_settings(s)

    def test_wildcard_cors_rejected(self):
        with pytest.raises(ProductionConfigError, match="wildcard"):
            validate_production_settings(make_settings(RELAYIQ_CORS_ORIGINS="*"))

    def test_public_metrics_rejected(self):
        s = make_settings(RELAYIQ_METRICS_TOKEN="")
        with pytest.raises(ProductionConfigError, match="METRICS_TOKEN"):
            validate_production_settings(s)

    def test_metrics_disabled_is_acceptable(self):
        s = make_settings(RELAYIQ_METRICS_TOKEN="", RELAYIQ_METRICS_ENABLED="false")
        validate_production_settings(s)

    def test_all_problems_reported_at_once(self):
        s = make_settings(
            RELAYIQ_JWT_SECRET="short",
            RELAYIQ_CORS_ORIGINS="*",
            RELAYIQ_METRICS_TOKEN="",
        )
        with pytest.raises(ProductionConfigError) as exc:
            validate_production_settings(s)
        message = str(exc.value)
        assert "JWT_SECRET" in message and "wildcard" in message and "METRICS_TOKEN" in message

    def test_development_env_skips_validation(self):
        # get_settings() only validates in production — dev defaults must keep working.
        s = make_settings(RELAYIQ_ENV="development",
                          RELAYIQ_JWT_SECRET="dev_only_jwt_secret_do_not_use_in_prod")
        assert not s.is_production


class TestRateLimiter:
    def make(self) -> RateLimiter:
        return RateLimiter(client=fakeredis.FakeRedis(decode_responses=True))

    def test_allows_within_limit_blocks_over(self):
        rl = self.make()
        results = [rl.allow("login", "1.2.3.4", limit=3) for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_keys_are_isolated(self):
        rl = self.make()
        for _ in range(3):
            assert rl.allow("login", "1.2.3.4", limit=3)
        assert not rl.allow("login", "1.2.3.4", limit=3)
        assert rl.allow("login", "5.6.7.8", limit=3)  # different client unaffected
        assert rl.allow("webhook", "1.2.3.4", limit=3)  # different scope unaffected

    def test_window_rollover_resets(self, monkeypatch):
        rl = self.make()
        real_now = time.time()
        monkeypatch.setattr(time, "time", lambda: real_now)
        for _ in range(3):
            rl.allow("login", "ip", limit=3, window_seconds=60)
        assert not rl.allow("login", "ip", limit=3, window_seconds=60)
        monkeypatch.setattr(time, "time", lambda: real_now + 61)
        assert rl.allow("login", "ip", limit=3, window_seconds=60)

    def test_zero_limit_disables(self):
        rl = self.make()
        assert all(rl.allow("api", "ip", limit=0) for _ in range(100))

    def test_fails_open_when_redis_down(self):
        import redis as redis_lib

        class Broken:
            def pipeline(self):
                raise redis_lib.ConnectionError("down")

        rl = RateLimiter(client=Broken())  # type: ignore[arg-type]
        assert rl.allow("login", "ip", limit=1)
        assert rl.allow("login", "ip", limit=1)  # still open — availability over strictness
