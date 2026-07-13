"""Repo map service for nonoka-cli.

Wraps the nonoka-agent repo-map tools and adapts them to CLI configuration:
- honours ``repo_map.enabled`` / ``max_tokens``
- pre-builds the map on demand for system-prompt injection
- keeps the symbol index implementation in nonoka-agent
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

from nonoka.core.agent import Agent
from nonoka.core.context import RunContext
from nonoka.core.session import Session
from nonoka.tools.repo_map import build_repo_map, search_repo_map

from nonoka_cli.config.models import CLIConfig, RepoMapConfig

logger = structlog.get_logger("nonoka_cli.core")


class RepoMapService:
  """Builds and searches a hierarchical repo map for the working directory."""

  def __init__(self, working_dir: Path, config: RepoMapConfig | None = None):
    self._working_dir = working_dir
    self._config = config or RepoMapConfig()

  @property
  def enabled(self) -> bool:
    return self._config.enabled

  @property
  def working_dir(self) -> Path:
    return self._working_dir

  async def build(self, path: str = ".", force_refresh: bool = False) -> str:
    """Return the formatted repo map, or an empty string if disabled."""
    if not self.enabled:
      return ""

    ctx = self._run_context()
    try:
      return await build_repo_map(
        ctx,
        path=path,
        max_tokens=self._config.max_tokens,
        force_refresh=force_refresh,
        lsp_languages=self._config.lsp_languages,
      )
    except Exception as exc:
      logger.warning("repo_map_build_failed", error=str(exc))
      return ""

  async def search(self, query: str, max_results: int = 10) -> str:
    """Search the cached repo map for symbols or files matching *query*."""
    if not self.enabled:
      return "Repo map is disabled."

    ctx = self._run_context()
    try:
      return await search_repo_map(ctx, query=query, max_results=max_results)
    except Exception as exc:
      logger.warning("repo_map_search_failed", error=str(exc))
      return f"Error searching repo map: {exc}"

  async def build_system_prompt_block(self, path: str = ".") -> str | None:
    """Return a repo-map block suitable for injection into the system prompt.

    Returns ``None`` when repo mapping is disabled or produces no output.
    """
    if not self.enabled:
      return None

    map_text = await self.build(path=path)
    if not map_text or map_text.startswith("Path not found"):
      return None

    return (
      "## Repository Map\n"
      "Use this overview to locate relevant files and symbols before reading or editing.\n\n"
      f"{map_text}"
    )

  def _run_context(self) -> RunContext:
    """Build a minimal RunContext whose deps expose ``working_dir``."""
    # Repo-map tools only need deps.working_dir, so a minimal Session is enough.
    agent = Agent(model="repo-map")
    session = Session(
      session_id="repo-map",
      agent=agent,
      deps=_RepoMapDeps(working_dir=str(self._working_dir)),
    )
    return RunContext(session)


class _RepoMapDeps:
  def __init__(self, working_dir: str):
    self.working_dir = working_dir


def build_repo_map_service(
  working_dir: Path,
  config: CLIConfig | None = None,
) -> RepoMapService:
  """Factory for creating a RepoMapService from CLI configuration."""
  repo_config = config.repo_map if config is not None else RepoMapConfig()
  return RepoMapService(working_dir=working_dir, config=repo_config)
