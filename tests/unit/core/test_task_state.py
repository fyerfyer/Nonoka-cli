"""Tests for local task-state persistence."""

from __future__ import annotations

import pytest

from nonoka_cli.core.task_state import TaskState, TaskStateService, TaskStep


def test_task_state_round_trip(tmp_path):
  service = TaskStateService(tasks_dir=tmp_path / "tasks", base_dir=tmp_path)
  state = TaskState(
    task_id="task-1",
    session_id="session-1",
    title="Test task",
    steps=[
      TaskStep(id="1", content="step 1", status="completed"),
      TaskStep(id="2", content="step 2", status="in_progress"),
    ],
  )
  path = service.save(state)
  assert path is not None
  assert path.exists()

  loaded = service.load("task-1")
  assert loaded is not None
  assert loaded.task_id == "task-1"
  assert loaded.session_id == "session-1"
  assert loaded.title == "Test task"
  assert len(loaded.steps) == 2
  assert loaded.steps[0].status == "completed"
  assert loaded.steps[1].status == "in_progress"


def test_sync_from_todowrite(tmp_path):
  service = TaskStateService(tasks_dir=".nonoka/tasks", base_dir=tmp_path)
  todos = [
    {"id": "1", "content": "do A", "status": "completed", "priority": "high"},
    {"id": "2", "content": "do B", "status": "in_progress"},
  ]
  path = service.sync_from_todowrite("session-1", todos, title="Plan")
  assert path is not None
  assert path.exists()
  assert path.parent == tmp_path / ".nonoka/tasks"

  loaded = service.load("session-1")
  assert loaded is not None
  assert loaded.title == "Plan"
  assert loaded.steps[1].status == "in_progress"


def test_disabled_service_does_not_write(tmp_path):
  service = TaskStateService(tasks_dir=tmp_path / "tasks", enabled=False)
  state = TaskState(task_id="task-1", session_id="session-1")
  path = service.save(state)
  assert path is None
  assert not (tmp_path / "tasks").exists()


def test_invalid_status_defaults_to_pending():
  step = TaskStep(id="1", content="x", status="unknown")
  assert step.status == "pending"


def test_list_filters_by_session(tmp_path):
  service = TaskStateService(tasks_dir=tmp_path / "tasks")
  service.save(TaskState(task_id="a", session_id="s1"))
  service.save(TaskState(task_id="b", session_id="s2"))
  s1_tasks = service.list(session_id="s1")
  assert len(s1_tasks) == 1
  assert s1_tasks[0].task_id == "a"


def test_delete_task(tmp_path):
  service = TaskStateService(tasks_dir=tmp_path / "tasks")
  service.save(TaskState(task_id="del", session_id="s1"))
  assert service.load("del") is not None
  assert service.delete("del") is True
  assert service.load("del") is None


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
