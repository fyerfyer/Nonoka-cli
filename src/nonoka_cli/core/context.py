"""CLI runtime context passed to tools via RunContext.deps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonoka_cli.config.models import CLIConfig


@dataclass
class CLIContext:
  """CLI runtime context injected into tools.

  Services that are not yet initialized (e.g. during early tests) are kept as
  ``Any`` to avoid circular imports.
  """
  user: str
  session_id: str
  config: CLIConfig
  working_dir: Path
  task_state_service: Any = field(default=None, repr=False)
  skill_manager: Any = field(default=None, repr=False)
  mcp_manager: Any = field(default=None, repr=False)
  git_service: Any = field(default=None, repr=False)
  repo_map_service: Any = field(default=None, repr=False)
  plugin_manifests: list[Any] = field(default_factory=list, repr=False)
  safety_policy: Any = field(default=None, repr=False)
