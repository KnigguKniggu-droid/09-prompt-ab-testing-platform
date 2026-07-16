"""Tests for the Prometheus metrics module and observability server."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.metrics import (
    HAS_PROMETHEUS,
    export_metrics,
    record_ttft,
    record_inter_token_latency,
    set_token_velocity,
    record_drift_anomaly,
    record_request,
    record_error,
    set_active_streams,
)
from src.server import app, ObservabilityServer, LiveMetricEvent


def test_export_metrics_returns_string():
    result = export_metrics()
    assert isinstance(result, str)
    assert len(result) > 0


def test_record_ttft_does_not_crash():
    record_ttft(model="gpt-4o", vendor="openai", ttft_seconds=0.15)
    record_ttft(model="gpt-4o", vendor="openai", ttft_seconds=0.05)
    record_ttft(model="gpt-4o-mini", vendor="openai", ttft_seconds=0.02)


def test_record_inter_token_latency_does_not_crash():
    record_inter_token_latency(model="gpt-4o", itl_seconds=0.005)
    record_inter_token_latency(model="gpt-4o", itl_seconds=0.012)


def test_set_token_velocity():
    set_token_velocity(model="gpt-4o", tokens_per_second=45.5)
    set_token_velocity(model="gpt-4o-mini", tokens_per_second=120.0)


def test_record_drift_anomaly():
    record_drift_anomaly(model="gpt-4o", drift_direction="over")
    record_drift_anomaly(model="gpt-4o", drift_direction="under")


def test_record_request_and_error():
    record_request(model="gpt-4o", status="success")
    record_request(model="gpt-4o", status="error")
    record_error(model="gpt-4o", error_type="timeout")


def test_set_active_streams():
    set_active_streams(5)
    set_active_streams(0)


def test_prometheus_metrics_contain_expected_names():
    record_ttft(model="test-model", vendor="test-vendor", ttft_seconds=0.1)
    record_inter_token_latency(model="test-model", itl_seconds=0.01)
    set_token_velocity(model="test-model", tokens_per_second=50.0)
    record_drift_anomaly(model="test-model", drift_direction="over")
    record_request(model="test-model", status="success")

    output = export_metrics()
    if HAS_PROMETHEUS:
        assert "llm_time_to_first_token_seconds" in output
        assert "llm_token_generation_velocity_per_second" in output
        assert "llm_semantic_drift_anomalies_total" in output
        assert "llm_inter_token_latency_seconds" in output
        assert "llm_requests_total" in output


@pytest.mark.asyncio
async def test_observability_server_start_and_end_stream():
    server = ObservabilityServer()
    await server.start_stream("req-001", "gpt-4o")
    assert server._active_streams == 1

    ttft = await server.record_first_chunk("req-001", "gpt-4o", "openai")
    assert ttft >= 0.0

    itl = await server.record_chunk("req-001", "gpt-4o", chunk_index=0, token_count=5)
    assert itl == 0.0

    await asyncio.sleep(0.01)

    itl2 = await server.record_chunk("req-001", "gpt-4o", chunk_index=1, token_count=10)
    assert itl2 > 0.0

    await server.end_stream("req-001", "gpt-4o", total_tokens=15, expected_tokens=20)
    assert server._active_streams == 0


@pytest.mark.asyncio
async def test_observability_server_drift_detection():
    server = ObservabilityServer()
    await server.start_stream("req-002", "gpt-4o")
    await server.record_first_chunk("req-002", "gpt-4o", "openai")
    await server.record_chunk("req-002", "gpt-4o", 0, 10)
    await server.end_stream("req-002", "gpt-4o", total_tokens=5, expected_tokens=100)
    assert server._active_streams == 0


@pytest.mark.asyncio
async def test_observability_server_snapshot():
    server = ObservabilityServer()
    await server.start_stream("req-003", "gpt-4o")
    await server.record_first_chunk("req-003", "gpt-4o", "openai")
    await server.record_chunk("req-003", "gpt-4o", 0, 5)
    await asyncio.sleep(0.01)
    await server.record_chunk("req-003", "gpt-4o", 1, 5)
    await server.end_stream("req-003", "gpt-4o", total_tokens=10)

    snapshot = server.get_snapshot()
    assert "ttft" in snapshot
    assert "itl" in snapshot
    assert "active_streams" in snapshot
    assert snapshot["ttft"]["samples"] > 0
    assert snapshot["itl"]["samples"] > 0


@pytest.mark.asyncio
async def test_sse_event_generator_yields_events():
    server = ObservabilityServer()
    await server.start_stream("req-004", "gpt-4o")
    await server.record_first_chunk("req-004", "gpt-4o", "openai")
    await server.end_stream("req-004", "gpt-4o", total_tokens=10)

    gen = server.event_generator()
    first_event = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
    assert first_event.startswith("event: ")
    assert "data: " in first_event


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics_endpoint():
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert isinstance(resp.text, str)


def test_snapshot_endpoint():
    client = TestClient(app)
    resp = client.get("/v1/observability/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "ttft" in data
    assert "itl" in data
    assert "active_streams" in data
