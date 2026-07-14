"""RelayIQ API application factory."""

import hmac
import re
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from relayiq.api.routers import (
    admin,
    auth,
    crm_api,
    enrichment,
    entities,
    metrics_api,
    misc,
    review,
    webhooks,
)
from relayiq.config import get_settings
from relayiq.logging_setup import configure_logging, correlation_id_var, get_logger
from relayiq.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from relayiq.observability.tracing import configure_tracing

log = get_logger("api")

# Client-supplied correlation ids are echoed back — accept a conservative charset only.
_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # HSTS is meaningful only behind TLS; harmless otherwise. Deployment guidance
    # (docs/production-checklist.md) assumes TLS termination at the edge (Fly/ALB/nginx).
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


def _rate_limit_scope(path: str) -> tuple[str, int] | None:
    """(scope, per-minute limit) for paths under explicit limits; None = default limit."""
    settings = get_settings()
    if path.startswith("/v1/auth/login"):
        return ("login", settings.rate_limit_login_per_minute)
    if path.startswith("/v1/webhooks/"):
        return ("webhook", settings.rate_limit_webhook_per_minute)
    return None


def create_app() -> FastAPI:
    settings = get_settings()  # validates production config at startup (fail fast)
    configure_logging(settings.log_level)
    configure_tracing()

    app = FastAPI(
        title="RelayIQ — Enrichment Control Plane",
        version="0.1.0",
        description=(
            "Field-level enrichment routing, cache-first decisions, provider reconciliation, "
            "confidence scoring, human review, CRM sync gating, and a full cost ledger. "
            "Providers are SIMULATED in this build; see docs for live-integration status."
        ),
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url="/redoc" if settings.expose_docs else None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
    )

    @app.middleware("http")
    async def hardening_middleware(request: Request, call_next):
        # Request body cap — reject oversized payloads before they reach handlers.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > settings.max_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"error": {"code": "payload_too_large",
                                           "message": "request body too large"}},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "bad_request", "message": "invalid content-length"}},
                )

        # Rate limiting: strict per-scope limits on login/webhooks, broad default elsewhere.
        from relayiq.services.ratelimit import get_rate_limiter

        client_ip = request.client.host if request.client else "unknown"
        scoped = _rate_limit_scope(request.url.path)
        limiter = get_rate_limiter()
        if scoped is not None:
            scope, limit = scoped
            allowed = limiter.allow(scope, client_ip, limit)
        else:
            allowed = limiter.allow("api", client_ip, settings.rate_limit_api_per_minute)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60", **SECURITY_HEADERS},
                content={"error": {"code": "rate_limited",
                                   "message": "too many requests — retry later"}},
            )

        return await call_next(request)

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Correlation-Id", "")
        correlation_id = supplied if _CORRELATION_ID_RE.match(supplied) else uuid.uuid4().hex
        correlation_id_var.set(correlation_id)
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            # Never leak stack traces to clients; log with correlation id instead.
            log.exception("unhandled error", path=request.url.path)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {"code": "internal_error", "message": "internal server error"},
                    "correlation_id": correlation_id,
                },
            )
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route_path).observe(elapsed)
        response.headers["X-Correlation-Id"] = correlation_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    if settings.metrics_enabled:
        @app.get("/metrics", include_in_schema=False)
        def metrics_endpoint(request: Request) -> Response:
            # When a metrics token is configured, require it (constant-time comparison).
            if settings.metrics_token:
                supplied = request.headers.get("Authorization", "")
                expected = f"Bearer {settings.metrics_token}"
                if not hmac.compare_digest(supplied.encode(), expected.encode()):
                    return JSONResponse(status_code=401, content={"error": {
                        "code": "unauthorized", "message": "metrics token required"}})
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    except Exception:  # pragma: no cover — instrumentation must never block startup
        log.warning("OTel FastAPI instrumentation unavailable")

    for router in (auth.router, enrichment.router, entities.router, review.router,
                   admin.router, metrics_api.router, crm_api.router, webhooks.router,
                   misc.router):
        app.include_router(router)
    return app


app = create_app()
