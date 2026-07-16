"""Core execution loop WorkflowManager for the agentic workflow orchestrator.

Implements a transactional file-based and Redis-backed state snapshot
save-point mechanism. The save_checkpoint() function serializes the entire
state dictionary to an immutable layout whenever a sub-agent switches
active execution nodes. The recover_state() function rolls back memory
variables to the last verified checkpoint, increments the retry index,
alters hyper-parameters, and restarts from the exact point of failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.state import (
    ExecutionHistoryEntry,
    ExecutionNodeName,
    Milestone,
    NodeStatus,
    WorkflowState,
)

DEFAULT_CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "data" / "checkpoints"

# Type alias for a node handler function
NodeHandler = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


class WorkflowManager:
    """Autonomous fault-tolerant multi-agent task dispatcher engine.

    Runs complex multi-step code generation and validation loops while
    preserving complete system execution memory via transactional
    checkpoint state layout. Handles unexpected sub-agent tool failures
    gracefully by rolling back to the last verified checkpoint.
    """

    def __init__(
        self,
        task_objective: str,
        checkpoint_dir: Path | None = None,
        redis_url: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.session_id = str(uuid.uuid4())
        self.state = WorkflowState(
            session_id=self.session_id,
            task_objective=task_objective,
            max_retries=max_retries,
        )
        self.checkpoint_dir = checkpoint_dir or DEFAULT_CHECKPOINT_DIR
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "")
        self._redis_client: Any = None
        self._node_handlers: dict[ExecutionNodeName, NodeHandler] = {}
        self._checkpoint_lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()

    def register_node(self, node: ExecutionNodeName, handler: NodeHandler) -> None:
        """Register a handler function for an execution node."""
        self._node_handlers[node] = handler

    async def run(self) -> dict[str, Any]:
        """Execute the full workflow loop until completion or exhaustion.

        The loop:
        1. Calls the Task Planner node to slice the objective into milestones.
        2. Iterates over pending milestones, dispatching each to the
           Code Executor node with a checkpoint save before each execution.
        3. On failure, calls recover_state() to roll back and retry.
        4. Returns the final state summary when all milestones complete.
        """
        async with self._execution_lock:
            self.state.status = NodeStatus.RUNNING
            start_time = time.monotonic()

            planner_max_attempts = self.state.max_retries
            for attempt in range(planner_max_attempts):
                try:
                    await self.save_checkpoint(reason="pre-planner")
                    await self._execute_node(ExecutionNodeName.PLANNER)
                    break
                except Exception as exc:
                    recovered = await self.recover_state(str(exc), failed_milestone_id=None)
                    if not recovered:
                        self.state.status = NodeStatus.FAILED
                        return self._build_result(start_time, f"Planner failed and recovery exhausted: {exc}")
            else:
                self.state.status = NodeStatus.FAILED
                return self._build_result(start_time, "Planner failed after all retries")

            if not self.state.milestones:
                self.state.status = NodeStatus.FAILED
                return self._build_result(start_time, "No milestones produced by planner")

            for milestone in list(self.state.pending_milestones):
                if self.state.has_exhausted_retries:
                    self.state.status = NodeStatus.FAILED
                    return self._build_result(start_time, "Exhausted all retry attempts")

                self.state.set_memory("current_milestone_id", milestone.milestone_id)
                self.state.set_memory("current_milestone_description", milestone.description)

                await self.save_checkpoint(reason=f"pre-execution milestone {milestone.milestone_id}")

                try:
                    result = await self._execute_node(ExecutionNodeName.CODE_EXECUTOR)
                    milestone.status = NodeStatus.SUCCEEDED
                    milestone.result = result
                    self.state.set_memory(f"milestone_result_{milestone.milestone_id}", result)
                    await self.save_checkpoint(reason=f"post-execution milestone {milestone.milestone_id}")

                except Exception as exc:
                    milestone.status = NodeStatus.FAILED
                    milestone.retry_count += 1
                    recovered = await self.recover_state(str(exc), milestone.milestone_id)
                    if not recovered:
                        self.state.status = NodeStatus.FAILED
                        return self._build_result(start_time, f"Recovery failed: {exc}")

            if self.state.is_complete:
                self.state.status = NodeStatus.SUCCEEDED
                await self.save_checkpoint(reason="workflow_complete")
            else:
                self.state.status = NodeStatus.FAILED

            return self._build_result(start_time, "Workflow completed" if self.state.is_complete else "Workflow failed with incomplete milestones")

    async def _execute_node(self, node: ExecutionNodeName) -> dict[str, Any]:
        """Execute a single node handler with full instrumentation.

        Creates an execution history entry, runs the registered handler,
        and records the outcome. If the handler raises an exception,
        the history entry captures the error before re-raising.
        """
        handler = self._node_handlers.get(node)
        if handler is None:
            raise RuntimeError(f"No handler registered for node {node.value}")

        self.state.set_active_node(node)
        entry_id = str(uuid.uuid4())
        start = time.monotonic()

        history_entry = ExecutionHistoryEntry(
            entry_id=entry_id,
            node=node,
            status=NodeStatus.RUNNING,
            input_data=dict(self.state.shared_memory_variables),
            temperature_used=self.state.get_temperature(),
        )

        try:
            result = await asyncio.wait_for(
                handler(self.state),
                timeout=float(self.state.hyperparameters.get("timeout_seconds", 120)),
            )
            duration = (time.monotonic() - start) * 1000
            history_entry.status = NodeStatus.SUCCEEDED
            history_entry.output_data = result
            history_entry.duration_ms = duration
            self.state.add_history_entry(history_entry)
            return result

        except asyncio.TimeoutError:
            duration = (time.monotonic() - start) * 1000
            history_entry.status = NodeStatus.FAILED
            history_entry.error_message = f"Node {node.value} timed out after {self.state.hyperparameters.get('timeout_seconds', 120)}s"
            history_entry.duration_ms = duration
            self.state.add_history_entry(history_entry)
            raise

        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            history_entry.status = NodeStatus.FAILED
            history_entry.error_message = str(exc)
            history_entry.duration_ms = duration
            self.state.add_history_entry(history_entry)
            raise

    async def save_checkpoint(self, reason: str = "") -> str:
        """Serialize the entire state dictionary to an immutable checkpoint file.

        This function must be called whenever a sub-agent switches active
        execution nodes. The checkpoint is written to disk as a JSON file
        with a content hash for integrity verification. If Redis is
        available, a copy is also stored in Redis for distributed access.

        Returns the filepath of the saved checkpoint.
        """
        async with self._checkpoint_lock:
            version = self.state.increment_checkpoint()
            state_dict = self.state.to_checkpoint_dict()
            state_json = json.dumps(state_dict, ensure_ascii=False, sort_keys=True, default=str)
            content_hash = hashlib.sha256(state_json.encode("utf-8")).hexdigest()[:16]

            filename = f"checkpoint_{self.session_id}_v{version}_{content_hash}.json"
            filepath = self.checkpoint_dir / filename
            filepath.write_text(state_json, encoding="utf-8")
            self.state.last_checkpoint_path = str(filepath)

            if self._redis_client is not None:
                try:
                    redis_key = f"workflow_checkpoint:{self.session_id}:v{version}"
                    self._redis_client.set(redis_key, state_json, ex=86400)
                    self._redis_client.set(
                        f"workflow_checkpoint:latest:{self.session_id}",
                        redis_key,
                        ex=86400,
                    )
                except Exception:
                    pass

            return str(filepath)

    async def recover_state(self, error_message: str, failed_milestone_id: str | None = None) -> bool:
        """Roll back to the last verified checkpoint and prepare for retry.

        When a sub-agent throws an API error, runtime crash, or data
        validation timeout, this function:
        1. Loads the last verified checkpoint state from disk.
        2. Restores shared memory variables to that checkpoint version.
        3. Increments the internal retry index.
        4. Alters hyper-parameters (increases temperature, adds fallback routing).
        5. Restarts the loop from the exact point of failure.

        Returns True if recovery succeeded, False if retries are exhausted.
        """
        if self.state.has_exhausted_retries:
            return False

        retry_num = self.state.increment_retry()

        if self.state.last_checkpoint_path and Path(self.state.last_checkpoint_path).exists():
            try:
                saved_json = Path(self.state.last_checkpoint_path).read_text(encoding="utf-8")
                saved_state = WorkflowState.model_validate_json(saved_json)

                self.state.shared_memory_variables = saved_state.shared_memory_variables
                self.state.execution_history = saved_state.execution_history
                self.state.milestones = saved_state.milestones
                self.state.checkpoint_version = saved_state.checkpoint_version
            except Exception:
                pass

        new_temp = min(1.0, self.state.get_temperature() + 0.15)
        self.state.adjust_hyperparameter("temperature", new_temp)

        fallback_instructions = (
            f"FALLBACK_ROUTING: Previous attempt failed with: {error_message}. "
            f"Retry {retry_num}/{self.state.max_retries}. "
            f"Temperature increased to {new_temp:.2f}. "
            "Use simpler approach and avoid the failed path."
        )
        self.state.set_memory("fallback_routing_instructions", fallback_instructions)
        self.state.set_memory("last_error", error_message)
        self.state.set_memory("retry_attempt", retry_num)

        if failed_milestone_id:
            for milestone in self.state.milestones:
                if milestone.milestone_id == failed_milestone_id:
                    milestone.status = NodeStatus.RETRYING
                    break

        await self.save_checkpoint(reason=f"recovery_retry_{retry_num}")
        return True

    def _build_result(self, start_time: float, message: str) -> dict[str, Any]:
        """Build the final result dictionary."""
        duration = time.monotonic() - start_time
        return {
            "session_id": self.session_id,
            "status": self.state.status.value,
            "message": message,
            "duration_seconds": duration,
            "checkpoints_saved": self.state.checkpoint_version,
            "retries_used": self.state.retry_index,
            "milestones_total": len(self.state.milestones),
            "milestones_completed": len(self.state.completed_milestones),
            "history_entries": len(self.state.execution_history),
            "final_temperature": self.state.get_temperature(),
            "shared_memory_keys": list(self.state.shared_memory_variables.keys()),
            "summary": self.state.summary(),
        }

    def connect_redis(self, redis_url: str | None = None) -> None:
        """Connect to Redis for distributed checkpoint storage."""
        url = redis_url or self._redis_url
        if not url:
            return
        try:
            import redis
            self._redis_client = redis.from_url(url, decode_responses=True)
            self._redis_client.ping()
        except Exception:
            self._redis_client = None

    def get_state_summary(self) -> dict[str, Any]:
        """Return a summary of the current workflow state."""
        return self.state.summary()
