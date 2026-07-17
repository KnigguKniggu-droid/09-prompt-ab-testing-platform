# LLM API Gateway

> Enterprise-grade LLM gateway with per-tenant rate limits.

Token-bucket rate limiting per API key/tenant/model, circuit breaker with model fallback, request/response transformation, full OpenTelemetry observability.

**Part of [AEGIS](https://github.com/KnigguKniggu-droid/AEGIS)** — Adaptive AI Governance Infrastructure for Cyber-Physical Systems. This subsystem maps to **L2: Programmable Data Plane** (Token bucket / traffic shaping — atomic Redis Lua scripts implement burst-capable rate control, identical to network QoS enforcement.).

---

## Architecture Position

```
AEGIS Layer: L2: Programmable Data Plane
ECE Mapping: Token bucket / traffic shaping — atomic Redis Lua scripts implement burst-capable rate control, identical to network QoS enforcement.
```

This module is one of 15 subsystems in the AEGIS platform. See the [unified architecture](https://github.com/KnigguKniggu-droid/AEGIS#readme) for how all components interconnect.

---

## Features

- Token-bucket rate limiting per API key / tenant / model (Redis-backed)
- Circuit breaker: trips on 5xx/timeout > threshold, fails over to backup
- Request/response transformation: PII strip, format enforcement, token truncation
- OpenTelemetry traces + Prometheus metrics (latency, tokens, errors, cost)
- Canary routing: 5% traffic to new model version

---

## Tech Stack

`Python` | `FastAPI` | `Redis` | `OpenTelemetry` | `Prometheus` | `Grafana` | `Docker` | `Kubernetes`

---

## Quick Start

```bash
git clone https://github.com/KnigguKniggu-droid/11-llm-api-gateway.git
cd 11-llm-api-gateway
pip install -e .
```

Run tests:

```bash
pytest tests/ -v
```

---

## Project Structure

```
11_llm_api_gateway/
  src/                  # Core application code
  tests/                # 22 passing tests
  .github/              # CI/CD workflows
  Dockerfile            # Container build
  pyproject.toml        # Package configuration
```

---

## Running in Docker

```bash
docker build -t 11_llm_api_gateway .
docker run -p 8000:8000 11_llm_api_gateway
```

---

## ECE Design Principles

This subsystem is modeled after classical electrical and computer engineering concepts:

> **Token bucket / traffic shaping — atomic Redis Lua scripts implement burst-capable rate control, identical to network QoS enforcement.**

The AEGIS platform applies safety-critical engineering principles from integrated circuit design to LLM deployment, ensuring production reliability in autonomous vehicles, power grids, and medical devices.

---

## Related Projects

All 15 AEGIS subsystems:

| # | Project | Layer | ECE Mapping |
|---|---------|-------|-------------|
| 01 | [Model Regression Detection](https://github.com/KnigguKniggu-droid/AEGIS) | L5 | SPC |
| 02 | [LLM Cost Autopilot](https://github.com/KnigguKniggu-droid/AEGIS) | L1 | DVFS |
| 03 | [Failure Forensics](https://github.com/KnigguKniggu-droid/AEGIS) | L4 | BIST+ATPG |
| 04 | [Self-Healing Docs](https://github.com/KnigguKniggu-droid/AEGIS) | L6 | ECO |
| 05 | [Output Arbitration](https://github.com/KnigguKniggu-droid/AEGIS) | L4 | TMR |
| 06 | [Hybrid Search RAG](https://github.com/KnigguKniggu-droid/AEGIS) | L3 | Sensor Fusion |
| 07 | [Semantic Cache](https://github.com/KnigguKniggu-droid/AEGIS) | L2 | CAM |
| 08 | [SQL Guardrails](https://github.com/KnigguKniggu-droid/AEGIS) | L4 | MPU/MMU |
| 09 | [A/B Testing](https://github.com/KnigguKniggu-droid/AEGIS) | L5 | SPRT |
| 10 | [LoRA Pipeline](https://github.com/KnigguKniggu-droid/AEGIS) | L1 | SVD |
| 11 | [API Gateway](https://github.com/KnigguKniggu-droid/AEGIS) | L2 | Token Bucket |
| 12 | [Feature Flags](https://github.com/KnigguKniggu-droid/AEGIS) | L5 | FPGA Reconfig |
| 13 | [Dataset Generator](https://github.com/KnigguKniggu-droid/AEGIS) | L3 | Signal Conditioning |
| 14 | [Workflow Orchestrator](https://github.com/KnigguKniggu-droid/AEGIS) | L6 | FSM Sequencer |
| 15 | [LLM Observability](https://github.com/KnigguKniggu-droid/AEGIS) | L7 | Oscilloscope+SA |

---

## License

MIT
