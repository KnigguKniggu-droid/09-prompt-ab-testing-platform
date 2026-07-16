"""Backward forensic analyzer for root cause detection.

Traces data propagation faults through the pipeline by walking spans
in reverse order (Summarization -> Classification -> Extraction -> Intake)
and applying the automated fault taxonomy.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.models import (
    FaultTaxonomy,
    ForensicFinding,
    ForensicReport,
    PipelineStep,
    SpanData,
    SpanStatus,
    TraceRecord,
)

CONFIDENCE_THRESHOLD = 3
TOKEN_DROP_RATIO = 0.5
LATENCY_SPIKE_MS = 5000.0


def analyze_trace(trace: TraceRecord) -> ForensicReport:
    """Run backward root cause analysis on a completed trace.

    Walks spans in reverse order for error and confidence checks,
    then forward for context loss and propagation checks. Collects
    all findings and produces a severity-ranked forensic report.
    """
    findings: list[ForensicFinding] = []
    spans_reversed = list(reversed(trace.spans))

    prev_output: dict[str, Any] | None = None
    prev_step: PipelineStep | None = None

    for span in spans_reversed:
        finding = _check_span_for_faults(span, trace, prev_output, prev_step)
        if finding:
            findings.append(finding)
        prev_output = span.output_data
        prev_step = span.step

    forward_prev_output: dict[str, Any] | None = None
    forward_prev_step: PipelineStep | None = None
    for span in trace.spans:
        finding = _check_context_loss_forward(span, trace, forward_prev_output, forward_prev_step)
        if finding and not any(f.root_cause_span == span.step and f.fault_type == FaultTaxonomy.CONTEXT_LOSS for f in findings):
            findings.append(finding)
        forward_prev_output = span.output_data
        forward_prev_step = span.step

    findings.sort(key=lambda f: f.confidence, reverse=True)

    severity = _determine_severity(findings, trace)
    assessment = _build_assessment(findings, trace)

    return ForensicReport(
        trace_id=trace.trace_id,
        findings=findings,
        overall_assessment=assessment,
        severity=severity,
        total_latency_ms=trace.total_latency_ms,
        min_confidence=trace.min_confidence,
    )


def _check_span_for_faults(
    span: SpanData,
    trace: TraceRecord,
    prev_output: dict[str, Any] | None,
    prev_step: PipelineStep | None,
) -> ForensicFinding | None:
    """Check a single span against all fault taxonomy categories."""

    if span.status == SpanStatus.ERROR:
        return _classify_error_span(span, trace)

    if span.model_confidence <= 1:
        return ForensicFinding(
            finding_id=str(uuid.uuid4()),
            trace_id=trace.trace_id,
            fault_type=FaultTaxonomy.EXTRACTION_HALLUCINATION
            if span.step == PipelineStep.EXTRACTION
            else FaultTaxonomy.PROMPT_FAILURE,
            root_cause_span=span.step,
            description=f"Model self-confidence critically low ({span.model_confidence}/5) at {span.step.value}",
            evidence={
                "confidence": span.model_confidence,
                "latency_ms": span.latency_ms,
                "output_data": span.output_data,
            },
            confidence=0.9,
            remediation="Review prompt template and input data quality for this step",
        )

    if span.model_confidence <= CONFIDENCE_THRESHOLD and span.step == PipelineStep.CLASSIFICATION:
        return ForensicFinding(
            finding_id=str(uuid.uuid4()),
            trace_id=trace.trace_id,
            fault_type=FaultTaxonomy.MISCLASSIFICATION,
            root_cause_span=span.step,
            description=f"Classification confidence below threshold ({span.model_confidence}/5)",
            evidence={
                "confidence": span.model_confidence,
                "output_data": span.output_data,
                "threshold": CONFIDENCE_THRESHOLD,
            },
            confidence=0.7,
            remediation="Verify training data distribution and consider re-prompting with more context",
        )

    if prev_output is not None and prev_step is not None:
        output_size = len(str(span.output_data))
        prev_size = len(str(prev_output))
        if prev_size > 0 and output_size / prev_size < TOKEN_DROP_RATIO:
            return ForensicFinding(
                finding_id=str(uuid.uuid4()),
                trace_id=trace.trace_id,
                fault_type=FaultTaxonomy.CONTEXT_LOSS,
                root_cause_span=span.step,
                description=f"Significant data loss between {prev_step.value} and {span.step.value}: "
                           f"output size dropped to {output_size / prev_size:.1%} of input",
                evidence={
                    "input_size": prev_size,
                    "output_size": output_size,
                    "drop_ratio": output_size / prev_size,
                    "prev_step": prev_step.value,
                },
                confidence=0.8,
                remediation="Check extraction logic for over-aggressive filtering or truncation",
            )

    if prev_output is not None and prev_step is not None:
        if not _data_propagates(prev_output, span.output_data):
            return ForensicFinding(
                finding_id=str(uuid.uuid4()),
                trace_id=trace.trace_id,
                fault_type=FaultTaxonomy.PROPAGATION_ERROR,
                root_cause_span=span.step,
                description=f"Data from {prev_step.value} not propagated to {span.step.value}",
                evidence={
                    "expected_keys": list(prev_output.keys()),
                    "actual_keys": list(span.output_data.keys()),
                    "missing": list(set(prev_output.keys()) - set(span.output_data.keys())),
                },
                confidence=0.75,
                remediation="Verify data passing between pipeline steps",
            )

    if span.latency_ms > LATENCY_SPIKE_MS:
        return ForensicFinding(
            finding_id=str(uuid.uuid4()),
            trace_id=trace.trace_id,
            fault_type=FaultTaxonomy.PROMPT_FAILURE,
            root_cause_span=span.step,
            description=f"Latency spike at {span.step.value}: {span.latency_ms:.0f}ms exceeds {LATENCY_SPIKE_MS}ms threshold",
            evidence={
                "latency_ms": span.latency_ms,
                "threshold_ms": LATENCY_SPIKE_MS,
            },
            confidence=0.6,
            remediation="Investigate model timeout, network issues, or prompt complexity",
        )

    return None


def _classify_error_span(span: SpanData, trace: TraceRecord) -> ForensicFinding:
    """Classify an errored span into the appropriate fault taxonomy."""
    step_fault_map = {
        PipelineStep.INTAKE: FaultTaxonomy.PROMPT_FAILURE,
        PipelineStep.EXTRACTION: FaultTaxonomy.EXTRACTION_HALLUCINATION,
        PipelineStep.CLASSIFICATION: FaultTaxonomy.MISCLASSIFICATION,
        PipelineStep.SUMMARIZATION: FaultTaxonomy.CONTEXT_LOSS,
    }
    fault = step_fault_map.get(span.step, FaultTaxonomy.PROPAGATION_ERROR)

    return ForensicFinding(
        finding_id=str(uuid.uuid4()),
        trace_id=trace.trace_id,
        fault_type=fault,
        root_cause_span=span.step,
        description=f"Step {span.step.value} failed with error: {span.error_message}",
        evidence={
            "error": span.error_message,
            "step": span.step.value,
            "latency_ms": span.latency_ms,
        },
        confidence=0.95,
        remediation=f"Fix the error in {span.step.value} step before re-running the pipeline",
    )


def _data_propagates(upstream: dict[str, Any], downstream: dict[str, Any]) -> bool:
    """Check if key data from upstream appears in downstream output."""
    if not upstream:
        return True
    upstream_keys = set(upstream.keys())
    downstream_str = str(downstream).lower()
    propagated = sum(1 for k in upstream_keys if str(k).lower() in downstream_str)
    if not upstream_keys:
        return True
    return propagated / len(upstream_keys) >= 0.3


def _determine_severity(findings: list[ForensicFinding], trace: TraceRecord) -> str:
    if not findings:
        return "low"
    if trace.final_status == SpanStatus.ERROR:
        return "critical"
    max_conf = max(f.confidence for f in findings)
    if max_conf >= 0.9:
        return "critical"
    if max_conf >= 0.75:
        return "high"
    if max_conf >= 0.5:
        return "medium"
    return "low"


def _build_assessment(findings: list[ForensicFinding], trace: TraceRecord) -> str:
    if not findings:
        return f"Trace {trace.trace_id} completed with no detected faults. "
    parts = [f"Trace {trace.trace_id} analysis found {len(findings)} fault(s):"]
    for f in findings:
        parts.append(f"  [{f.fault_type.value}] at {f.root_cause_span.value}: {f.description}")
    return "\n".join(parts)


def _check_context_loss_forward(
    span: SpanData,
    trace: TraceRecord,
    upstream_output: dict[str, Any] | None,
    upstream_step: PipelineStep | None,
) -> ForensicFinding | None:
    """Check for context loss by comparing a span's output to its upstream input.

    This forward-pass check detects when a step produces output that is
    significantly smaller than the input it received from the previous step,
    indicating that data was lost or filtered too aggressively.
    """
    if upstream_output is None or upstream_step is None:
        return None
    if span.status == SpanStatus.ERROR:
        return None

    output_size = len(str(span.output_data))
    upstream_size = len(str(upstream_output))

    if upstream_size > 0 and output_size / upstream_size < TOKEN_DROP_RATIO:
        return ForensicFinding(
            finding_id=str(uuid.uuid4()),
            trace_id=trace.trace_id,
            fault_type=FaultTaxonomy.CONTEXT_LOSS,
            root_cause_span=span.step,
            description=f"Significant data loss between {upstream_step.value} and {span.step.value}: "
                       f"output size dropped to {output_size / upstream_size:.1%} of input",
            evidence={
                "upstream_size": upstream_size,
                "output_size": output_size,
                "drop_ratio": output_size / upstream_size,
                "upstream_step": upstream_step.value,
            },
            confidence=0.8,
            remediation="Check extraction logic for over-aggressive filtering or truncation",
        )

    return None
