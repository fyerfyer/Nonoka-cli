"""Tests for the run command that launches the OpenCode TUI."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from nonoka_cli.commands import run_cmd


def test_run_missing_opencode_returns_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: False)

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)
    ret = run_cmd.launch_tui(args)

    assert ret == 1


def test_run_auto_initializes_opencode_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    with patch.object(run_cmd, "cmd_init", return_value=0) as mock_init:
        with patch.object(run_cmd, "subprocess") as mock_subprocess:
            proc = MagicMock()
            proc.returncode = 0
            mock_subprocess.run.return_value = proc

            ret = run_cmd.launch_tui(args)

    assert ret == 0
    mock_init.assert_called_once()
    mock_subprocess.run.assert_called_once_with(
        ["opencode", str(tmp_path)],
        cwd=tmp_path,
    )


def test_run_skips_init_when_config_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    (tmp_path / "opencode.json").write_text("{}")

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    with patch.object(run_cmd, "cmd_init") as mock_init:
        with patch.object(run_cmd, "subprocess") as mock_subprocess:
            proc = MagicMock()
            proc.returncode = 0
            mock_subprocess.run.return_value = proc

            ret = run_cmd.launch_tui(args)

    assert ret == 0
    mock_init.assert_not_called()
    mock_subprocess.run.assert_called_once_with(
        ["opencode", str(tmp_path)],
        cwd=tmp_path,
    )


def test_run_one_shot_message_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    (tmp_path / "opencode.json").write_text("{}")

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message="hello")

    with patch.object(run_cmd, "cmd_init") as mock_init:
        with patch.object(run_cmd, "subprocess") as mock_subprocess:
            proc = MagicMock()
            proc.returncode = 0
            mock_subprocess.run.return_value = proc

            ret = run_cmd.launch_tui(args)

    assert ret == 0
    mock_init.assert_not_called()
    mock_subprocess.run.assert_called_once_with(
        ["opencode", "run", "--auto", "hello"],
        cwd=tmp_path,
    )


def test_run_propagates_init_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    with patch.object(run_cmd, "cmd_init", return_value=1) as mock_init:
        with patch.object(run_cmd, "subprocess") as mock_subprocess:
            ret = run_cmd.launch_tui(args)

    assert ret == 1
    mock_init.assert_called_once()
    mock_subprocess.run.assert_not_called()


def test_run_keyboard_interrupt_returns_130(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    (tmp_path / "opencode.json").write_text("{}")

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    with patch.object(run_cmd, "subprocess") as mock_subprocess:
        mock_subprocess.run.side_effect = KeyboardInterrupt()

        ret = run_cmd.launch_tui(args)

    assert ret == 130
