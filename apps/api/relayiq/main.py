"""RelayIQ API application factory."""

import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from relayiq.api.routers import admin, auth, crm_api, enrichment, entities, metrics_api, misc, review, webhooks
from relayiq.config import get_settings
from relayiq.logging_setup import configure_logging, correlation_id_var, get_logger
from relayiq.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from relayiq.observability.tracing import configure_tracing

log = get_logger("api")


def create_app() -> FastAPI:
    settings = get_settings()
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
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
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
        return response

    if settings.metrics_enabled:
        @app.get("/metrics", include_in_schema=False)
        def metrics_endpoint() -> Response:
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
