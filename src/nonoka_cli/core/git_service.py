"""Git checkpoint / rollback service for nonoka-cli.

This service wraps the nonoka-agent git tools and provides orchestration-level
helpers: deciding when to checkpoint, rolling back after failures, and listing
recent checkpoints.

The actual git commands are implemented in ``nonoka.tools.git``; this service
only handles policy (which tools trigger checkpoints, when to roll back).
"""

from __future__ import annotations

import shutil
import structlog
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nonoka.core.context import RunContext

from nonoka_cli.config.models import CLIConfig, GitConfig

logger = structlog.get_logger("nonoka_cli.core")

_WRITE_TOOLS = {
  "write_file",
  "edit_file",
  "search_and_replace",
  "delete_file",
}


class GitService:
  """Manages git checkpoints for a working directory."""

  def __init__(
    self,
    working_dir: Path,
    config: GitConfig | None = None,
  ):
    self._working_dir = working_dir
    self._config = config or GitConfig()
    self._last_checkpoint: str | None = None

  @property
  def enabled(self) -> bool:
    return self._config.enabled and shutil.which("git") is not None

  @property
  def working_dir(self) -> Path:
    return self._working_dir

  def is_git_repo(self) -> bool:
    """Return True if the working directory is inside a git repository."""
    git_dir = self._working_dir / ".git"
    if git_dir.exists():
      return True
    # Also accept nested repos where .git is a file (worktrees/submodules).
    try:
      import subprocess
      result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(self._working_dir),
        capture_output=True,
        text=True,
        check=False,
      )
      return result.returncode == 0
    except Exception:
      return False

  def should_checkpoint_before(self, tool_name: str) -> bool:
    """Return True if the given write tool should trigger a pre-checkpoint."""
    if not self.enabled or not self._config.auto_checkpoint:
      return False
    if not self.is_git_repo():
      return False
    return tool_name in _WRITE_TOOLS

  def should_checkpoint_after(self, tool_name: str) -> bool:
    """Return True if the given write tool should trigger a post-checkpoint.

    Committing after a write tool runs ensures that newly created files are
    tracked, so a subsequent rollback can remove them.
    """
    return self.should_checkpoint_before(tool_name)

  async def checkpoint_before(
    self,
    ctx: RunContext,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
  ) -> str | None:
    """Create a checkpoint before a write tool runs.

    Returns the checkpoint commit hash, or None if no checkpoint was created.
    """
    if not self.should_checkpoint_before(tool_name):
      return None

    try:
      from nonoka.tools.git import git_checkpoint
    except ImportError:
      logger.warning("git_tools_not_available")
      return None

    message = self._build_checkpoint_message(tool_name, arguments, phase="before")
    try:
      result = await git_checkpoint(ctx, message=message)
      if result.startswith("Error:"):
        logger.warning("git_checkpoint_failed", result=result)
        return None
      # Result format: "<hash> <message>"
      self._last_checkpoint = result.split()[0] if result.split() else None
      logger.info("git_checkpoint_created", result=result, hash=self._last_checkpoint)
      return self._last_checkpoint
    except Exception as exc:
      logger.warning("git_checkpoint_exception", error=str(exc))
      return None

  async def checkpoint_after(
    self,
    ctx: RunContext,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
  ) -> str | None:
    """Create a checkpoint after a write tool runs.

    This commits the actual file changes (including newly created files) so
    that ``rollback_last`` can restore the pre-change state precisely.

    Returns:
      The checkpoint commit hash, or None if no checkpoint was created.
    """
    if not self.should_checkpoint_after(tool_name):
      return None

    try:
      from nonoka.tools.git import git_checkpoint
    except ImportError:
      logger.warning("git_tools_not_available")
      return None

    message = self._build_checkpoint_message(tool_name, arguments, phase="after")
    try:
      result = await git_checkpoint(ctx, message=message)
      if result.startswith("Error:"):
        logger.warning("git_checkpoint_after_failed", result=result)
        return None
      checkpoint_hash = result.split()[0] if result.split() else None
      logger.info(
        "git_checkpoint_after_created",
        result=result,
        hash=checkpoint_hash,
        tool=tool_name,
      )
      return checkpoint_hash
    except Exception as exc:
      logger.warning("git_checkpoint_after_exception", error=str(exc))
      return None

  async def rollback_last(
    self,
    ctx: RunContext,
    steps: int = 1,
    to_hash: str | None = None,
    paths: list[str] | None = None,
  ) -> str | None:
    """Roll back to a previous checkpoint.

    Args:
      ctx: Tool execution context.
      steps: Number of checkpoint commits to roll back when *to_hash* is not
        provided.
      to_hash: Optional hash to reset to directly.
      paths: Optional list of relative paths to remove after the reset. Used
        to clean up files created by a failed tool that were never committed.

    Returns:
      The rollback result message, or None if rollback was skipped.
    """
    if not self.enabled or not self._config.rollback_on_error:
      return None
    if not self.is_git_repo():
      return None

    try:
      from nonoka.tools.git import git_rollback
    except ImportError:
      logger.warning("git_tools_not_available")
      return None

    try:
      if to_hash:
        result = await git_rollback(ctx, commit_hash=to_hash)
      else:
        result = await git_rollback(ctx, steps=steps)
      logger.info("git_rollback_executed", result=result, to_hash=to_hash, steps=steps)

      if paths:
        for rel_path in paths:
          target = self._working_dir / rel_path
          if target.exists():
            if target.is_dir():
              import shutil
              shutil.rmtree(target)
            else:
              target.unlink()
            logger.info("git_rollback_removed_path", path=rel_path)

      return result
    except Exception as exc:
      logger.warning("git_rollback_exception", error=str(exc))
      return None

  def _build_checkpoint_message(
    self,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    phase: str = "before",
  ) -> str:
    """Build a checkpoint commit message."""
    mode = self._config.commit_message
    prefix = f"nonoka checkpoint {phase}"
    if mode == "simple":
      return f"{prefix} {tool_name}"
    if mode == "auto":
      path = ""
      if arguments:
        for key in ("path", "file_path", "filePath", "file", "filename"):
          value = arguments.get(key)
          if value:
            path = value
            break
      if path:
        return f"{prefix} {tool_name} on {path}"
      return f"{prefix} {tool_name}"
    # Treat any other value as a template.
    try:
      return mode.format(
        tool_name=tool_name,
        arguments=arguments or {},
        phase=phase,
      )
    except Exception:
      return f"{prefix} {tool_name}"

  async def status_summary(self) -> str | None:
    """Return a concise git status summary, or None if not a git repo."""
    if not self.enabled or not self.is_git_repo():
      return None

    try:
      from nonoka.core.agent import Agent
      from nonoka.core.context import RunContext
      from nonoka.core.session import Session
      from nonoka.tools.git import git_status
    except ImportError:
      logger.warning("git_tools_not_available")
      return None

    agent = Agent(model="git-status")
    session = Session(
      session_id="git-status",
      agent=agent,
      deps=SimpleNamespace(working_dir=str(self._working_dir)),
    )
    try:
      return await git_status(RunContext(session))
    except Exception as exc:
      logger.warning("git_status_failed", error=str(exc))
      return None


def build_git_service(working_dir: Path, config: CLIConfig | None = None) -> GitService:
  """Factory for creating a GitService from CLI configuration."""
  git_config = config.git if config is not None else GitConfig()
  return GitService(working_dir=working_dir, config=git_config)
