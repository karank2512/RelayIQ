"""Structured logging (structlog → JSON) with sensitive-field redaction and correlation IDs."""

import logging
import re
from contextvars import ContextVar

import structlog

# Correlation context propagated across the request/worker lifecycle
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)

REDACT_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
    "api_key",
    "apikey",
    "signature",
    "hubspot_access_token",
    "jwt",
}

# Rough patterns for values that should never land in logs verbatim
_EMAIL_RE = re.compile(r"([a-zA-Z0-9_.+-]{2})[a-zA-Z0-9_.+-]*(@[\w.-]+)")


def redact_processor(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in REDACT_KEYS:
            event_dict[key] = "[REDACTED]"
        elif isinstance(event_dict[key], str) and "email" in key.lower():
            event_dict[key] = _EMAIL_RE.sub(r"\1***\2", event_dict[key])
    return event_dict


def add_correlation(_logger, _method, event_dict: dict) -> dict:
    cid = correlation_id_var.get()
    if cid:
        event_dict.setdefault("correlation_id", cid)
    tid = tenant_id_var.get()
    if tid:
        event_dict.setdefault("tenant_id", tid)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            add_correlation,
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "relayiq"):
    return structlog.get_logger(name)
