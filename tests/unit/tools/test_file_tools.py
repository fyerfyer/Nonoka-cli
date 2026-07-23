"""Behavioral tests for built-in filesystem and shell tools."""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nonoka_cli.tools.builtins.file_tools import execute_command


@pytest.mark.asyncio
async def test_execute_command_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
  """A shell timeout must not leave children that mutate the workspace later."""
  survivor = tmp_path / "survivor.txt"
  child_code = (
    "import pathlib, sys, time; "
    "time.sleep(0.5); pathlib.Path(sys.argv[1]).write_text('still running')"
  )
  parent_code = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    + repr(child_code)
    + ", sys.argv[1]]); time.sleep(60)"
  )
  command = " ".join(
    [
      shlex.quote(sys.executable),
      "-c",
      shlex.quote(parent_code),
      shlex.quote(str(survivor)),
    ]
  )
  ctx = SimpleNamespace(deps=SimpleNamespace(working_dir=tmp_path))

  result = await execute_command._func(ctx, command=command, timeout=0.1)

  assert result == "Error: command timed out after 0.1s"
  await asyncio.sleep(0.7)
  assert not survivor.exists()
