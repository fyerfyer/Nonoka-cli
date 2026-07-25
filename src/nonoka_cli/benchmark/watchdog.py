"""Run a command in an isolated process group with a hard TERM/KILL deadline."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from nonoka.core.runtime import TerminalReason
from nonoka_cli.core.run_evidence import (
  TerminationEvidence,
  TaskEffectEvidence,
  WorkspaceEffectEvidence,
  read_run_evidence,
)

_SCORABLE_REASONS = {
  TerminalReason.TURN_BUDGET_EXHAUSTED.value,
  TerminalReason.TOOL_BUDGET_EXHAUSTED.value,
}


def is_scorable_budget_exit(evidence_path: Path) -> bool:
  """Return whether a failed OpenCode run should still reach the verifier.

  A resource-budget exit is scorable only after OpenCode has completed an
  explicit host file mutation. The verifier, rather than the adapter, then
  decides whether that partial or complete workspace state is correct.
  """
  saw_mutation = False
  saw_budget_error = False
  for event in read_run_evidence(evidence_path):
    if isinstance(event, (WorkspaceEffectEvidence, TaskEffectEvidence)) and event.changed:
      saw_mutation = True
    elif isinstance(event, TerminationEvidence) and event.reason in _SCORABLE_REASONS:
      saw_budget_error = True

  return saw_mutation and saw_budget_error


def run_with_watchdog(
  command: list[str],
  *,
  timeout_seconds: float,
  grace_seconds: float = 5.0,
  log_path: Path | None = None,
  evidence_path: Path | None = None,
  artifact_dir: Path | None = None,
  allow_scorable_budget_exit: bool = False,
) -> int:
  if not command:
    raise ValueError("watchdog command must not be empty")
  if log_path is not None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
  stream = log_path.open("ab") if log_path is not None else None
  try:
    process = subprocess.Popen(
      command,
      stdin=subprocess.DEVNULL,
      stdout=stream or sys.stdout.buffer,
      stderr=subprocess.STDOUT,
      start_new_session=True,
    )
    try:
      return_code = process.wait(timeout=timeout_seconds)
      if (
        return_code != 0
        and allow_scorable_budget_exit
        and evidence_path is not None
        and log_path is not None
      ):
        if stream is not None:
          stream.flush()
        if is_scorable_budget_exit(evidence_path):
          status_path = log_path.parent / "adapter-exit.json"
          status_path.write_text(json.dumps({
            "classification": "scorable_budget_exit",
            "original_return_code": return_code,
          }) + "\n")
          return 0
      return return_code
    except subprocess.TimeoutExpired:
      os.killpg(process.pid, signal.SIGTERM)
      try:
        process.wait(timeout=grace_seconds)
      except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
      print(json.dumps({
        "type": "watchdog_timeout",
        "timeout_seconds": timeout_seconds,
        "grace_seconds": grace_seconds,
        "pid": process.pid,
      }), file=sys.stderr)
      return 124
  finally:
    if stream is not None:
      stream.close()
    if artifact_dir is not None and log_path is not None and log_path.parent.exists():
      artifact_dir.mkdir(parents=True, exist_ok=True)
      shutil.copytree(log_path.parent, artifact_dir, dirs_exist_ok=True)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--timeout", type=float, required=True)
  parser.add_argument("--grace", type=float, default=5.0)
  parser.add_argument("--log", type=Path)
  parser.add_argument("--evidence-log", type=Path)
  parser.add_argument("--artifact-dir", type=Path)
  parser.add_argument("--allow-scorable-budget-exit", action="store_true")
  parser.add_argument("command", nargs=argparse.REMAINDER)
  args = parser.parse_args(argv)
  command = list(args.command)
  if command and command[0] == "--":
    command = command[1:]
  return run_with_watchdog(
    command,
    timeout_seconds=args.timeout,
    grace_seconds=args.grace,
    log_path=args.log,
    evidence_path=args.evidence_log,
    artifact_dir=args.artifact_dir,
    allow_scorable_budget_exit=args.allow_scorable_budget_exit,
  )


if __name__ == "__main__":
  raise SystemExit(main())
