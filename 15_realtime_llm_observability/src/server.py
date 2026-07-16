"""High-throughput async FastAPI streaming observability proxy server.

Constructs a proxy endpoint (POST /v1/observability/stream) that accepts
standard chat completion streaming requests, forwards them to the upstream
gateway layer, catches incoming token stream data chunks asynchronously,
and accurately logs Time-To-First-Token (TTFT) and Inter-Token Latency (ITL).

Also provides an SSE Broadcast Event Stream loop (GET /v1/observability/dashboard/events)
that continuously yields structured JSON with live performance analytics
without polling or blocking active model execution channels.

Prometheus metrics are exposed at /metrics for Grafana scraping.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.metrics import (
    export_metrics,
    record_error,
    record_inter_token_latency,
    record_request,
    record_ttft,
    record_drift_anomaly,
    set_active_streams,
    set_token_velocity,
)

UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "https://api.openai.com/v1")
UPSTREAM_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
SSE_UPDATE_INTERVAL = float(os.environ.get("SSE_UPDATE_INTERVAL", "1.0"))


class ChatCompletionRequest(BaseModel):
    """Standard OpenAI-compatible chat completion request."""

    model: str = "gpt-4o"
    messages: list[dict[str, str]] = Field(..., min_length=1)
    temperature: float = 0.7
    max_tokens: int = 1000
    stream: bool = True
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    expected_tokens: int = Field(0, description="Expected output token count for drift detection")


class LiveMetricEvent(BaseModel):
    """A single live metric event pushed via SSE to dashboard clients."""

    event_type: str
    model: str
    request_id: str = ""
    ttft_ms: float = 0.0
    itl_ms: float = 0.0
    token_velocity: float = 0.0
    total_tokens: int = 0
    drift_anomaly: bool = False
    drift_direction: str = ""
    timestamp: float = Field(default_factory=lambda: time.time())


class ObservabilityServer:
    """Core observability server with streaming metric collection.

    Maintains in-memory rolling buffers for TTFT, ITL, token velocity,
    and drift anomalies. These buffers feed both the Prometheus metrics
    and the SSE event stream for the real-time dashboard.
    """

    def __init__(self) -> None:
        self._ttft_buffer: list[float] = []
        self._itl_buffer: list[float] = []
        self._token_counts: dict[str, int] = {}
        self._stream_timings: dict[str, dict[str, float]] = {}
        self._stream_chunks: dict[str, list[float]] = {}
        self._active_streams: int = 0
        self._event_queue: asyncio.Queue[LiveMetricEvent] = asyncio.Queue(maxsize=1000)
        self._lock = asyncio.Lock()
        self._max_buffer = 5000

    async def start_stream(self, request_id: str, model: str) -> None:
        """Record the start of a streaming inference call."""
        async with self._lock:
            self._stream_timings[request_id] = {
                "start": time.monotonic(),
                "first_chunk": 0.0,
                "end": 0.0,
                "model": 0.0,
            }
            self._stream_chunks[request_id] = []
            self._token_counts[request_id] = 0
            self._active_streams += 1
            set_active_streams(self._active_streams)

    async def record_first_chunk(self, request_id: str, model: str, vendor: str = "openai") -> float:
        """Record the first chunk arrival and compute TTFT.

        Returns the TTFT in milliseconds. Only records on the first chunk;
        subsequent calls for the same request_id are no-ops.
        """
        async with self._lock:
            timings = self._stream_timings.get(request_id)
            if timings is None or timings.get("first_chunk", 0) > 0:
                return 0.0

            now = time.monotonic()
            timings["first_chunk"] = now
            start = timings.get("start", now)
            ttft_seconds = now - start
            ttft_ms = ttft_seconds * 1000

            self._ttft_buffer.append(ttft_ms)
            if len(self._ttft_buffer) > self._max_buffer:
                self._ttft_buffer = self._ttft_buffer[-self._max_buffer:]

            record_ttft(model=model, vendor=vendor, ttft_seconds=ttft_seconds)

            await self._push_event(LiveMetricEvent(
                event_type="ttft",
                model=model,
                request_id=request_id,
                ttft_ms=ttft_ms,
            ))

            return ttft_ms

    async def record_chunk(self, request_id: str, model: str, chunk_index: int, token_count: int = 0) -> float:
        """Record a streaming chunk and compute Inter-Token Latency.

        Returns the ITL in milliseconds for this chunk, or 0.0 if this
        is the first chunk (no previous chunk to compare against).
        """
        now = time.monotonic()
        itl_ms = 0.0

        async with self._lock:
            self._stream_chunks.setdefault(request_id, []).append(now)

            if chunk_index > 0 and len(self._stream_chunks[request_id]) > 1:
                prev_time = self._stream_chunks[request_id][-2]
                itl_seconds = now - prev_time
                itl_ms = itl_seconds * 1000

                self._itl_buffer.append(itl_ms)
                if len(self._itl_buffer) > self._max_buffer:
                    self._itl_buffer = self._itl_buffer[-self._max_buffer:]

                record_inter_token_latency(model=model, itl_seconds=itl_seconds)

            self._token_counts[request_id] = self._token_counts.get(request_id, 0) + token_count

        return itl_ms

    async def end_stream(
        self,
        request_id: str,
        model: str,
        total_tokens: int,
        expected_tokens: int = 0,
        success: bool = True,
    ) -> None:
        """Record the end of a streaming inference call."""
        async with self._lock:
            timings = self._stream_timings.get(request_id, {})
            now = time.monotonic()
            timings["end"] = now
            start = timings.get("start", now)
            first = timings.get("first_chunk", now)
            self._active_streams = max(0, self._active_streams - 1)
            set_active_streams(self._active_streams)

            total_duration = now - start
            stream_duration = now - first if first > 0 else 0.0

            if total_tokens > 0 and stream_duration > 0:
                velocity = total_tokens / stream_duration
                set_token_velocity(model=model, tokens_per_second=velocity)

                await self._push_event(LiveMetricEvent(
                    event_type="stream_end",
                    model=model,
                    request_id=request_id,
                    token_velocity=velocity,
                    total_tokens=total_tokens,
                ))

            if expected_tokens > 0 and total_tokens > 0:
                drift_ratio = total_tokens / expected_tokens
                if abs(drift_ratio - 1.0) > 0.2:
                    direction = "over" if drift_ratio > 1.0 else "under"
                    record_drift_anomaly(model=model, drift_direction=direction)
                    await self._push_event(LiveMetricEvent(
                        event_type="drift_anomaly",
                        model=model,
                        request_id=request_id,
                        drift_anomaly=True,
                        drift_direction=direction,
                        total_tokens=total_tokens,
                    ))

            record_request(model=model, status="success" if success else "error")

            self._stream_timings.pop(request_id, None)
            self._stream_chunks.pop(request_id, None)
            self._token_counts.pop(request_id, None)

    async def record_stream_error(self, request_id: str, model: str, error_type: str) -> None:
        """Record a streaming error."""
        async with self._lock:
            self._active_streams = max(0, self._active_streams - 1)
            set_active_streams(self._active_streams)
            self._stream_timings.pop(request_id, None)
            self._stream_chunks.pop(request_id, None)

        record_error(model=model, error_type=error_type)
        record_request(model=model, status="error")

        await self._push_event(LiveMetricEvent(
            event_type="error",
            model=model,
            request_id=request_id,
        ))

    async def _push_event(self, event: LiveMetricEvent) -> None:
        """Push a live metric event to the SSE queue."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    def get_snapshot(self) -> dict[str, Any]:
        """Get a current snapshot of all metrics for the dashboard."""
        ttft_vals = self._ttft_buffer[-500:]
        itl_vals = self._itl_buffer[-500:]

        def percentile(vals: list[float], p: float) -> float:
            if not vals:
                return 0.0
            sorted_vals = sorted(vals)
            idx = int(len(sorted_vals) * p / 100.0)
            idx = min(idx, len(sorted_vals) - 1)
            return sorted_vals[idx]

        return {
            "active_streams": self._active_streams,
            "ttft": {
                "p50_ms": percentile(ttft_vals, 50),
                "p95_ms": percentile(ttft_vals, 95),
                "p99_ms": percentile(ttft_vals, 99),
                "mean_ms": sum(ttft_vals) / len(ttft_vals) if ttft_vals else 0.0,
                "samples": len(ttft_vals),
            },
            "itl": {
                "p50_ms": percentile(itl_vals, 50),
                "p95_ms": percentile(itl_vals, 95),
                "p99_ms": percentile(itl_vals, 99),
                "mean_ms": sum(itl_vals) / len(itl_vals) if itl_vals else 0.0,
                "samples": len(itl_vals),
            },
        }

    async def event_generator(self) -> AsyncGenerator[str, None]:
        """Async generator that yields SSE events continuously.

        This generator yields structured JSON events containing live
        performance analytics. It merges real-time events from the queue
        with periodic snapshot updates, ensuring the dashboard updates
        seamlessly without polling or blocking active model execution.
        """
        while True:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=SSE_UPDATE_INTERVAL,
                )
                yield self._format_sse(event.model_dump(), event.event_type)
            except asyncio.TimeoutError:
                snapshot = self.get_snapshot()
                yield self._format_sse(snapshot, "snapshot")

    @staticmethod
    def _format_sse(data: dict[str, Any], event_name: str) -> str:
        """Format data as an SSE message string."""
        return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_server = ObservabilityServer()

app = FastAPI(
    title="Realtime LLM Observability Server",
    description="Streaming TTFT/ITL tracking, semantic drift detection, and SSE dashboard",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/v1/observability/stream")
async def observability_stream(request: ChatCompletionRequest) -> StreamingResponse:
    """Proxy endpoint that forwards chat completions and tracks streaming metrics.

    Accepts standard chat completion streaming requests, forwards them to
    the upstream gateway layer (or directly to OpenAI if no gateway is
    configured), and intercepts the streaming response to log:

    - Time-To-First-Token (TTFT): The absolute time delta between initial
      socket dispatch and the arrival of the very first data packet chunk.
    - Inter-Token Latency (ITL): The rolling mean gap between subsequent
      streaming chunk emissions.

    The response is streamed back to the client unchanged, with metrics
    collected in the background.
    """
    request_id = str(uuid.uuid4())
    model = request.model

    await _server.start_stream(request_id, model)

    upstream_target = GATEWAY_URL or UPSTREAM_URL
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if UPSTREAM_API_KEY and not GATEWAY_URL:
        headers["Authorization"] = f"Bearer {UPSTREAM_API_KEY}"

    payload: dict[str, Any] = {
        "model": request.model,
        "messages": request.messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": True,
        "top_p": request.top_p,
        "frequency_penalty": request.frequency_penalty,
        "presence_penalty": request.presence_penalty,
    }

    async def stream_with_metrics() -> AsyncGenerator[str, None]:
        """Generator that proxies the upstream stream while collecting metrics."""
        chunk_index = 0
        total_tokens = 0
        had_error = False

        try:
            async with _httpx_client() as client:
                async with client.stream(
                    "POST",
                    f"{upstream_target}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            yield line + "\n"
                            continue

                        data_str = line[6:]

                        if data_str.strip() == "[DONE]":
                            yield line + "\n"
                            break

                        try:
                            chunk_data = json.loads(data_str)
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            chunk_tokens = len(delta.get("content", "")) // 4

                            if chunk_index == 0:
                                await _server.record_first_chunk(
                                    request_id, model,
                                    vendor=_get_vendor(model),
                                )

                            itl = await _server.record_chunk(
                                request_id, model, chunk_index, chunk_tokens,
                            )
                            total_tokens += chunk_tokens
                            chunk_index += 1

                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass

                        yield line + "\n"

        except Exception as exc:
            had_error = True
            await _server.record_stream_error(request_id, model, type(exc).__name__)
            yield f'data: {{"error": "{type(exc).__name__}"}}\n\n'

        finally:
            if not had_error:
                await _server.end_stream(
                    request_id, model, total_tokens,
                    expected_tokens=request.expected_tokens,
                )

    return StreamingResponse(
        stream_with_metrics(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-ID": request_id,
        },
    )


@app.get("/v1/observability/dashboard/events")
async def dashboard_sse_events() -> StreamingResponse:
    """SSE Broadcast Event Stream for the real-time dashboard.

    Returns a continuous Server-Sent Events stream that yields structured
    JSON with live performance analytics. The channel updates seamlessly
    without polling or blocking active model execution channels.

    Event types:
    - ttft: Time-to-first-token observation for a specific request.
    - stream_end: Final metrics when a stream completes.
    - drift_anomaly: Semantic drift anomaly detected.
    - error: Stream error occurred.
    - snapshot: Periodic aggregate snapshot of all metrics.
    """
    return StreamingResponse(
        _server.event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/v1/observability/snapshot")
async def observability_snapshot() -> dict[str, Any]:
    """Get a point-in-time snapshot of all current metrics."""
    return _server.get_snapshot()


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    """Prometheus metrics scraping endpoint for Grafana configuration."""
    return export_metrics()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "realtime-llm-observability"}


def _get_vendor(model: str) -> str:
    """Determine the vendor from the model name."""
    if "gpt" in model.lower():
        return "openai"
    if "claude" in model.lower():
        return "anthropic"
    if "llama" in model.lower():
        return "ollama"
    return "unknown"


def _httpx_client():
    """Create an httpx AsyncClient with appropriate timeouts."""
    import httpx
    return httpx.AsyncClient(timeout=120.0)
