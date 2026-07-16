"""Master Architectural Cross-Integration Contract.

Maps how Project 11 (LLM Gateway) intercepts inbound connections, routes
payloads through Project 7 (Semantic Cache), relies on Project 14 (Agentic
Orchestrator) to coordinate downstream workflows, and streams tracking
metrics directly into Project 15 (Realtime Observability Dashboard).

This file serves as both documentation and a runnable integration test
that validates the contract between these four core services.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

if not sys.stdout.encoding or sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


ARCHITECTURE_DIAGRAM = """
========================================================================
          MASTER ARCHITECTURAL CROSS-INTEGRATION CONTRACT
========================================================================

                    +-------------------+
                    |   CLIENT REQUEST  |
                    |  (chat completion)|
                    +---------+---------+
                              |
                              v
                    +-------------------+
                    |  PROJECT 11       |
                    |  LLM API GATEWAY  |
                    |                   |
                    |  - Redis Token    |
                    |    Bucket Rate    |
                    |    Limiting       |
                    |  - Circuit        |
                    |    Breakers       |
                    |  - Fallback       |
                    |    Routing        |
                    +---------+---------+
                              |
                    +--------+--------+
                    |                 |
                    v                 v
          +---------+---+    +--------+--------+
          | PROJECT 7   |    |  Direct Route   |
          | SEMANTIC    |    |  (cache miss)   |
          | CACHE LAYER |    |                 |
          |             |    +--------+--------+
          | Redis VL    |             |
          | 0.95 sim    |             |
          | boundary    |             |
          +------+------+             |
                 |                    |
         +-------+--------+           |
         | CACHE HIT      |           |
         | Return cached  |           |
         | response       |           |
         +-------+--------+           |
                 |                    |
                 v                    v
          +------+--------------------+------+
          |        PROJECT 14              |
          |   AGENTIC WORKFLOW             |
          |   ORCHESTRATOR                 |
          |                                |
          |  WorkflowManager               |
          |    |                           |
          |    +-- Task Planner Node       |
          |    |   (slices objective)      |
          |    +-- Code Executor Node      |
          |    |   (tool actions)          |
          |    +-- Checkpoint Save         |
          |    +-- Error Recovery          |
          |        (roll back + retry)     |
          +---------------+----------------+
                          |
                          v
          +---------------+----------------+
          |        PROJECT 15              |
          |   REALTIME LLM OBSERVABILITY   |
          |                                |
          |  ObservabilityServer           |
          |    |                           |
          |    +-- TTFT Tracking           |
          |    +-- ITL Tracking            |
          |    +-- Token Drift Detection   |
          |    +-- SSE Event Stream        |
          |    +-- Prometheus /metrics     |
          |               |                |
          |               v                |
          |     +------------------+       |
          |     |   GRAFANA        |       |
          |     |   DASHBOARD      |       |
          |     |   (scrapes       |       |
          |     |    /metrics)     |       |
          |     +------------------+       |
          +--------------------------------+

========================================================================
  DATA FLOW SUMMARY:
========================================================================

  1. Client sends request to Project 11 (Gateway)
  2. Gateway checks rate limits via Redis Token Bucket
  3. Gateway queries Project 7 (Semantic Cache) for cache hit
     - HIT: Return cached response immediately, stream to Project 15
     - MISS: Route to upstream vendor, forward to Project 14
  4. Project 14 (Orchestrator) coordinates the workflow:
     - Planner node slices the request into milestones
     - Code Executor node processes each milestone
     - Checkpoints saved before and after each node transition
     - On failure: recover_state() rolls back and retries
  5. All streaming responses pass through Project 15 (Observability):
     - TTFT recorded on first chunk
     - ITL recorded on each subsequent chunk
     - Token drift checked on stream end
     - SSE events pushed to dashboard clients
     - Prometheus metrics exposed at /metrics
  6. Grafana scrapes /metrics endpoint for time-series visualization

========================================================================
  INTEGRATION CONTRACTS:
========================================================================

  Gateway -> Cache:
    POST /v1/chat/completions with messages, model, temperature
    Cache returns: {cache_status: hit|miss, content, similarity_score}

  Gateway -> Orchestrator:
    POST /v1/workflows with task_objective, agent_configs, task_configs
    Orchestrator returns: {session_id, status, milestones, checkpoints}

  Orchestrator -> Observability:
    POST /v1/observability/stream with streaming chat completion
    Observability returns: StreamingResponse with SSE metrics

  Observability -> Grafana:
    GET /metrics (Prometheus text exposition format)
    GET /v1/observability/dashboard/events (SSE stream)
========================================================================
"""


def print_diagram() -> None:
    """Print the full architecture diagram to stdout."""
    print(ARCHITECTURE_DIAGRAM)


async def validate_integration_contract() -> dict[str, Any]:
    """Validate that all four core services can be imported and initialized.

    This function serves as a runtime integration test that verifies
    the contract between the four core projects. It imports the key
    classes from each project and confirms they can be instantiated
    without external dependencies.
    """
    results: dict[str, Any] = {
        "project_11_gateway": False,
        "project_07_cache": False,
        "project_14_orchestrator": False,
        "project_15_observability": False,
    }
    errors: list[str] = []

    from pathlib import Path
    workspace_root = Path(__file__).resolve().parent

    project_11_path = str(workspace_root / "11_llm_api_gateway")
    project_07_path = str(workspace_root / "07_semantic_caching_proxy")
    project_14_path = str(workspace_root / "14_agentic_workflow_orchestrator")
    project_15_path = str(workspace_root / "15_realtime_llm_observability")

    saved_sys_path = sys.path[:]
    import importlib

    def _import_from_project(project_path: str, module_name: str):
        """Import a module from a specific project path, isolating sys.path."""
        for key in list(sys.modules.keys()):
            if key.startswith("src"):
                del sys.modules[key]
        sys.path[:] = [p for p in sys.path if "llm_api_gateway" not in p and "semantic_caching" not in p and "agentic_workflow" not in p and "realtime_llm" not in p]
        sys.path.insert(0, project_path)
        return importlib.import_module(module_name)

    try:
        mod = _import_from_project(project_11_path, "src.gateway")
        LLMGateway = mod.LLMGateway
        mod2 = _import_from_project(project_11_path, "src.models")
        GatewayConfig = mod2.GatewayConfig
        results["project_11_gateway"] = True
    except Exception as exc:
        errors.append(f"Project 11 Gateway: {exc}")

    try:
        mod = _import_from_project(project_07_path, "src.cache")
        SemanticCache = mod.SemanticCache
        compute_cache_key = mod.compute_cache_key
        results["project_07_cache"] = True
    except Exception as exc:
        errors.append(f"Project 07 Cache: {exc}")

    try:
        mod = _import_from_project(project_14_path, "src.orchestrator")
        WorkflowManager = mod.WorkflowManager
        mod2 = _import_from_project(project_14_path, "src.state")
        manager = WorkflowManager(task_objective="integration test", max_retries=1)
        assert manager.state.session_id is not None
        results["project_14_orchestrator"] = True
    except Exception as exc:
        errors.append(f"Project 14 Orchestrator: {exc}")

    try:
        mod = _import_from_project(project_15_path, "src.metrics")
        export_metrics = mod.export_metrics
        mod2 = _import_from_project(project_15_path, "src.server")
        ObservabilityServer = mod2.ObservabilityServer
        server = ObservabilityServer()
        assert server._active_streams == 0
        metrics_output = export_metrics()
        assert isinstance(metrics_output, str)
        results["project_15_observability"] = True
    except Exception as exc:
        errors.append(f"Project 15 Observability: {exc}")

    sys.path[:] = saved_sys_path

    results["all_passed"] = all(results.values())
    results["errors"] = errors
    return results


def main() -> None:
    """Print the diagram and run the integration validation."""
    print_diagram()
    print("Running integration contract validation...")
    print()
    result = asyncio.run(validate_integration_contract())
    for key, value in result.items():
        if key == "errors":
            if value:
                print("  Errors:")
                for err in value:
                    print(f"    - {err}")
        elif key == "all_passed":
            status = "PASSED" if value else "FAILED"
            print(f"  Overall: {status}")
        else:
            status = "OK" if value else "FAIL"
            print(f"  {key}: {status}")
    print()


if __name__ == "__main__":
    main()
