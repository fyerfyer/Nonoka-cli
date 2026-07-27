from __future__ import annotations

import json
import sys
from pathlib import Path

from nonoka_cli.benchmark.watchdog import (
  is_scorable_budget_exit,
  protected_harness_violations,
  run_with_watchdog,
)


def _event(**values: object) -> str:
  return json.dumps(values)


def _workspace_effect(*, changed: bool, policy_violations: list[str] | None = None) -> str:
  return _event(
    schema_version=1,
    kind="workspace_effect",
    source="host",
    tool_call_id="call-1",
    tool_name="arbitrary-host-tool",
    changed=changed,
    created=["answer.txt"] if changed else [],
    modified=[],
    deleted=[],
    before_digest="before",
    after_digest="after" if changed else "before",
    policy_violations=policy_violations or [],
  )


def _termination(reason: str) -> str:
  return _event(
    schema_version=1,
    kind="termination",
    source="bridge",
    reason=reason,
    termination={"reason": reason},
  )


def _task_effect(*, changed: bool) -> str:
  return _event(
    schema_version=1,
    kind="task_effect",
    source="host",
    tool_call_id="call-system",
    tool_name="terminal",
    changed=changed,
    scope="system",
    collector="test-host",
  )


def test_scorable_budget_exit_requires_observed_mutation_and_typed_budget_reason(
  tmp_path: Path,
):
  evidence = tmp_path / "run-evidence.ndjson"
  evidence.write_text("\n".join([
    _workspace_effect(changed=True),
    _termination("turn_budget_exhausted"),
  ]))

  assert is_scorable_budget_exit(evidence) is True


def test_scorable_budget_exit_rejects_uncontrolled_or_unmutated_failures(tmp_path: Path):
  uncontrolled = tmp_path / "uncontrolled.ndjson"
  uncontrolled.write_text("\n".join([
    _workspace_effect(changed=True),
    _termination("model_timeout"),
  ]))
  unmutated = tmp_path / "unmutated.ndjson"
  unmutated.write_text("\n".join([
    _workspace_effect(changed=False),
    _termination("turn_budget_exhausted"),
  ]))

  assert is_scorable_budget_exit(uncontrolled) is False
  assert is_scorable_budget_exit(unmutated) is False


def test_scorable_budget_exit_accepts_non_workspace_task_effect(tmp_path: Path):
  evidence = tmp_path / "system-effect.ndjson"
  evidence.write_text("\n".join([
    _task_effect(changed=True),
    _termination("turn_budget_exhausted"),
  ]))

  assert is_scorable_budget_exit(evidence) is True


def test_watchdog_converts_only_scorable_budget_exit_to_success(tmp_path: Path):
  log = tmp_path / "agent" / "opencode.txt"
  evidence = tmp_path / "agent" / "run-evidence.ndjson"
  evidence.parent.mkdir(parents=True)
  evidence.write_text("\n".join([
    _workspace_effect(changed=True),
    _termination("tool_budget_exhausted"),
  ]))

  return_code = run_with_watchdog(
    [sys.executable, "-c", "import sys; sys.exit(1)"],
    timeout_seconds=5,
    log_path=log,
    evidence_path=evidence,
    artifact_dir=tmp_path / "artifacts",
    allow_scorable_budget_exit=True,
  )

  assert return_code == 0
  assert json.loads((tmp_path / "agent" / "adapter-exit.json").read_text()) == {
    "classification": "scorable_budget_exit",
    "original_return_code": 1,
  }
  assert (tmp_path / "artifacts" / "adapter-exit.json").is_file()


def test_watchdog_rejects_protected_external_harness_mutation(tmp_path: Path):
  log = tmp_path / "agent" / "opencode.txt"
  evidence = tmp_path / "agent" / "run-evidence.ndjson"
  evidence.parent.mkdir(parents=True)
  evidence.write_text("\n".join([
    _workspace_effect(changed=True, policy_violations=["/tests"]),
    _termination("tool_budget_exhausted"),
  ]))

  assert protected_harness_violations(evidence) == ["/tests"]
  return_code = run_with_watchdog(
    [sys.executable, "-c", "import sys; sys.exit(1)"],
    timeout_seconds=5,
    log_path=log,
    evidence_path=evidence,
    artifact_dir=tmp_path / "artifacts",
    allow_scorable_budget_exit=True,
  )

  assert return_code == 2
  assert json.loads((tmp_path / "agent" / "adapter-exit.json").read_text()) == {
    "classification": "protected_harness_mutation",
    "original_return_code": 1,
    "violations": ["/tests"],
  }
  assert (tmp_path / "artifacts" / "adapter-exit.json").is_file()
