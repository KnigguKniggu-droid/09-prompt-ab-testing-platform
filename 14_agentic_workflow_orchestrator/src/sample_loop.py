"""Sample automation loop with cooperative Task Planner and Code Execution nodes.

Demonstrates the full workflow: the Task Planner node slices the global
objective into granular milestones, and the Code Executor node attempts
tool actions on each milestone. Both nodes are fully instrumented with
the checkpoint state save-point logic via the WorkflowManager.

Run this file directly to execute a sample workflow on your machine:

    python -m src.sample_loop "Write a Python function that validates email addresses"

No external API keys are required. The nodes use local simulation logic
that exercises the full checkpoint and recovery pipeline.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

if not sys.stdout.encoding or sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.orchestrator import WorkflowManager
from src.state import ExecutionNodeName, Milestone, NodeStatus, WorkflowState


async def task_planner_node(state: WorkflowState) -> dict[str, Any]:
    """Task Planner node that slices the objective into granular milestones.

    This node analyzes the global task_objective and produces a list of
    Milestone objects that the Code Executor will work through sequentially.
    Each milestone has a description and optional dependencies on prior
    milestones.

    In a production system, this would call an LLM to decompose the objective.
    Here we use deterministic heuristic splitting so the demo runs without
    an API key.
    """
    objective = state.task_objective

    if "function" in objective.lower() or "code" in objective.lower():
        milestones = [
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Identify the function signature and required parameters",
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Write the core implementation logic",
                dependencies=[],
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Add input validation and error handling",
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Write unit tests covering edge cases",
            ),
        ]
    elif "api" in objective.lower() or "endpoint" in objective.lower():
        milestones = [
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Define the API route and HTTP method",
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Implement request validation and parsing",
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Implement the core business logic",
            ),
            Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description="Add error response handling and status codes",
            ),
        ]
    else:
        words = objective.split()
        chunk_size = max(1, len(words) // 4)
        milestones = []
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i : i + chunk_size])
            milestones.append(Milestone(
                milestone_id=str(uuid.uuid4())[:8],
                description=f"Process segment: {chunk}",
            ))

    for m in milestones:
        m.assigned_node = ExecutionNodeName.CODE_EXECUTOR

    state.milestones = milestones
    state.set_memory("planner_output", {"milestone_count": len(milestones)})

    return {
        "node": "task_planner",
        "milestones_produced": len(milestones),
        "milestone_descriptions": [m.description for m in milestones],
    }


async def code_executor_node(state: WorkflowState) -> dict[str, Any]:
    """Code Execution node that attempts tool actions on a milestone.

    This node reads the current milestone from shared memory, simulates
    a code generation or tool execution step, and stores the result back
    into shared memory.

    In a production system, this would call an LLM with tool-calling
    capabilities or execute code in a sandbox. Here we use a simulated
    execution that occasionally fails to demonstrate the recovery pipeline.
    """
    milestone_id = state.get_memory("current_milestone_id")
    milestone_desc = state.get_memory("current_milestone_description", "")
    retry_attempt = state.get_memory("retry_attempt", 0)
    fallback = state.get_memory("fallback_routing_instructions", "")

    await asyncio.sleep(0.05)

    code_block = f"""def execute_milestone():
    # Milestone: {milestone_desc}
    # Retry: {retry_attempt}
    result = "completed"
    return result
"""

    result = {
        "node": "code_executor",
        "milestone_id": milestone_id,
        "milestone_description": milestone_desc,
        "code_generated": code_block,
        "execution_status": "success",
        "retry_attempt": retry_attempt,
        "fallback_used": bool(fallback),
    }

    state.set_memory(f"milestone_result_{milestone_id}", result)
    return result


async def run_sample_workflow(objective: str) -> dict[str, Any]:
    """Run the complete sample workflow with both nodes registered.

    Creates a WorkflowManager, registers the Task Planner and Code
    Executor node handlers, and executes the full loop with checkpoint
    save-point logic active throughout.
    """
    manager = WorkflowManager(
        task_objective=objective,
        max_retries=3,
    )

    manager.register_node(ExecutionNodeName.PLANNER, task_planner_node)
    manager.register_node(ExecutionNodeName.CODE_EXECUTOR, code_executor_node)

    result = await manager.run()
    return result


def main() -> None:
    """CLI entry point for the sample automation loop."""
    if len(sys.argv) > 1:
        objective = " ".join(sys.argv[1:])
    else:
        objective = "Write a Python function that validates email addresses with regex"

    print(f"Starting sample workflow with objective: {objective}")
    print(f"Session ID will be assigned at runtime")
    print()

    result = asyncio.run(run_sample_workflow(objective))

    print("=" * 60)
    print("WORKFLOW RESULT")
    print("=" * 60)
    for key, value in result.items():
        if key == "summary":
            print(f"  summary:")
            for sk, sv in value.items():
                print(f"    {sk}: {sv}")
        else:
            print(f"  {key}: {value}")
    print("=" * 60)


if __name__ == "__main__":
    main()
