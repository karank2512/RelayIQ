"""Application settings. All secrets come from the environment — never hardcode."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    otel_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="relayiq-api", alias="OTEL_SERVICE_NAME")

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
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
