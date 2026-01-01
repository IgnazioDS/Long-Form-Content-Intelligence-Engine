from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from packages.shared_db.settings import settings

logger = logging.getLogger(__name__)
_TRACING_INITIALIZED = False


def init_tracing_if_enabled(app: FastAPI) -> None:
    global _TRACING_INITIALIZED
    if _TRACING_INITIALIZED:
        return
    if not settings.otel_enabled and not settings.otel_exporter_otlp_endpoint:
        return

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    else:
        exporter = OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    RequestsInstrumentor().instrument()
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    _TRACING_INITIALIZED = True
    logger.info("tracing_initialized", extra={"event": "tracing_initialized"})
