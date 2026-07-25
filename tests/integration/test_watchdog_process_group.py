from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nonoka_cli.benchmark.watchdog import run_with_watchdog


@pytest.mark.integration
def test_watchdog_terminates_command_process_group(tmp_path: Path):
  terminated = tmp_path / "child-terminated"
  child = (
    "import signal,time,pathlib,sys; "
    "p=pathlib.Path(sys.argv[1]); "
    "signal.signal(signal.SIGTERM, lambda *_: (p.write_text('term'), sys.exit(0))); "
    "time.sleep(60)"
  )
  parent = (
    "import subprocess,sys,time; "
    "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
    "time.sleep(60)"
  )
  return_code = run_with_watchdog(
    [sys.executable, "-c", parent, child, str(terminated)],
    timeout_seconds=0.5,
    grace_seconds=1.0,
    log_path=tmp_path / "agent" / "opencode.txt",
    artifact_dir=tmp_path / "artifacts",
  )
  assert return_code == 124
  assert terminated.read_text() == "term"
  assert (tmp_path / "artifacts" / "opencode.txt").exists()
