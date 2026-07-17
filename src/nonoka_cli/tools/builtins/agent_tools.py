"""Agent-level tools re-exported from nonoka-agent.

These tools are implemented in nonoka-agent (the core agent framework) and are
made available to every nonoka-cli Agent. Keeping the implementation in
nonoka-agent lets other consumers reuse them, while this module declares the
subset the CLI exposes by default.
"""

from __future__ import annotations

from nonoka.core.types import Capability
from nonoka.tools import (
  build_repo_map,
  git_checkpoint,
  git_rollback,
  git_status,
  lsp_document_symbols,
  search_repo_map,
)


def get_tools() -> list[Capability]:
  """Return agent-level tools exposed by nonoka-cli."""
  return [
    git_checkpoint,
    git_rollback,
    git_status,
    build_repo_map,
    search_repo_map,
    lsp_document_symbols,
  ]
