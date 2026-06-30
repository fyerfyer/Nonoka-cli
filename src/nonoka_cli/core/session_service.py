"""Session lifecycle service for nonoka-cli."""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog

from nonoka_cli.sessions.manager import SessionManager
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.utils.errors import SessionError, SessionNotFoundError

logger = structlog.get_logger("nonoka_cli.core.session")


class SessionService:
  """Manage session metadata: create, switch, rename, delete, list."""

  def __init__(
    self,
    session_manager: SessionManager | None = None,
    db_path: Path | str | None = None,
  ):
    self._manager = session_manager or SessionManager(db_path=db_path)
    self._current_id: str = str(uuid.uuid4())

  @property
  def current_id(self) -> str:
    return self._current_id

  @property
  def manager(self) -> SessionManager:
    return self._manager

  async def initialize(self, model: str) -> None:
    """Ensure the current session exists in the store."""
    existing = await self._manager.get(self._current_id)
    if existing is None:
      await self._manager.create(
        session_id=self._current_id,
        model=model,
      )

  async def new(self, model: str, name: str | None = None) -> str:
    """Create a new session and switch to it."""
    self._current_id = str(uuid.uuid4())
    await self._manager.create(
      session_id=self._current_id,
      model=model,
      name=name,
    )
    logger.info("session_created", session_id=self._current_id, name=name)
    return self._current_id

  async def switch(self, session_id: str) -> SessionInfo:
    """Switch to an existing session."""
    info = await self._manager.get(session_id)
    if info is None:
      raise SessionNotFoundError(f"Session not found: {session_id}")
    self._current_id = session_id
    logger.info("session_switched", session_id=session_id)
    return info

  async def rename(self, name: str) -> SessionInfo:
    """Rename the current session."""
    return await self._manager.rename(self._current_id, name)

  async def delete(self, session_id: str) -> None:
    """Delete a session."""
    if session_id == self._current_id:
      raise SessionError(
        "Cannot delete the active session. Switch to another session first."
      )
    await self._manager.delete(session_id)

  async def list(self) -> list[SessionInfo]:
    """Return all sessions ordered by last activity descending."""
    return await self._manager.list()

  async def touch(self) -> None:
    """Update last_active for the current session."""
    await self._manager.touch(self._current_id)

  async def close(self) -> None:
    """Close the underlying session manager."""
    await self._manager.close()
