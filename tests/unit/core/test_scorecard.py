from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonoka_cli.core.scorecard import (
  LaneOutcome,
  RuntimeBudgets,
  build_scorecard,
  read_lane_outcome,
)


def test_scorecard_keeps_release_lanes_separate(tmp_path: Path, monkeypatch):
  monkeypatch.setattr("nonoka_cli.core.scorecard.git_revision", lambda path: path.name)
  scorecard = build_scorecard(
    release_candidate="0.3.0-rc1",
    cli_root=tmp_path / "nonoka-cli",
    framework_root=tmp_path / "nonoka-agent",
    model="provider/model",
    temperature=0.0,
    budgets=RuntimeBudgets(max_turns=20, tool_budget=40),
    sample_ids=["repair-1", "shell-1"],
    verifier="official-harbor",
    artifact_root=tmp_path / "artifacts",
    deterministic=LaneOutcome(status="passed", passed=230, failed=0),
    framework=LaneOutcome(status="failed", passed=19, failed=1),
    opencode=LaneOutcome(status="blocked", invalid=1),
  )
  data = scorecard.model_dump(mode="json")
  assert "aggregate" not in data
  assert data["deterministic_regression"]["passed"] == 230
  assert data["framework_diagnostic"]["failed"] == 1
  assert data["opencode_end_to_end"]["status"] == "blocked"
  assert data["sample_ids"] == ["repair-1", "shell-1"]
  assert data["schema_version"] == 2
  assert "versions" in data


def test_scorecard_refuses_to_replace_an_existing_manifest(tmp_path: Path):
  scorecard = build_scorecard(
    release_candidate="0.3.0-rc1",
    cli_root=tmp_path,
    framework_root=tmp_path,
    model="provider/model",
    temperature=0.0,
    budgets=RuntimeBudgets(),
    sample_ids=["repair-1"],
    verifier="local",
    artifact_root=tmp_path,
    deterministic=LaneOutcome(),
    framework=LaneOutcome(),
    opencode=LaneOutcome(),
  )
  output = tmp_path / "scorecard.json"
  scorecard.write(output)
  with pytest.raises(FileExistsError):
    scorecard.write(output)


def test_read_lane_outcome(tmp_path: Path):
  path = tmp_path / "lane.json"
  path.write_text(json.dumps({"status": "passed", "passed": 3, "failed": 0}))
  assert read_lane_outcome(path).passed == 3
