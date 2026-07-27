"""Release-candidate manifests with lane-separated evaluation outcomes."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from nonoka_cli.bridge.protocol import BRIDGE_CAPABILITIES, BRIDGE_PROTOCOL_VERSION

LaneStatus = Literal["pending", "passed", "failed", "blocked", "invalid", "stopped"]


class RuntimeBudgets(BaseModel):
  max_turns: int | None = None
  tool_budget: int | None = None
  timeout_seconds: float | None = None
  wall_timeout_seconds: float | None = None
  max_context_bytes: int | None = None
  max_cost_usd: float | None = None


class LaneOutcome(BaseModel):
  status: LaneStatus = "pending"
  artifact: str | None = None
  passed: int | None = None
  failed: int | None = None
  invalid: int | None = None
  notes: str = ""


class ReleaseScorecard(BaseModel):
  """A fixed release manifest whose outcomes remain separated by lane."""

  schema_version: Literal[1, 2] = 2
  created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
  release_candidate: str
  revisions: dict[str, str]
  runtime: dict[str, str]
  versions: dict[str, str] = Field(default_factory=dict)
  protocol: dict[str, object]
  model: str
  temperature: float
  budgets: RuntimeBudgets
  sample_ids: list[str]
  verifier: str
  artifact_root: str
  deterministic_regression: LaneOutcome
  framework_diagnostic: LaneOutcome
  opencode_end_to_end: LaneOutcome

  def write(self, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
      raise FileExistsError(f"Scorecard already exists: {path}")
    path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


def build_scorecard(
  *,
  release_candidate: str,
  cli_root: Path,
  framework_root: Path,
  model: str,
  temperature: float,
  budgets: RuntimeBudgets,
  sample_ids: list[str],
  verifier: str,
  artifact_root: Path,
  deterministic: LaneOutcome,
  framework: LaneOutcome,
  opencode: LaneOutcome,
) -> ReleaseScorecard:
  return ReleaseScorecard(
    release_candidate=release_candidate,
    revisions={
      "nonoka_cli": git_revision(cli_root),
      "nonoka_agent": git_revision(framework_root),
      "nonoka_opencode_provider": git_revision(cli_root),
    },
    runtime={"python": sys.version.split()[0], "platform": platform.platform()},
    versions={
      "nonoka_cli": _distribution_version("nonoka-cli"),
      "nonoka_agent": _distribution_version("nonoka"),
      "nonoka_opencode_provider": _provider_version(cli_root),
    },
    protocol={
      "version": BRIDGE_PROTOCOL_VERSION,
      "capabilities": sorted(BRIDGE_CAPABILITIES),
    },
    model=model,
    temperature=temperature,
    budgets=budgets,
    sample_ids=sample_ids,
    verifier=verifier,
    artifact_root=str(artifact_root.resolve()),
    deterministic_regression=deterministic,
    framework_diagnostic=framework,
    opencode_end_to_end=opencode,
  )


def _distribution_version(distribution: str) -> str:
  try:
    return importlib.metadata.version(distribution)
  except importlib.metadata.PackageNotFoundError:
    return "unknown"


def _provider_version(cli_root: Path) -> str:
  package = cli_root / "packages" / "nonoka-opencode-provider" / "package.json"
  try:
    value = json.loads(package.read_text(encoding="utf-8")).get("version")
    return str(value) if value else "unknown"
  except (OSError, ValueError):
    return "unknown"


def git_revision(repo: Path) -> str:
  """Resolve a repository revision without making scorecard creation fragile."""
  try:
    return subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=repo,
      capture_output=True,
      text=True,
      check=True,
    ).stdout.strip()
  except (OSError, subprocess.SubprocessError):
    return "unknown"


def read_lane_outcome(path: Path) -> LaneOutcome:
  """Load a lane outcome JSON file produced by CI or a benchmark wrapper."""
  return LaneOutcome.model_validate(json.loads(path.read_text(encoding="utf-8")))
