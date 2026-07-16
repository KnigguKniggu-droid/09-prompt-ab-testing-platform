"""Strict Pydantic WorkflowState for the agentic workflow orchestrator.

Defines a transactional state object that tracks the complete execution
memory of a multi-agent workflow. This state is serialized to disk or
Redis on every checkpoint and restored on failure recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ConfigDict


class ExecutionNodeName(str, Enum):
    """Named execution nodes in the workflow graph."""

    PLANNER = "task_planner"
    CODE_EXECUTOR = "code_executor"
    VALIDATOR = "validator"
    REVIEWER = "reviewer"
    SUMMARIZER = "summarizer"
    ORCHESTRATOR = "orchestrator"


class NodeStatus(str, Enum):
    """Status of a single execution node invocation."""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class ExecutionHistoryEntry(BaseModel):
    """A single entry in the execution history log.

    Records every node transition, including the node name, input,
    output, status, and any error that occurred. This provides a
    complete audit trail of the workflow execution.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    entry_id: str = Field(..., description="Unique entry identifier")
    node: ExecutionNodeName
    status: NodeStatus
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    retry_attempt: int = 0
    temperature_used: float = 0.7
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0


class Milestone(BaseModel):
    """A granular milestone produced by the Task Planner node."""

    milestone_id: str
    description: str
    status: NodeStatus = NodeStatus.IDLE
    dependencies: list[str] = Field(default_factory=list, description="IDs of milestones that must complete first")
    assigned_node: ExecutionNodeName | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0


class WorkflowState(BaseModel):
    """Strict transactional workflow state object.

    Tracks the complete execution memory of a multi-agent workflow.
    This object is serialized to an immutable checkpoint layout whenever
    a sub-agent switches active execution nodes, and restored from
    the last verified checkpoint on failure recovery.

    Fields:
        session_id: Unique identifier for this workflow session.
        task_objective: The global objective the workflow is solving.
        execution_history: Ordered list of every node invocation record.
        execution_node: Pointer to the currently active execution node.
        shared_memory_variables: Cross-node shared key-value store.
        checkpoint_version: Incremental version tracker for checkpoints.
        milestones: Granular sub-objectives produced by the planner.
        retry_index: Global retry counter across all recovery attempts.
        hyperparameters: Adjustable parameters (temperature, model, etc.).
        max_retries: Maximum recovery attempts before declaring failure.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str = Field(..., description="Unique session identifier")
    task_objective: str = Field(..., description="Global objective for the workflow")
    execution_history: list[ExecutionHistoryEntry] = Field(default_factory=list)
    execution_node: ExecutionNodeName = Field(ExecutionNodeName.ORCHESTRATOR, description="Currently active node")
    shared_memory_variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Cross-node shared key-value store for intermediate results",
    )
    checkpoint_version: int = Field(0, ge=0, description="Incremental checkpoint version tracker")
    milestones: list[Milestone] = Field(default_factory=list)
    retry_index: int = Field(0, ge=0, description="Global retry counter")
    hyperparameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "model": "gpt-4o",
            "max_tokens": 2000,
            "timeout_seconds": 120,
        }
    )
    max_retries: int = Field(3, ge=1, description="Maximum recovery attempts before failure")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: NodeStatus = Field(NodeStatus.IDLE, description="Overall workflow status")
    last_checkpoint_path: str | None = Field(None, description="Filepath of the last saved checkpoint")

    def add_history_entry(self, entry: ExecutionHistoryEntry) -> None:
        """Append a new execution history entry."""
        self.execution_history.append(entry)
        self._touch()

    def set_active_node(self, node: ExecutionNodeName) -> None:
        """Update the active execution node pointer."""
        self.execution_node = node
        self._touch()

    def set_memory(self, key: str, value: Any) -> None:
        """Set a shared memory variable."""
        self.shared_memory_variables[key] = value
        self._touch()

    def get_memory(self, key: str, default: Any = None) -> Any:
        """Get a shared memory variable."""
        return self.shared_memory_variables.get(key, default)

    def increment_checkpoint(self) -> int:
        """Increment the checkpoint version and return the new value."""
        self.checkpoint_version += 1
        self._touch()
        return self.checkpoint_version

    def increment_retry(self) -> int:
        """Increment the global retry index and return the new value."""
        self.retry_index += 1
        self._touch()
        return self.retry_index

    def adjust_hyperparameter(self, key: str, value: Any) -> None:
        """Adjust a hyperparameter for retry attempts."""
        self.hyperparameters[key] = value
        self._touch()

    def get_temperature(self) -> float:
        """Get the current temperature setting."""
        return float(self.hyperparameters.get("temperature", 0.7))

    def get_milestones_by_status(self, status: NodeStatus) -> list[Milestone]:
        """Get all milestones matching a given status."""
        return [m for m in self.milestones if m.status == status]

    @property
    def pending_milestones(self) -> list[Milestone]:
        """Milestones that have not been started or are retrying."""
        return self.get_milestones_by_status(NodeStatus.IDLE) + self.get_milestones_by_status(NodeStatus.RETRYING)

    @property
    def completed_milestones(self) -> list[Milestone]:
        """Milestones that have succeeded."""
        return self.get_milestones_by_status(NodeStatus.SUCCEEDED)

    @property
    def is_complete(self) -> bool:
        """Check if all milestones have completed successfully."""
        if not self.milestones:
            return False
        return all(m.status == NodeStatus.SUCCEEDED for m in self.milestones)

    @property
    def has_exhausted_retries(self) -> bool:
        """Check if the workflow has exhausted all retry attempts."""
        return self.retry_index >= self.max_retries

    def to_checkpoint_dict(self) -> dict[str, Any]:
        """Serialize the state to a dictionary for checkpoint persistence."""
        return self.model_dump(mode="json")

    def _touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc)

    def summary(self) -> dict[str, Any]:
        """Return a summary of the current state for logging."""
        return {
            "session_id": self.session_id,
            "execution_node": self.execution_node.value,
            "checkpoint_version": self.checkpoint_version,
            "retry_index": self.retry_index,
            "history_entries": len(self.execution_history),
            "milestones_total": len(self.milestones),
            "milestones_completed": len(self.completed_milestones),
            "milestones_pending": len(self.pending_milestones),
            "status": self.status.value,
            "temperature": self.get_temperature(),
        }
