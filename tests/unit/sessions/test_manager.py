import pytest

from nonoka_cli.sessions.manager import (
  SessionManager,
  project_event_db_path,
  project_session_db_path,
)
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.utils.errors import SessionNotFoundError


@pytest.fixture
async def session_manager(tmp_path):
  db_path = tmp_path / "sessions.db"
  manager = SessionManager(db_path=db_path)
  yield manager
  await manager.close()


async def test_create_and_get(session_manager):
  info = await session_manager.create("sess-1", "gpt-4o", name="test")
  assert isinstance(info, SessionInfo)
  assert info.session_id == "sess-1"

  loaded = await session_manager.get("sess-1")
  assert loaded is not None
  assert loaded.name == "test"
  assert loaded.model == "gpt-4o"


async def test_get_missing(session_manager):
  assert await session_manager.get("missing") is None


async def test_rename(session_manager):
  await session_manager.create("sess-1", "gpt-4o")
  renamed = await session_manager.rename("sess-1", "new name")
  assert renamed.name == "new name"


async def test_rename_missing(session_manager):
  with pytest.raises(SessionNotFoundError):
    await session_manager.rename("missing", "x")


async def test_delete(session_manager):
  await session_manager.create("sess-1", "gpt-4o")
  await session_manager.delete("sess-1")
  assert await session_manager.get("sess-1") is None


async def test_delete_missing(session_manager):
  with pytest.raises(SessionNotFoundError):
    await session_manager.delete("missing")


async def test_list_ordered_by_last_active(session_manager):
  await session_manager.create("a", "gpt-4o")
  await session_manager.create("b", "gpt-4o")
  await session_manager.touch("a")
  sessions = await session_manager.list()
  assert [s.session_id for s in sessions] == ["a", "b"]


def test_project_database_paths_are_isolated_per_workspace(tmp_path):
  first = tmp_path / "first"
  second = tmp_path / "second"

  assert project_session_db_path(first) == first / ".nonoka" / "sessions.db"
  assert project_event_db_path(first) == first / ".nonoka" / "events.db"
  assert project_session_db_path(first) != project_session_db_path(second)


async def test_session_manager_enables_wal_and_busy_timeout(session_manager):
  await session_manager.list()
  conn = session_manager._conn
  assert conn is not None
  assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
  assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
