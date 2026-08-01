"""Session index manager.

Maintains the ``cli_sessions`` SQLite table alongside nonoka's checkpoint
tables, providing session metadata, listing, and lifecycle operations.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from nonoka.backends.checkpoint.sqlite import SQLiteCheckpointStore

from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.utils.errors import SessionNotFoundError

logger = structlog.get_logger("nonoka_cli.sessions")


_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "nonoka"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "nonoka.db"


def project_session_db_path(working_dir: Path | str) -> Path:
  """Return the session/checkpoint database owned by one workspace.

  The OpenCode bridge is long-lived and several projects can be open at once.
  Keeping their state below each project avoids cross-project SQLite writer
  contention and prevents session history leaking into another project's TUI.
  """
  return Path(working_dir).expanduser().resolve() / ".nonoka" / "sessions.db"


def project_event_db_path(working_dir: Path | str) -> Path:
  """Return the structured-event database owned by one workspace."""
  return Path(working_dir).expanduser().resolve() / ".nonoka" / "events.db"


class SessionManager:
  """Manages the CLI session index table in SQLite.

  Uses a dedicated sqlite3 connection to the same database file that
  nonoka's ``SQLiteCheckpointStore`` uses. This lets the CLI maintain its
  own metadata while reusing nonoka's checkpoint persistence for message
  context.

  All synchronous database operations are wrapped in ``asyncio.to_thread``
  so the public API is async-friendly.
  """

  def __init__(self, db_path: Path | str | None = None) -> None:
    """Initialize the session manager.

    Args:
      db_path: Path to the SQLite database. Defaults to
        ``~/.local/share/nonoka/nonoka.db``.
    """
    self._db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
    self._conn: sqlite3.Connection | None = None
    self._lock = asyncio.Lock()

  @property
  def db_path(self) -> Path:
    """Resolved database path."""
    return self._db_path

  def _ensure_connection(self) -> sqlite3.Connection:
    """Return an open connection, creating tables if necessary."""
    if self._conn is None:
      self._db_path.parent.mkdir(parents=True, exist_ok=True)
      self._conn = sqlite3.connect(
        str(self._db_path), check_same_thread=False, timeout=10.0
      )
      self._conn.row_factory = sqlite3.Row
      # WAL permits readers while a writer is active; busy_timeout handles the
      # short writes from another OpenCode window instead of failing startup.
      self._conn.execute("PRAGMA busy_timeout = 10000")
      self._conn.execute("PRAGMA journal_mode = WAL")
      self._conn.execute("PRAGMA synchronous = NORMAL")
      self._create_tables()
    return self._conn

  def _create_tables(self) -> None:
    """Create the cli_sessions index table if it does not exist."""
    conn = self._conn
    assert conn is not None
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS cli_sessions (
        session_id TEXT PRIMARY KEY,
        name TEXT,
        model TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        message_count INTEGER DEFAULT 0,
        metadata TEXT DEFAULT '{}'
      )
      """
    )
    conn.execute(
      """
      CREATE INDEX IF NOT EXISTS idx_cli_sessions_last_active
      ON cli_sessions(last_active DESC)
      """
    )
    conn.commit()

  # ------------------------------------------------------------------ #
  # Lifecycle
  # ------------------------------------------------------------------ #

  async def close(self) -> None:
    """Close the database connection."""
    if self._conn is not None:

      def _close() -> None:
        self._conn.close()

      await asyncio.to_thread(_close)
      self._conn = None

  async def __aenter__(self) -> SessionManager:
    return self

  async def __aexit__(self, *args: Any) -> None:
    await self.close()

  # ------------------------------------------------------------------ #
  # CRUD operations
  # ------------------------------------------------------------------ #

  async def create(
    self,
    session_id: str,
    model: str,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
  ) -> SessionInfo:
    """Insert a new session record.

    Args:
      session_id: Global unique session identifier.
      model: Model identifier used to create the session.
      name: Optional human-readable session name.
      metadata: Optional JSON-serializable metadata dict.

    Returns:
      The newly created SessionInfo.
    """
    now = datetime.now()
    info = SessionInfo(
      session_id=session_id,
      name=name,
      model=model,
      created_at=now,
      last_active=now,
      message_count=0,
      metadata=metadata or {},
    )

    def _insert() -> None:
      conn = self._ensure_connection()
      conn.execute(
        """
        INSERT INTO cli_sessions
          (session_id, name, model, created_at, last_active, message_count, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
          info.session_id,
          info.name,
          info.model,
          info.created_at.isoformat(),
          info.last_active.isoformat(),
          info.message_count,
          json.dumps(info.metadata),
        ),
      )
      conn.commit()

    async with self._lock:
      await asyncio.to_thread(_insert)

    logger.info("session_created", session_id=session_id, name=name, model=model)
    return info

  async def get(self, session_id: str) -> SessionInfo | None:
    """Return session metadata, or None if not found."""

    def _get() -> sqlite3.Row | None:
      conn = self._ensure_connection()
      return conn.execute(
        "SELECT * FROM cli_sessions WHERE session_id = ?",
        (session_id,),
      ).fetchone()

    async with self._lock:
      row = await asyncio.to_thread(_get)

    if row is None:
      return None
    return self._row_to_info(row)

  async def list(self) -> list[SessionInfo]:
    """Return all sessions ordered by ``last_active`` descending."""

    def _list() -> list[sqlite3.Row]:
      conn = self._ensure_connection()
      return conn.execute(
        "SELECT * FROM cli_sessions ORDER BY last_active DESC"
      ).fetchall()

    async with self._lock:
      rows = await asyncio.to_thread(_list)

    return [self._row_to_info(row) for row in rows]

  async def rename(self, session_id: str, name: str) -> SessionInfo:
    """Rename an existing session.

    Args:
      session_id: Session to rename.
      name: New human-readable name.

    Returns:
      Updated SessionInfo.

    Raises:
      SessionNotFoundError: If the session does not exist.
    """

    def _update() -> None:
      conn = self._ensure_connection()
      cursor = conn.execute(
        """
        UPDATE cli_sessions
        SET name = ?, last_active = ?
        WHERE session_id = ?
        """,
        (name, datetime.now().isoformat(), session_id),
      )
      if cursor.rowcount == 0:
        raise SessionNotFoundError(f"Session not found: {session_id}")
      conn.commit()

    async with self._lock:
      await asyncio.to_thread(_update)

    logger.info("session_renamed", session_id=session_id, name=name)
    info = await self.get(session_id)
    assert info is not None
    return info

  async def delete(self, session_id: str) -> None:
    """Delete a session and its checkpoint data.

    Uses nonoka's ``SQLiteCheckpointStore.delete_session`` to clean up the
    checkpoint tables owned by the framework.

    Args:
      session_id: Session to delete.

    Raises:
      SessionNotFoundError: If the session does not exist in the index.
    """

    def _delete() -> None:
      conn = self._ensure_connection()
      cursor = conn.execute(
        "DELETE FROM cli_sessions WHERE session_id = ?",
        (session_id,),
      )
      if cursor.rowcount == 0:
        raise SessionNotFoundError(f"Session not found: {session_id}")
      conn.commit()

    async with self._lock:
      await asyncio.to_thread(_delete)

    # Clean up nonoka checkpoint data through the public CheckpointStore API.
    try:
      store = SQLiteCheckpointStore(db_path=str(self._db_path))
      await store.delete_session(session_id)
      await store.close()
    except Exception as exc:
      logger.warning(
        "checkpoint_cleanup_failed",
        session_id=session_id,
        error=str(exc),
      )

    logger.info("session_deleted", session_id=session_id)

  async def touch(self, session_id: str, message_count_delta: int = 1) -> None:
    """Update last_active and increment message_count.

    Args:
      session_id: Session to touch.
      message_count_delta: Amount to add to message_count (default 1).
    """

    def _touch() -> None:
      conn = self._ensure_connection()
      conn.execute(
        """
        UPDATE cli_sessions
        SET last_active = ?, message_count = message_count + ?
        WHERE session_id = ?
        """,
        (datetime.now().isoformat(), message_count_delta, session_id),
      )
      conn.commit()

    async with self._lock:
      await asyncio.to_thread(_touch)

  # ------------------------------------------------------------------ #
  # Helpers
  # ------------------------------------------------------------------ #

  @staticmethod
  def _row_to_info(row: sqlite3.Row) -> SessionInfo:
    """Convert a sqlite3 row to SessionInfo."""
    return SessionInfo(
      session_id=row["session_id"],
      name=row["name"],
      model=row["model"],
      created_at=datetime.fromisoformat(row["created_at"]),
      last_active=datetime.fromisoformat(row["last_active"]),
      message_count=row["message_count"],
      metadata=json.loads(row["metadata"] or "{}"),
    )
