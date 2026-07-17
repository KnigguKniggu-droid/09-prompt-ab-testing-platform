# Failure Forensics Tool

> Automated root-cause analysis for LLM failures.

Captures full request/response/context on every error, classifies failure modes (timeout, rate-limit, hallucination, refusal, format-error, policy), and generates minimal repro test cases with suggested fixes.

**Part of [AEGIS](https://github.com/KnigguKniggu-droid/AEGIS)** — Adaptive AI Governance Infrastructure for Cyber-Physical Systems. This subsystem maps to **L4: Safety-Critical Fault Tolerance** (BIST + ATPG + fault dictionary — 4-step pipeline trace with span contexts, minimal repro generation is automatic test pattern generation.).

---

## Architecture Position

```
AEGIS Layer: L4: Safety-Critical Fault Tolerance
ECE Mapping: BIST + ATPG + fault dictionary — 4-step pipeline trace with span contexts, minimal repro generation is automatic test pattern generation.
```

This module is one of 15 subsystems in the AEGIS platform. See the [unified architecture](https://github.com/KnigguKniggu-droid/AEGIS#readme) for how all components interconnect.

---

## Features

- Full request/response/context capture on every error (PII-redacted)
- Failure mode classification: timeout, rate-limit, hallucination, refusal, format-error, policy
- Minimal repro test case + suggested fix generation
- SQLite + ClickHouse for hot/warm storage with 90-day retention
- Streamlit timeline view: filter by model, error type, user segment

---

## Tech Stack

`Python` | `FastAPI` | `ClickHouse` | `SQLite` | `Presidio (PII)` | `Streamlit` | `Docker`

---

## Quick Start

```bash
git clone https://github.com/KnigguKniggu-droid/03-failure-forensics-tool.git
cd 03-failure-forensics-tool
pip install -e .
```

Run tests:

```bash
pytest tests/ -v
```

---

## Project Structure

```
03_failure_forensics_tool/
  src/                  # Core application code
  tests/                # 7 passing tests
  .github/              # CI/CD workflows
  Dockerfile            # Container build
  pyproject.toml        # Package configuration
```

---

## Running in Docker

```bash
docker build -t 03_failure_forensics_tool .
docker run -p 8000:8000 03_failure_forensics_tool
```

---

## ECE Design Principles

This subsystem is modeled after classical electrical and computer engineering concepts:

> **BIST + ATPG + fault dictionary — 4-step pipeline trace with span contexts, minimal repro generation is automatic test pattern generation.**

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
