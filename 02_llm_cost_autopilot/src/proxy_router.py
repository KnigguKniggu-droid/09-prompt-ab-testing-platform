"""Async complexity classifier and multi-provider routing proxy.

Scores incoming payloads across three strict tiers and routes requests
to the most cost-effective model that supports the required complexity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from src.models import (
    ComplexityClassification,
    ComplexityTier,
    ModelConfig,
    ProviderType,
    ProxyRequest,
    ProxyResponse,
    RoutingDecision,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"

# Complexity classification signal weights
SIGNAL_WEIGHTS: dict[str, float] = {
    "message_count": 0.15,
    "total_token_estimate": 0.20,
    "has_code_block": 0.20,
    "has_multi_step_instruction": 0.25,
    "has_reasoning_markers": 0.20,
    "output_length_request": 0.15,
    "has_system_prompt": 0.05,
}

REASONING_MARKERS = [
    "explain", "analyze", "reason", "why", "because", "therefore",
    "step by step", "derive", "calculate", "prove", "compare",
    "evaluate", "assess", "design", "architect", "implement",
    "classify", "categorize", "sort", "label", "tag", "route",
]

CODE_MARKERS = ["```", "def ", "function", "class ", "import ", "SELECT", "CREATE TABLE", "async "]

MULTI_STEP_MARKERS = ["first", "then", "next", "finally", "after that", "subsequently", "step 1", "step 2"]


def load_model_registry(config_path: Path | None = None) -> dict[str, ModelConfig]:
    path = config_path or CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    registry: dict[str, ModelConfig] = {}
    for m in raw["models"]:
        cfg = ModelConfig.model_validate(m)
        registry[cfg.model_id] = cfg
    return registry


def load_routing_rules(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw["routing_rules"]


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def classify_complexity(request: ProxyRequest) -> ComplexityClassification:
    """Classify request into one of three strict complexity tiers.

    Tier 1 (Extraction): short input, single-turn, no reasoning markers.
    Tier 2 (Classification): moderate input, categorization or routing.
    Tier 3 (Multi-step Logic): long input, code blocks, reasoning markers,
        multi-step instructions, or system prompt with complex instructions.
    """
    all_text = " ".join(m.get("content", "") for m in request.messages)
    token_est = estimate_tokens(all_text)

    has_code = any(marker in all_text for marker in CODE_MARKERS)
    has_reasoning = any(marker in all_text.lower() for marker in REASONING_MARKERS)
    has_multi_step = any(marker in all_text.lower() for marker in MULTI_STEP_MARKERS)
    has_system = any(m.get("role") == "system" for m in request.messages)
    msg_count = len(request.messages)
    output_long = request.max_tokens > 1000

    signals: dict[str, Any] = {
        "message_count": msg_count,
        "total_token_estimate": token_est,
        "has_code_block": has_code,
        "has_multi_step_instruction": has_multi_step,
        "has_reasoning_markers": has_reasoning,
        "output_length_request": output_long,
        "has_system_prompt": has_system,
    }

    score = 0.0
    if msg_count > 2:
        score += SIGNAL_WEIGHTS["message_count"]
    if token_est > 500:
        score += SIGNAL_WEIGHTS["total_token_estimate"]
    if has_code:
        score += SIGNAL_WEIGHTS["has_code_block"]
    if has_multi_step:
        score += SIGNAL_WEIGHTS["has_multi_step_instruction"]
    if has_reasoning:
        score += SIGNAL_WEIGHTS["has_reasoning_markers"]
    if output_long:
        score += SIGNAL_WEIGHTS["output_length_request"]
    if has_system:
        score += SIGNAL_WEIGHTS["has_system_prompt"]

    if has_code or has_multi_step or (has_reasoning and token_est > 300):
        tier = ComplexityTier.TIER_3_MULTI_STEP_LOGIC
        confidence = min(1.0, score + 0.2)
    elif score >= 0.3 or has_reasoning or msg_count > 1:
        tier = ComplexityTier.TIER_2_CLASSIFICATION
        confidence = min(1.0, max(0.5, score))
    else:
        tier = ComplexityTier.TIER_1_EXTRACTION
        confidence = max(0.5, 1.0 - score)

    return ComplexityClassification(
        tier=tier,
        confidence=confidence,
        signals=signals,
        estimated_input_tokens=token_est,
        estimated_output_tokens=request.max_tokens,
    )


def select_model(
    classification: ComplexityClassification,
    registry: dict[str, ModelConfig],
    rules: dict[str, Any],
) -> tuple[ModelConfig, str]:
    """Select the most cost-effective model for the given complexity tier."""
    tier_key = f"tier_{classification.tier.value}_preferred"
    preferred_id = rules.get(tier_key, "gpt-4o-mini")

    candidates = [
        m for m in registry.values()
        if classification.tier in m.supported_tiers
    ]
    if not candidates:
        fallback_id = rules.get("fallback_chain", ["gpt-4o"])[0]
        return registry[fallback_id], f"no candidates for tier {classification.tier.value}, using fallback"

    preferred = registry.get(preferred_id)
    if preferred and classification.tier in preferred.supported_tiers:
        return preferred, f"preferred model for tier {classification.tier.value}"

    best = max(candidates, key=lambda m: m.cost_efficiency)
    return best, "highest cost-efficiency among tier candidates"


def estimate_cost(model: ModelConfig, classification: ComplexityClassification) -> float:
    input_cost = (classification.estimated_input_tokens / 1000.0) * model.input_cost_per_1k
    output_cost = (classification.estimated_output_tokens / 1000.0) * model.output_cost_per_1k
    return input_cost + output_cost


def make_routing_decision(
    request: ProxyRequest,
    classification: ComplexityClassification,
    model: ModelConfig,
    reason: str,
) -> RoutingDecision:
    return RoutingDecision(
        request_id=str(uuid.uuid4()),
        selected_model=model.model_id,
        selected_provider=model.provider,
        tier=classification.tier,
        estimated_cost=estimate_cost(model, classification),
        estimated_latency_ms=model.latency_p50_ms,
        routing_reason=reason,
    )


async def execute_request(
    request: ProxyRequest,
    model: ModelConfig,
) -> dict[str, Any]:
    """Execute the request against the selected model provider."""
    api_key = os.environ.get(model.api_key_env, "") if model.api_key_env else ""

    if model.provider == ProviderType.OLLAMA:
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": min(request.max_tokens, model.max_output),
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        url = f"{model.api_base}/chat/completions"
    elif model.provider == ProviderType.ANTHROPIC:
        system_msg = next((m["content"] for m in request.messages if m.get("role") == "system"), "")
        user_messages = [m for m in request.messages if m.get("role") != "system"]
        payload = {
            "model": model.model_id,
            "system": system_msg,
            "messages": user_messages,
            "max_tokens": min(request.max_tokens, model.max_output),
            "temperature": request.temperature,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        url = f"{model.api_base}/messages"
    else:
        payload = {
            "model": model.model_id,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": min(request.max_tokens, model.max_output),
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        url = f"{model.api_base}/chat/completions"

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()
        data = resp.json()

    if model.provider == ProviderType.ANTHROPIC:
        content = data.get("content", [{}])[0].get("text", "")
        input_tokens = data.get("usage", {}).get("input_tokens", 0)
        output_tokens = data.get("usage", {}).get("output_tokens", 0)
    else:
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

    actual_cost = (input_tokens / 1000.0 * model.input_cost_per_1k) + (output_tokens / 1000.0 * model.output_cost_per_1k)

    return {
        "content": content,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": actual_cost,
        "latency_ms": latency,
    }


async def route_and_execute(
    request: ProxyRequest,
    registry: dict[str, ModelConfig] | None = None,
    rules: dict[str, Any] | None = None,
) -> ProxyResponse:
    """Full pipeline: classify, route, execute, and package response."""
    registry = registry or load_model_registry()
    rules = rules or load_routing_rules()

    classification = classify_complexity(request)
    model, reason = select_model(classification, registry, rules)
    decision = make_routing_decision(request, classification, model, reason)

    result = await execute_request(request, model)

    return ProxyResponse(
        request_id=decision.request_id,
        model=model.model_id,
        provider=model.provider,
        content=result["content"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost=result["cost"],
        latency_ms=result["latency_ms"],
        routing=decision,
    )
