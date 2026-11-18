"""OpenTelemetry tracing: one span per task run, graph node, step, model call and tool call.

Off unless configured. Set OTEL_EXPORTER_OTLP_ENDPOINT (plus OTEL_EXPORTER_OTLP_HEADERS when
the backend needs auth) to export over OTLP/HTTP to any compatible backend, for example a
local Jaeger (`docker run -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one`) or a hosted
free tier such as Langfuse or Grafana Cloud. OTEL_CONSOLE_EXPORT=true prints spans instead.
Model-call spans follow the OpenTelemetry GenAI semantic conventions (gen_ai.* attributes).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace

from config import get_settings

logger = logging.getLogger(__name__)
TRACER_NAME = "reliable-agent"
_initialized = False


def init_telemetry() -> bool:
    """Install an SDK tracer provider when an exporter is configured; return whether it did."""
    global _initialized
    if _initialized:
        return True
    settings = get_settings()
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not endpoint and not settings.OTEL_CONSOLE_EXPORT:
        return False

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.OTEL_SERVICE_NAME}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if settings.OTEL_CONSOLE_EXPORT:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _initialized = True
    logger.info("telemetry enabled endpoint=%s console=%s", bool(endpoint), settings.OTEL_CONSOLE_EXPORT)
    return True


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)
