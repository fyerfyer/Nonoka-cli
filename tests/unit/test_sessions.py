"""Tests for the SessionManager."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nonoka_cli.sessions.manager import SessionManager
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.utils.errors import SessionNotFoundError


@pytest.fixture
def temp_db_path():
  """Yield a temporary SQLite database path."""
  with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    path = Path(f.name)
  yield path
  path.unlink(missing_ok=True)


@pytest.fixture
async def session_manager(temp_db_path: Path):
  """Return an initialized SessionManager backed by a temp DB."""
  manager = SessionManager(db_path=temp_db_path)
  yield manager
  await manager.close()


class TestSessionManagerCreate:
  """Tests for session creation."""

  @pytest.mark.asyncio
  async def test_create_persists_session(self, session_manager: SessionManager):
    info = await session_manager.create(
      session_id="sess-001",
      model="gpt-4o",
      name="My session",
    )

    assert isinstance(info, SessionInfo)
    assert info.session_id == "sess-001"
    assert info.name == "My session"
    assert info.model == "gpt-4o"
    assert info.message_count == 0

    loaded = await session_manager.get("sess-001")
    assert loaded is not None
    assert loaded.name == "My session"

  @pytest.mark.asyncio
  async def test_create_without_name(self, session_manager: SessionManager):
    info = await session_manager.create(session_id="sess-002", model="gpt-4o-mini")
    assert info.name is None


class TestSessionManagerGet:
  """Tests for fetching a single session."""

  @pytest.mark.asyncio
  async def test_get_existing_session(self, session_manager: SessionManager):
    await session_manager.create("sess-003", "gpt-4o")
    info = await session_manager.get("sess-003")
    assert info is not None
    assert info.session_id == "sess-003"

  @pytest.mark.asyncio
  async def test_get_missing_session_returns_none(self, session_manager: SessionManager):
    info = await session_manager.get("missing")
    assert info is None


class TestSessionManagerList:
  """Tests for listing sessions."""

  @pytest.mark.asyncio
  async def test_list_sorted_by_last_active_descending(
    self, session_manager: SessionManager
  ):
    await session_manager.create("sess-a", "gpt-4o")
    await session_manager.create("sess-b", "gpt-4o-mini")
    await session_manager.create("sess-c", "claude")

    # Touch sess-a so it becomes most recent.
    await session_manager.touch("sess-a")

    sessions = await session_manager.list()
    assert len(sessions) == 3
    assert sessions[0].session_id == "sess-a"

  @pytest.mark.asyncio
  async def test_list_empty(self, session_manager: SessionManager):
    sessions = await session_manager.list()
    assert sessions == []


class TestSessionManagerRename:
  """Tests for renaming sessions."""

  @pytest.mark.asyncio
  async def test_rename_updates_name(self, session_manager: SessionManager):
    await session_manager.create("sess-004", "gpt-4o", name="Old name")
    updated = await session_manager.rename("sess-004", "New name")

    assert updated.name == "New name"

    loaded = await session_manager.get("sess-004")
    assert loaded is not None
    assert loaded.name == "New name"

  @pytest.mark.asyncio
  async def test_rename_missing_session_raises(self, session_manager: SessionManager):
    with pytest.raises(SessionNotFoundError):
      await session_manager.rename("missing", "Name")


class TestSessionManagerTouch:
  """Tests for updating session activity."""

  @pytest.mark.asyncio
  async def test_touch_increments_message_count(
    self, session_manager: SessionManager
  ):
    created = await session_manager.create("sess-005", "gpt-4o")
    original_count = created.message_count

    await session_manager.touch("sess-005")
    await session_manager.touch("sess-005", message_count_delta=3)

    loaded = await session_manager.get("sess-005")
    assert loaded is not None
    assert loaded.message_count == original_count + 1 + 3
    assert loaded.last_active >= created.last_active


class TestSessionManagerDelete:
  """Tests for deleting sessions."""

  @pytest.mark.asyncio
  async def test_delete_removes_session(self, session_manager: SessionManager):
    await session_manager.create("sess-006", "gpt-4o")
    await session_manager.delete("sess-006")

    assert await session_manager.get("sess-006") is None

  @pytest.mark.asyncio
  async def test_delete_missing_session_raises(self, session_manager: SessionManager):
    with pytest.raises(SessionNotFoundError):
      await session_manager.delete("missing")

  @pytest.mark.asyncio
  async def test_delete_cleans_nonoka_checkpoint_rows(
    self, session_manager: SessionManager
  ):
    # Create nonoka checkpoint tables and insert dummy rows.
    conn = session_manager._ensure_connection()
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS checkpoints (
        session_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL
      )
      """
    )
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS step_updates (
        session_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        update_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (session_id, step_id, update_type)
      )
      """
    )
    conn.execute(
      "INSERT INTO checkpoints (session_id, state_json) VALUES (?, ?)",
      ("sess-007", "{}"),
    )
    conn.execute(
      "INSERT INTO step_updates (session_id, step_id, update_type, payload_json) VALUES (?, ?, ?, ?)",
      ("sess-007", "step-1", "status", "{}"),
    )
    conn.commit()

    await session_manager.create("sess-007", "gpt-4o")
    await session_manager.delete("sess-007")

    row = conn.execute(
      "SELECT 1 FROM checkpoints WHERE session_id = ?", ("sess-007",)
    ).fetchone()
    assert row is None


class TestSessionManagerContextManager:
  """Tests for async context manager support."""

  @pytest.mark.asyncio
  async def test_async_context_manager_closes_connection(self, temp_db_path: Path):
    async with SessionManager(db_path=temp_db_path) as manager:
      await manager.create("sess-008", "gpt-4o")

    assert manager._conn is None
