"""Cross-process evidence journal for externally hosted agent runs."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

EVIDENCE_ENV = "NONOKA_RUN_EVIDENCE_PATH"


class WorkspaceEffectEvidence(BaseModel):
  schema_version: Literal[1] = 1
  kind: Literal["workspace_effect"] = "workspace_effect"
  source: str
  tool_call_id: str
  tool_name: str = ""
  changed: bool
  created: list[str] = Field(default_factory=list)
  modified: list[str] = Field(default_factory=list)
  deleted: list[str] = Field(default_factory=list)
  policy_violations: list[str] = Field(default_factory=list)
  restored_paths: list[str] = Field(default_factory=list)
  before_digest: str
  after_digest: str


class TerminationEvidence(BaseModel):
  schema_version: Literal[1] = 1
  kind: Literal["termination"] = "termination"
  source: str
  reason: str
  finish_reason: str | None = None
  termination: dict[str, Any] = Field(default_factory=dict)


class TaskEffectEvidence(BaseModel):
  schema_version: Literal[1] = 1
  kind: Literal["task_effect"] = "task_effect"
  source: str
  tool_call_id: str
  tool_name: str = ""
  changed: bool
  scope: str = "external"
  collector: str = "host"
  summary: str | None = None
  policy_violations: list[str] = Field(default_factory=list)


class VerificationEvidence(BaseModel):
  schema_version: Literal[1] = 1
  kind: Literal["verification"] = "verification"
  source: str
  tool_call_id: str
  tool_name: str = ""
  receipt: dict[str, Any]


RunEvidence = (
  WorkspaceEffectEvidence | TaskEffectEvidence | VerificationEvidence | TerminationEvidence
)


def append_run_evidence(event: RunEvidence, path: Path | None = None) -> None:
  """Append one durable evidence event without affecting the agent stream."""
  target = path or _configured_path()
  if target is None:
    return
  try:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
      stream.write(event.model_dump_json(exclude_none=True) + "\n")
  except OSError:
    pass


def read_run_evidence(path: Path) -> Iterable[RunEvidence]:
  """Yield valid journal entries while ignoring partial or unknown records."""
  try:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
  except OSError:
    return
  for line in lines:
    try:
      value = json.loads(line)
      kind = value.get("kind") if isinstance(value, dict) else None
      if kind == "workspace_effect":
        yield WorkspaceEffectEvidence.model_validate(value)
      elif kind == "task_effect":
        yield TaskEffectEvidence.model_validate(value)
      elif kind == "verification":
        yield VerificationEvidence.model_validate(value)
      elif kind == "termination":
        yield TerminationEvidence.model_validate(value)
    except (ValueError, TypeError):
      continue


def _configured_path() -> Path | None:
  value = os.environ.get(EVIDENCE_ENV)
  return Path(value) if value else None
