# Semantic Caching Proxy

> Sub-millisecond LLM responses for repeated queries.

Drop-in proxy between your app and any LLM API. Embedding-based semantic dedup with cosine > 0.95, Redis Cluster for horizontal scale, cache warming from historical logs.

**Part of [AEGIS](https://github.com/KnigguKniggu-droid/AEGIS)** — Adaptive AI Governance Infrastructure for Cyber-Physical Systems. This subsystem maps to **L2: Programmable Data Plane** (Content-Addressable Memory (CAM) — cosine similarity > 0.95 is a CAM lookup where the address is meaning, not physical location.).

---

## Architecture Position

```
AEGIS Layer: L2: Programmable Data Plane
ECE Mapping: Content-Addressable Memory (CAM) — cosine similarity > 0.95 is a CAM lookup where the address is meaning, not physical location.
```

This module is one of 15 subsystems in the AEGIS platform. See the [unified architecture](https://github.com/KnigguKniggu-droid/AEGIS#readme) for how all components interconnect.

---

## Features

- Embedding-based semantic dedup (cosine > 0.95) with TTL + LRU eviction
- Redis Cluster for horizontal scale; 500k entries < 2GB
- Cache warming from historical logs; invalidation on model version bump
- Metrics: hit rate, latency distribution, cost saved ($/day)
- Works with OpenAI, Anthropic, vLLM, Ollama — zero code change

---

## Tech Stack

`Python` | `FastAPI` | `Redis Cluster` | `Sentence-Transformers` | `Prometheus` | `Grafana`

---

## Quick Start

```bash
git clone https://github.com/KnigguKniggu-droid/07-semantic-caching-proxy.git
cd 07-semantic-caching-proxy
pip install -e .
```

Run tests:

```bash
pytest tests/ -v
```

---

## Project Structure

```
07_semantic_caching_proxy/
  src/                  # Core application code
  tests/                # 8 passing tests
  .github/              # CI/CD workflows
  Dockerfile            # Container build
  pyproject.toml        # Package configuration
```

---

## Running in Docker

```bash
docker build -t 07_semantic_caching_proxy .
docker run -p 8000:8000 07_semantic_caching_proxy
```

---

## ECE Design Principles

This subsystem is modeled after classical electrical and computer engineering concepts:

> **Content-Addressable Memory (CAM) — cosine similarity > 0.95 is a CAM lookup where the address is meaning, not physical location.**

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
