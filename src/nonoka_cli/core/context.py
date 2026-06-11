"""CLI runtime context passed to tools via RunContext.deps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nonoka_cli.config.models import CLIConfig


@dataclass
class CLIContext:
  """CLI runtime context injected into tools."""
  user: str
  session_id: str
  config: CLIConfig
  working_dir: Path
