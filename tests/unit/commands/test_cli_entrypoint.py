"""Tests for the public ``nonoka`` command surface."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nonoka_cli import cli
from nonoka_cli.commands.opencode_cmd import cmd_init


def test_top_level_init_alias_uses_opencode_initializer() -> None:
  args = cli._build_parser().parse_args(
    ["init", "--config", "nonoka.yaml", "--cwd", ".", "--yes"]
  )

  assert args.command == "init"
  assert args.func is cmd_init
  assert args.config == "nonoka.yaml"
  assert args.cwd == "."
  assert args.yes is True


def test_version_flag_reports_public_command(capsys: pytest.CaptureFixture[str]) -> None:
  with pytest.raises(SystemExit) as exc_info:
    cli._build_parser().parse_args(["--version"])

  assert exc_info.value.code == 0
  assert capsys.readouterr().out.startswith("nonoka ")


def test_logging_is_configured_before_env_files_are_loaded(monkeypatch) -> None:
  calls: list[str] = []
  monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: calls.append("logging"))
  monkeypatch.setattr(cli, "_load_env_files", lambda *_args: calls.append("env"))
  monkeypatch.setattr(cli.run_cmd, "launch_tui", lambda _args: 0)

  with patch.object(cli.sys, "argv", ["nonoka"]):
    assert cli.main() == 0

  assert calls == ["logging", "env"]


def test_explicit_config_loads_sibling_env(tmp_path, monkeypatch) -> None:
  config_path = tmp_path / "custom" / "config.yaml"
  env_path = config_path.parent / ".env"
  env_path.parent.mkdir()
  env_path.write_text("DEEPSEEK_API_KEY=test-only\n")
  loaded: list[str] = []
  monkeypatch.setattr(cli, "load_dotenv", lambda dotenv_path, override: loaded.append(str(dotenv_path)))
  monkeypatch.chdir(tmp_path)

  cli._load_env_files(config_path)

  assert str(env_path.resolve()) in loaded
