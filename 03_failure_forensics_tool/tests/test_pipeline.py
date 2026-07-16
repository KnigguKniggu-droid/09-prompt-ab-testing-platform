"""Tests for the pipeline tracing and forensic analyzer."""

from __future__ import annotations

import pytest

from src.models import (
    FaultTaxonomy,
    PipelineInput,
    PipelineStep,
    SpanData,
    SpanStatus,
    TraceRecord,
)
from src.pipeline import ForensicPipeline, SpanContext, span_context
from src.forensics import analyze_trace


def test_pipeline_produces_four_spans():
    pipeline = ForensicPipeline()
    result = pipeline.run(PipelineInput(raw_text="The system crashed and we need a bug fix immediately."))
    assert len(result.spans) == 4
    steps = [s.step for s in result.spans]
    assert steps == [
        PipelineStep.INTAKE,
        PipelineStep.EXTRACTION,
        PipelineStep.CLASSIFICATION,
        PipelineStep.SUMMARIZATION,
    ]


def test_pipeline_confidence_scores_recorded():
    pipeline = ForensicPipeline()
    result = pipeline.run(PipelineInput(raw_text="Request for a new feature enhancement."))
    assert all(1 <= score <= 5 for score in result.confidence_scores.values())
    assert len(result.confidence_scores) == 4


def test_span_context_manager_records_latency():
    trace = TraceRecord(trace_id="test-trace")
    SpanContext.set_trace(trace)
    SpanContext.set_current_span(None)

    with span_context(PipelineStep.EXTRACTION, input_tokens=100) as span:
        span.output_data = {"test": True}
        span.model_confidence = 4

    assert span.latency_ms >= 0
    assert span.status == SpanStatus.OK
    assert len(trace.spans) == 1
    assert trace.spans[0].step == PipelineStep.EXTRACTION
    SpanContext.reset()


def test_forensic_analyzer_no_faults():
    trace = TraceRecord(trace_id="clean-trace")
    for step in PipelineStep:
        trace.add_span(SpanData(
            span_id=f"span-{step.value}",
            trace_id="clean-trace",
            step=step,
            latency_ms=100.0,
            input_tokens=50,
            output_tokens=50,
            model_confidence=5,
            output_data={"data": "preserved"},
        ))
    trace.final_status = SpanStatus.OK
    report = analyze_trace(trace)
    assert len(report.findings) == 0
    assert report.severity == "low"


def test_forensic_analyzer_detects_low_confidence():
    trace = TraceRecord(trace_id="low-conf-trace")
    trace.add_span(SpanData(
        span_id="s1", trace_id="low-conf-trace", step=PipelineStep.INTAKE,
        latency_ms=50, model_confidence=5, output_data={"raw": "text"},
    ))
    trace.add_span(SpanData(
        span_id="s2", trace_id="low-conf-trace", step=PipelineStep.EXTRACTION,
        latency_ms=100, model_confidence=1, output_data={"entities": []},
    ))
    trace.add_span(SpanData(
        span_id="s3", trace_id="low-conf-trace", step=PipelineStep.CLASSIFICATION,
        latency_ms=80, model_confidence=5, output_data={"label": "test"},
    ))
    trace.add_span(SpanData(
        span_id="s4", trace_id="low-conf-trace", step=PipelineStep.SUMMARIZATION,
        latency_ms=60, model_confidence=5, output_data={"summary": "done"},
    ))
    trace.final_status = SpanStatus.OK
    report = analyze_trace(trace)
    assert any(f.fault_type == FaultTaxonomy.EXTRACTION_HALLUCINATION for f in report.findings)
    assert report.severity in ("critical", "high")


def test_forensic_analyzer_detects_error_span():
    trace = TraceRecord(trace_id="error-trace")
    trace.add_span(SpanData(
        span_id="s1", trace_id="error-trace", step=PipelineStep.INTAKE,
        latency_ms=50, model_confidence=5, output_data={"raw": "text"},
    ))
    trace.add_span(SpanData(
        span_id="s2", trace_id="error-trace", step=PipelineStep.EXTRACTION,
        latency_ms=100, model_confidence=3, output_data={},
        status=SpanStatus.ERROR, error_message="Model timeout",
    ))
    trace.final_status = SpanStatus.ERROR
    report = analyze_trace(trace)
    assert any(f.fault_type == FaultTaxonomy.EXTRACTION_HALLUCINATION for f in report.findings)
    assert report.severity == "critical"


def test_forensic_analyzer_detects_context_loss():
    trace = TraceRecord(trace_id="ctx-loss-trace")
    trace.add_span(SpanData(
        span_id="s1", trace_id="ctx-loss-trace", step=PipelineStep.INTAKE,
        latency_ms=50, model_confidence=5,
        output_data={"long_data": "x" * 1000, "context": "important"},
    ))
    trace.add_span(SpanData(
        span_id="s2", trace_id="ctx-loss-trace", step=PipelineStep.EXTRACTION,
        latency_ms=100, model_confidence=4,
        output_data={"short": "x"},
    ))
    trace.add_span(SpanData(
        span_id="s3", trace_id="ctx-loss-trace", step=PipelineStep.CLASSIFICATION,
        latency_ms=80, model_confidence=4, output_data={"label": "test"},
    ))
    trace.add_span(SpanData(
        span_id="s4", trace_id="ctx-loss-trace", step=PipelineStep.SUMMARIZATION,
        latency_ms=60, model_confidence=4, output_data={"summary": "done"},
    ))
    trace.final_status = SpanStatus.OK
    report = analyze_trace(trace)
    assert any(f.fault_type == FaultTaxonomy.CONTEXT_LOSS for f in report.findings)
