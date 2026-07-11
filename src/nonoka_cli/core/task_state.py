"""Local task-state persistence for OpenCode ``todowrite`` synchronization.

OpenCode's native ``todowrite`` tool maintains the visible TODO list in the
TUI, but that state lives in the OpenCode process.  This module provides a
lightweight local mirror so that:

- Task progress survives across nonoka-cli restarts.
- Other tools (e.g. skill loaders) can read the current plan.
- The bridge layer can reconstruct the TODO list when resuming a session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("nonoka_cli.core.task_state")


TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled", "blocked", "deleted")


@dataclass
class TaskStep:
  """A single task item."""

  id: str
  content: str
  status: str = "pending"
  priority: str | None = None

  def __post_init__(self):
    if self.status not in TODO_STATUSES:
      self.status = "pending"


@dataclass
class TaskState:
  """Snapshot of a TODO list for one task."""

  task_id: str
  session_id: str
  title: str | None = None
  steps: list[TaskStep] = field(default_factory=list)
  created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
  updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

  def to_dict(self) -> dict[str, Any]:
    return {
      "task_id": self.task_id,
      "session_id": self.session_id,
      "title": self.title,
      "created_at": self.created_at,
      "updated_at": self.updated_at,
      "steps": [
        {
          "id": step.id,
          "content": step.content,
          "status": step.status,
          "priority": step.priority,
        }
        for step in self.steps
      ],
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "TaskState":
    steps = [
      TaskStep(
        id=str(item.get("id", idx)),
        content=item.get("content", ""),
        status=item.get("status", "pending"),
        priority=item.get("priority"),
      )
      for idx, item in enumerate(data.get("steps", []))
    ]
    return cls(
      task_id=data.get("task_id", ""),
      session_id=data.get("session_id", ""),
      title=data.get("title"),
      steps=steps,
      created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
      updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
    )


class TaskStateService:
  """Persist TODO snapshots to ``.nonoka/tasks/<task_id>.json``."""

  def __init__(
    self,
    tasks_dir: str | Path,
    enabled: bool = True,
    base_dir: str | Path | None = None,
  ):
    self._enabled = enabled
    self._tasks_dir = Path(tasks_dir).expanduser()
    if not self._tasks_dir.is_absolute() and base_dir is not None:
      self._tasks_dir = Path(base_dir).expanduser() / self._tasks_dir
    if self._enabled:
      try:
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
      except OSError as exc:
        logger.warning("task_state_dir_creation_failed", path=str(self._tasks_dir), error=str(exc))

  @property
  def enabled(self) -> bool:
    return self._enabled

  @property
  def tasks_dir(self) -> Path:
    return self._tasks_dir

  def _path(self, task_id: str) -> Path:
    # Sanitize filename.
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    return self._tasks_dir / f"{safe_id}.json"

  def save(self, state: TaskState) -> Path | None:
    """Persist a task state snapshot."""
    if not self._enabled:
      return None
    state.updated_at = datetime.now(timezone.utc).isoformat()
    path = self._path(state.task_id)
    try:
      path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
      logger.info("task_state_saved", task_id=state.task_id, path=str(path))
      return path
    except OSError as exc:
      logger.error("task_state_save_failed", task_id=state.task_id, error=str(exc))
      return None

  def load(self, task_id: str) -> TaskState | None:
    """Load a previously persisted task state."""
    path = self._path(task_id)
    if not path.exists():
      return None
    try:
      data = json.loads(path.read_text(encoding="utf-8"))
      return TaskState.from_dict(data)
    except (OSError, json.JSONDecodeError) as exc:
      logger.error("task_state_load_failed", task_id=task_id, error=str(exc))
      return None

  def delete(self, task_id: str) -> bool:
    """Delete a persisted task state."""
    path = self._path(task_id)
    if not path.exists():
      return False
    try:
      path.unlink()
      logger.info("task_state_deleted", task_id=task_id)
      return True
    except OSError as exc:
      logger.error("task_state_delete_failed", task_id=task_id, error=str(exc))
      return False

  def list(self, session_id: str | None = None) -> list[TaskState]:
    """Return all persisted task states, optionally filtered by session."""
    if not self._enabled or not self._tasks_dir.exists():
      return []
    states: list[TaskState] = []
    for path in self._tasks_dir.glob("*.json"):
      state = self.load(path.stem)
      if state is None:
        continue
      if session_id is not None and state.session_id != session_id:
        continue
      states.append(state)
    return states

  def sync_from_todowrite(
    self,
    session_id: str,
    todos: list[dict[str, Any]],
    task_id: str | None = None,
    title: str | None = None,
  ) -> Path | None:
    """Convert a ``todowrite`` tool payload into a local ``TaskState``."""
    if not self._enabled:
      return None
    task_id = task_id or session_id
    steps = [
      TaskStep(
        id=str(item.get("id", idx)),
        content=item.get("content", ""),
        status=item.get("status", "pending"),
        priority=item.get("priority"),
      )
      for idx, item in enumerate(todos)
    ]
    existing = self.load(task_id)
    created_at = existing.created_at if existing else datetime.now(timezone.utc).isoformat()
    state = TaskState(
      task_id=task_id,
      session_id=session_id,
      title=title,
      steps=steps,
      created_at=created_at,
    )
    return self.save(state)
