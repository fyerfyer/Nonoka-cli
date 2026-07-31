"""Tests for the run command that launches the OpenCode TUI."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nonoka_cli.commands import run_cmd
from nonoka_cli.config.models import CLIConfig, SafetyConfig


def _unsandboxed_test_config() -> CLIConfig:
    return CLIConfig(safety=SafetyConfig(enabled=False, sandbox="disabled"))


@pytest.fixture(autouse=True)
def _stable_readiness(monkeypatch):
    monkeypatch.setattr(run_cmd, "_repo_state", lambda _cwd: "clean")


def _write_ready_project(tmp_path: Path) -> None:
    config_path = tmp_path / "nonoka.yaml"
    config_path.write_text("model: deepseek-chat\nsafety:\n  enabled: false\n")
    (tmp_path / "opencode.json").write_text(json.dumps({
        "model": "nonoka/default",
        "provider": {
            "nonoka": {
                "options": {
                    "configPath": str(config_path),
                    "serverCommand": [sys.executable, "-m", "nonoka_cli", "--server"],
                }
            }
        },
    }))
    package = tmp_path / "node_modules" / "nonoka-opencode-provider"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "0.2.16"}))


def test_run_missing_opencode_returns_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: False)

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)
    ret = run_cmd.launch_tui(args)

    assert ret == 1


def test_run_auto_initializes_opencode_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    monkeypatch.setattr(run_cmd.ConfigLoader, "load", lambda *_: _unsandboxed_test_config())

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    def initialize(*_args):
        _write_ready_project(tmp_path)
        return 0

    with patch.object(run_cmd, "cmd_init", side_effect=initialize) as mock_init:
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
        env=mock_subprocess.run.call_args.kwargs["env"],
    )
    assert mock_subprocess.run.call_args.kwargs["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "1"


def test_run_skips_init_when_config_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    monkeypatch.setattr(run_cmd.ConfigLoader, "load", lambda *_: _unsandboxed_test_config())
    _write_ready_project(tmp_path)

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
        env=mock_subprocess.run.call_args.kwargs["env"],
    )


def test_run_one_shot_message_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    monkeypatch.setattr(run_cmd.ConfigLoader, "load", lambda *_: _unsandboxed_test_config())
    _write_ready_project(tmp_path)

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
        env=mock_subprocess.run.call_args.kwargs["env"],
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
    monkeypatch.setattr(run_cmd.ConfigLoader, "load", lambda *_: _unsandboxed_test_config())
    _write_ready_project(tmp_path)

    args = argparse.Namespace(config=None, cwd=str(tmp_path), message=None)

    with patch.object(run_cmd, "subprocess") as mock_subprocess:
        mock_subprocess.run.side_effect = KeyboardInterrupt()

        ret = run_cmd.launch_tui(args)

    assert ret == 130


def test_run_rejects_missing_working_directory(tmp_path: Path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)

    ret = run_cmd.launch_tui(argparse.Namespace(config=None, cwd=str(missing), message=None))

    assert ret == 1
    assert not missing.exists()


def test_run_rejects_existing_but_invalid_opencode_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run_cmd, "_has_opencode", lambda: True)
    (tmp_path / "opencode.json").write_text(json.dumps({
        "model": "nonoka/default",
        "provider": {"unrelated": {}},
    }))

    with patch.object(run_cmd, "cmd_init") as mock_init, patch.object(run_cmd, "subprocess") as proc:
        ret = run_cmd.launch_tui(
            argparse.Namespace(config=None, cwd=str(tmp_path), message=None)
        )

    assert ret == 1
    mock_init.assert_not_called()
    proc.run.assert_not_called()
