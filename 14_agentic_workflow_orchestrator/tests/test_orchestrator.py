"""Tests for the WorkflowManager orchestrator checkpoint and recovery pipeline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.orchestrator import WorkflowManager
from src.state import (
    ExecutionHistoryEntry,
    ExecutionNodeName,
    Milestone,
    NodeStatus,
    WorkflowState,
)


def test_workflow_state_initialization():
    state = WorkflowState(
        session_id="test-session-001",
        task_objective="Write a Python function",
    )
    assert state.session_id == "test-session-001"
    assert state.task_objective == "Write a Python function"
    assert state.checkpoint_version == 0
    assert state.retry_index == 0
    assert state.execution_node == ExecutionNodeName.ORCHESTRATOR
    assert state.status == NodeStatus.IDLE
    assert state.max_retries == 3
    assert state.hyperparameters["temperature"] == 0.7


def test_workflow_state_memory_operations():
    state = WorkflowState(session_id="s1", task_objective="test")
    state.set_memory("key1", "value1")
    assert state.get_memory("key1") == "value1"
    assert state.get_memory("nonexistent", "default") == "default"
    assert "key1" in state.shared_memory_variables


def test_workflow_state_checkpoint_increment():
    state = WorkflowState(session_id="s1", task_objective="test")
    v1 = state.increment_checkpoint()
    v2 = state.increment_checkpoint()
    assert v1 == 1
    assert v2 == 2
    assert state.checkpoint_version == 2


def test_workflow_state_retry_increment():
    state = WorkflowState(session_id="s1", task_objective="test")
    r1 = state.increment_retry()
    r2 = state.increment_retry()
    assert r1 == 1
    assert r2 == 2
    assert state.retry_index == 2


def test_workflow_state_adjust_hyperparameter():
    state = WorkflowState(session_id="s1", task_objective="test")
    state.adjust_hyperparameter("temperature", 0.9)
    assert state.get_temperature() == 0.9
    state.adjust_hyperparameter("model", "gpt-4o-mini")
    assert state.hyperparameters["model"] == "gpt-4o-mini"


def test_workflow_state_milestone_tracking():
    state = WorkflowState(session_id="s1", task_objective="test")
    m1 = Milestone(milestone_id="m1", description="Step 1")
    m2 = Milestone(milestone_id="m2", description="Step 2", status=NodeStatus.SUCCEEDED)
    m3 = Milestone(milestone_id="m3", description="Step 3", status=NodeStatus.RETRYING)
    state.milestones = [m1, m2, m3]

    assert len(state.pending_milestones) == 2
    assert len(state.completed_milestones) == 1
    assert not state.is_complete

    for m in state.milestones:
        m.status = NodeStatus.SUCCEEDED
    assert state.is_complete


def test_workflow_state_exhausted_retries():
    state = WorkflowState(session_id="s1", task_objective="test", max_retries=2)
    assert not state.has_exhausted_retries
    state.increment_retry()
    state.increment_retry()
    assert state.has_exhausted_retries


def test_workflow_state_to_checkpoint_dict():
    state = WorkflowState(session_id="s1", task_objective="test")
    state.set_memory("k", "v")
    state.increment_checkpoint()
    d = state.to_checkpoint_dict()
    assert isinstance(d, dict)
    assert d["session_id"] == "s1"
    assert d["checkpoint_version"] == 1
    assert d["shared_memory_variables"]["k"] == "v"


def test_execution_history_entry():
    entry = ExecutionHistoryEntry(
        entry_id="e1",
        node=ExecutionNodeName.PLANNER,
        status=NodeStatus.SUCCEEDED,
        input_data={"key": "val"},
        output_data={"result": "done"},
    )
    assert entry.node == ExecutionNodeName.PLANNER
    assert entry.status == NodeStatus.SUCCEEDED
    assert entry.error_message is None


def test_workflow_state_summary():
    state = WorkflowState(session_id="s1", task_objective="test")
    state.milestones = [
        Milestone(milestone_id="m1", description="a", status=NodeStatus.SUCCEEDED),
        Milestone(milestone_id="m2", description="b"),
    ]
    state.increment_checkpoint()
    state.increment_retry()
    summary = state.summary()
    assert summary["session_id"] == "s1"
    assert summary["checkpoint_version"] == 1
    assert summary["retry_index"] == 1
    assert summary["milestones_total"] == 2
    assert summary["milestones_completed"] == 1
    assert summary["milestones_pending"] == 1


@pytest.mark.asyncio
async def test_save_checkpoint_writes_file(tmp_path):
    manager = WorkflowManager(
        task_objective="test objective",
        checkpoint_dir=tmp_path,
        max_retries=3,
    )
    filepath = await manager.save_checkpoint(reason="test")
    assert Path(filepath).exists()
    data = json.loads(Path(filepath).read_text(encoding="utf-8"))
    assert data["session_id"] == manager.session_id
    assert data["task_objective"] == "test objective"
    assert data["checkpoint_version"] == 1


@pytest.mark.asyncio
async def test_recover_state_restores_memory(tmp_path):
    manager = WorkflowManager(
        task_objective="test objective",
        checkpoint_dir=tmp_path,
        max_retries=3,
    )
    manager.state.set_memory("important_value", 42)
    manager.state.milestones = [Milestone(milestone_id="m1", description="step 1")]
    await manager.save_checkpoint(reason="pre-execution")

    manager.state.set_memory("important_value", 999)
    manager.state.set_memory("corrupted_data", "bad")

    recovered = await manager.recover_state("simulated API error", "m1")
    assert recovered
    assert manager.state.retry_index == 1
    assert manager.state.get_memory("important_value") == 42
    assert "corrupted_data" not in manager.state.shared_memory_variables


@pytest.mark.asyncio
async def test_recover_state_increases_temperature(tmp_path):
    manager = WorkflowManager(
        task_objective="test",
        checkpoint_dir=tmp_path,
        max_retries=5,
    )
    initial_temp = manager.state.get_temperature()
    await manager.save_checkpoint(reason="initial")
    await manager.recover_state("error", "m1")
    assert manager.state.get_temperature() > initial_temp
    assert manager.state.get_temperature() <= 1.0


@pytest.mark.asyncio
async def test_recover_state_exhausted_retries(tmp_path):
    manager = WorkflowManager(
        task_objective="test",
        checkpoint_dir=tmp_path,
        max_retries=2,
    )
    await manager.save_checkpoint(reason="initial")
    await manager.recover_state("error 1", "m1")
    await manager.recover_state("error 2", "m1")
    result = await manager.recover_state("error 3", "m1")
    assert not result
    assert manager.state.has_exhausted_retries


@pytest.mark.asyncio
async def test_full_workflow_run(tmp_path):
    from src.sample_loop import task_planner_node, code_executor_node

    manager = WorkflowManager(
        task_objective="Write a Python function that validates email addresses",
        checkpoint_dir=tmp_path,
        max_retries=3,
    )
    manager.register_node(ExecutionNodeName.PLANNER, task_planner_node)
    manager.register_node(ExecutionNodeName.CODE_EXECUTOR, code_executor_node)

    result = await manager.run()

    assert result["session_id"] == manager.session_id
    assert result["milestones_total"] > 0
    assert result["milestones_completed"] == result["milestones_total"]
    assert result["checkpoints_saved"] > 0
    assert result["status"] == "succeeded"


@pytest.mark.asyncio
async def test_workflow_with_failing_node_retries(tmp_path):
    call_count = 0

    async def failing_planner(state: WorkflowState) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("Simulated API failure")
        state.milestones = [Milestone(milestone_id="m1", description="done")]
        return {"milestones": 1}

    async def simple_executor(state: WorkflowState) -> dict:
        return {"result": "ok"}

    manager = WorkflowManager(
        task_objective="test with failures",
        checkpoint_dir=tmp_path,
        max_retries=5,
    )
    manager.register_node(ExecutionNodeName.PLANNER, failing_planner)
    manager.register_node(ExecutionNodeName.CODE_EXECUTOR, simple_executor)

    result = await manager.run()
    assert call_count >= 3
    assert result["retries_used"] >= 2
