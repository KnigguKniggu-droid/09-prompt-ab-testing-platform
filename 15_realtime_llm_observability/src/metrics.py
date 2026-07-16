"""Prometheus metric indicators for the realtime LLM observability system.

Initializes explicit global Prometheus tracking indicators using the native
prometheus_client Python library. These metrics are scraped by Prometheus
and visualized in Grafana dashboards for enterprise observability.

Metrics defined:
    llm_time_to_first_token_seconds: Histogram tracking TTFT with tight
        millisecond bucket intervals for precise latency analysis.
    llm_token_generation_velocity_per_second: Gauge tracking the current
        token generation rate (tokens per second) per model.
    llm_semantic_drift_anomalies_total: Counter tracking the total number
        of semantic drift anomalies detected when prompt-to-response token
        embeddings fall below acceptable structural validation targets.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


# Custom registry so metrics are isolated to this service
_registry: Any = None
_llm_ttft_histogram: Any = None
_llm_token_velocity_gauge: Any = None
_llm_drift_anomalies_counter: Any = None
_llm_inter_token_latency_histogram: Any = None
_llm_total_request_counter: Any = None
_llm_error_counter: Any = None
_llm_active_streams_gauge: Any = None


def _init_metrics() -> None:
    """Initialize all Prometheus metric objects with the custom registry."""
    global _registry, _llm_ttft_histogram, _llm_token_velocity_gauge
    global _llm_drift_anomalies_counter, _llm_inter_token_latency_histogram
    global _llm_total_request_counter, _llm_error_counter, _llm_active_streams_gauge

    if not HAS_PROMETHEUS:
        return

    if _registry is not None:
        return

    _registry = CollectorRegistry()

    _llm_ttft_histogram = Histogram(
        "llm_time_to_first_token_seconds",
        "Time from request dispatch to first token received, in seconds",
        labelnames=["model", "vendor"],
        buckets=(
            0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25,
            0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0,
        ),
        registry=_registry,
    )

    _llm_token_velocity_gauge = Gauge(
        "llm_token_generation_velocity_per_second",
        "Current token generation velocity in tokens per second",
        labelnames=["model"],
        registry=_registry,
    )

    _llm_drift_anomalies_counter = Counter(
        "llm_semantic_drift_anomalies_total",
        "Total semantic drift anomalies detected when prompt-to-response "
        "token embeddings fall below acceptable structural validation targets",
        labelnames=["model", "drift_direction"],
        registry=_registry,
    )

    _llm_inter_token_latency_histogram = Histogram(
        "llm_inter_token_latency_seconds",
        "Rolling mean gap between subsequent streaming chunk emissions, in seconds",
        labelnames=["model"],
        buckets=(
            0.001, 0.002, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03,
            0.04, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5,
        ),
        registry=_registry,
    )

    _llm_total_request_counter = Counter(
        "llm_requests_total",
        "Total LLM requests processed by the observability proxy",
        labelnames=["model", "status"],
        registry=_registry,
    )

    _llm_error_counter = Counter(
        "llm_errors_total",
        "Total LLM request errors encountered",
        labelnames=["model", "error_type"],
        registry=_registry,
    )

    _llm_active_streams_gauge = Gauge(
        "llm_active_streams",
        "Number of currently active streaming connections",
        registry=_registry,
    )


def get_registry() -> Any:
    """Return the Prometheus collector registry, initializing if needed."""
    if _registry is None:
        _init_metrics()
    return _registry


def record_ttft(model: str, vendor: str, ttft_seconds: float) -> None:
    """Record a Time-To-First-Token observation.

    Args:
        model: The model name (e.g. gpt-4o).
        vendor: The vendor name (e.g. openai).
        ttft_seconds: Time from request dispatch to first token, in seconds.
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_ttft_histogram is None:
        _init_metrics()
    _llm_ttft_histogram.labels(model=model, vendor=vendor).observe(ttft_seconds)


def record_inter_token_latency(model: str, itl_seconds: float) -> None:
    """Record an Inter-Token Latency observation.

    Args:
        model: The model name.
        itl_seconds: Time between subsequent streaming chunks, in seconds.
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_inter_token_latency_histogram is None:
        _init_metrics()
    _llm_inter_token_latency_histogram.labels(model=model).observe(itl_seconds)


def set_token_velocity(model: str, tokens_per_second: float) -> None:
    """Set the current token generation velocity for a model.

    Args:
        model: The model name.
        tokens_per_second: Current generation rate in tokens per second.
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_token_velocity_gauge is None:
        _init_metrics()
    _llm_token_velocity_gauge.labels(model=model).set(tokens_per_second)


def record_drift_anomaly(model: str, drift_direction: str) -> None:
    """Record a semantic drift anomaly.

    Triggered when prompt-to-response token embeddings fall below
    acceptable structural validation targets.

    Args:
        model: The model name.
        drift_direction: "over" if actual exceeds expected, "under" if below.
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_drift_anomalies_counter is None:
        _init_metrics()
    _llm_drift_anomalies_counter.labels(model=model, drift_direction=drift_direction).inc()


def record_request(model: str, status: str) -> None:
    """Record a completed request.

    Args:
        model: The model name.
        status: "success" or "error".
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_total_request_counter is None:
        _init_metrics()
    _llm_total_request_counter.labels(model=model, status=status).inc()


def record_error(model: str, error_type: str) -> None:
    """Record an error encountered during request processing.

    Args:
        model: The model name.
        error_type: Category of error (timeout, api_error, validation, etc.).
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_error_counter is None:
        _init_metrics()
    _llm_error_counter.labels(model=model, error_type=error_type).inc()


def set_active_streams(count: int) -> None:
    """Set the current number of active streaming connections.

    Args:
        count: Number of active streams.
    """
    if not HAS_PROMETHEUS:
        return
    if _llm_active_streams_gauge is None:
        _init_metrics()
    _llm_active_streams_gauge.set(count)


def export_metrics() -> str:
    """Export all Prometheus metrics in text exposition format.

    This string is served at the /metrics endpoint for Prometheus scraping.
    """
    if not HAS_PROMETHEUS:
        return "# prometheus_client not installed\n"
    if _registry is None:
        _init_metrics()
    if _registry is None:
        return "# metric initialization failed\n"
    return generate_latest(_registry).decode("utf-8")
