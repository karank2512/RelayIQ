"""Application settings. All secrets come from the environment — never hardcode.

In production (RELAYIQ_ENV=production) the settings VALIDATE THEMSELVES at import:
the process refuses to boot with dev-default secrets, weak secrets, or a wildcard
CORS policy. See validate_production_settings() and docs/production-checklist.md.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known development placeholders that must never reach production.
_DEV_SECRET_SENTINELS = {
    "dev_only_jwt_secret_do_not_use_in_prod",
    "dev_only_webhook_secret",
    "ci_only_jwt_secret_not_production",
    "ci_only_webhook_secret",
    "CHANGE_ME_generate_a_long_random_value",
    "CHANGE_ME_webhook_secret_v1",
    "CHANGE_ME_webhook_secret_v2",
}
_MIN_SECRET_LENGTH = 32


class ProductionConfigError(RuntimeError):
    """Raised at startup when production is configured with unsafe values."""


# Minimum distinct characters — rejects "aaaa…", "        ", "121212…" style low-entropy keys.
_MIN_DISTINCT_CHARS = 8


def _secret_problems(name: str, value: str) -> list[str]:
    """Reject dev placeholders, short, whitespace-only, and low-entropy secrets.
    The value is stripped first so space-padding cannot mask a short/empty secret."""
    stripped = value.strip()
    if stripped in _DEV_SECRET_SENTINELS or value in _DEV_SECRET_SENTINELS:
        return [f"{name} is a known development placeholder"]
    if len(stripped) < _MIN_SECRET_LENGTH:
        return [f"{name} must be at least {_MIN_SECRET_LENGTH} non-whitespace characters"]
    if len(set(stripped)) < _MIN_DISTINCT_CHARS:
        return [f"{name} is too low-entropy (needs ≥{_MIN_DISTINCT_CHARS} distinct characters)"]
    return []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="development", alias="RELAYIQ_ENV")
    log_level: str = Field(default="INFO", alias="RELAYIQ_LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://relayiq:relayiq_dev_password@localhost:5433/relayiq",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://localhost:6379/2", alias="CELERY_RESULT_BACKEND")

    jwt_secret: str = Field(default="dev_only_jwt_secret_do_not_use_in_prod", alias="RELAYIQ_JWT_SECRET")
    jwt_ttl_seconds: int = Field(default=28800, alias="RELAYIQ_JWT_TTL_SECONDS")

    # Comma-separated, newest first — supports rotation (all secrets are tried on verify).
    webhook_secrets: str = Field(default="dev_only_webhook_secret", alias="RELAYIQ_WEBHOOK_SECRETS")
    webhook_replay_window_seconds: int = Field(default=300, alias="RELAYIQ_WEBHOOK_REPLAY_WINDOW_SECONDS")

    synthetic_world_path: str = Field(default="./data/synthetic_world.json", alias="RELAYIQ_SYNTHETIC_WORLD_PATH")  # noqa: E501
    provider_sim_seed: int = Field(default=42, alias="RELAYIQ_PROVIDER_SIM_SEED")

    hubspot_access_token: str = Field(default="", alias="HUBSPOT_ACCESS_TOKEN")
    hubspot_base_url: str = Field(default="https://api.hubapi.com", alias="HUBSPOT_BASE_URL")

    metrics_enabled: bool = Field(default=True, alias="RELAYIQ_METRICS_ENABLED")
    # When set, GET /metrics requires "Authorization: Bearer <token>". Strongly recommended
    # in production unless the endpoint is protected at the network layer.
    metrics_token: str = Field(default="", alias="RELAYIQ_METRICS_TOKEN")
    otel_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="relayiq-api", alias="OTEL_SERVICE_NAME")

    # HTTP hardening
    # Comma-separated allowed browser origins for CORS (the dashboard's public URL).
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="RELAYIQ_CORS_ORIGINS"
    )
    # Serve /docs and /openapi.json. Safe to keep on (auth still protects the API itself);
    # flip off to reduce surface in locked-down deployments.
    expose_docs: bool = Field(default=True, alias="RELAYIQ_EXPOSE_DOCS")
    max_body_bytes: int = Field(default=2 * 1024 * 1024, alias="RELAYIQ_MAX_BODY_BYTES")

    # When true, trust X-Forwarded-For for the client IP (rate limiting). ONLY enable when
    # the app sits behind a trusted proxy that sets it — otherwise clients spoof their IP.
    trust_forwarded_for: bool = Field(default=False, alias="RELAYIQ_TRUST_FORWARDED_FOR")

    # Rate limiting (Redis-backed fixed windows; 0 disables a limiter)
    rate_limit_login_per_minute: int = Field(default=5, alias="RELAYIQ_RATE_LIMIT_LOGIN_PER_MINUTE")
    rate_limit_webhook_per_minute: int = Field(default=120, alias="RELAYIQ_RATE_LIMIT_WEBHOOK_PER_MINUTE")
    rate_limit_api_per_minute: int = Field(default=600, alias="RELAYIQ_RATE_LIMIT_API_PER_MINUTE")

    # Cache
    cache_schema_version: str = "v1"
    cache_default_ttl_seconds: int = 6 * 3600
    cache_negative_ttl_seconds: int = 15 * 60
    cache_lock_ttl_seconds: int = 10

    # Idempotency
    idempotency_ttl_hours: int = 48

    # SSRF protection for callback URLs
    callback_allowed_schemes: tuple[str, ...] = ("https", "http")
    callback_block_private_networks: bool = True

    # Confidence / acceptance
    default_min_confidence: float = 0.6

    # Usable-lead definition (docs/benchmarks/metric-definitions.md) — configurable so
    # cost-per-usable-lead can be re-derived under different definitions.
    usable_lead_require_company: bool = True
    usable_lead_require_valid_domain: bool = True
    usable_lead_require_contact_name: bool = True
    usable_lead_require_title_or_seniority: bool = True
    usable_lead_min_confidence: float = 0.6

    @property
    def webhook_secret_list(self) -> list[str]:
        return [s.strip() for s in self.webhook_secrets.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def validate_production_settings(settings: Settings) -> None:
    """Refuse to boot production with unsafe configuration (fail fast, fail loud)."""
    problems: list[str] = []

    problems.extend(_secret_problems("RELAYIQ_JWT_SECRET", settings.jwt_secret))

    if not settings.webhook_secret_list:
        problems.append("RELAYIQ_WEBHOOK_SECRETS must be set")
    for secret in settings.webhook_secret_list:
        webhook_problems = _secret_problems("a webhook secret", secret)
        if webhook_problems:
            problems.extend(webhook_problems)
            break

    if "relayiq_dev_password" in settings.database_url:
        problems.append("DATABASE_URL still uses the development password")

    origins = [o.lower() for o in settings.cors_origin_list]
    if "*" in origins:
        problems.append("RELAYIQ_CORS_ORIGINS must not be a wildcard in production")
    _loopback_hosts = ("localhost", "127.", "[::1]", "0.0.0.0")  # noqa: S104 — reject list, not a bind
    if any(
        any(f"//{h}" in o for h in _loopback_hosts)
        for o in origins
    ):
        problems.append(
            "RELAYIQ_CORS_ORIGINS still points at a loopback host — set the dashboard's public URL"
        )

    if settings.metrics_enabled and not settings.metrics_token:
        problems.append(
            "set RELAYIQ_METRICS_TOKEN (or RELAYIQ_METRICS_ENABLED=false) so /metrics is not public"
        )

    if problems:
        bullet = "\n  - ".join(problems)
        raise ProductionConfigError(
            f"Refusing to start with unsafe production configuration:\n  - {bullet}\n"
            "Generate secrets with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "See docs/production-checklist.md."
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production:
        validate_production_settings(settings)
    return settings
