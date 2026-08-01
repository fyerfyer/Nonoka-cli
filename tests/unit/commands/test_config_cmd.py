"""Tests for config command helpers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from nonoka_cli.commands import config_cmd
from nonoka_cli.commands.config_cmd import _coerce_value, _set_dotted, cmd_init, cmd_set
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig


def test_coerce_value_bool():
  assert _coerce_value("true") is True
  assert _coerce_value("false") is False


def test_coerce_value_int():
  assert _coerce_value("42") == 42


def test_coerce_value_list():
  assert _coerce_value('["a", "b"]') == ["a", "b"]


def test_coerce_value_string():
  assert _coerce_value("hello") == "hello"


def test_set_dotted_nested():
  data: dict = {}
  _set_dotted(data, "cli.theme", "light")
  assert data == {"cli": {"theme": "light"}}


def test_config_set(tmp_path: Path):
  config_path = tmp_path / "config.yaml"
  ConfigLoader.save(CLIConfig(model="gpt-4o"), config_path)

  args = argparse.Namespace(config=str(config_path), key="model", value="deepseek-chat")
  assert cmd_set(args) == 0

  cfg = ConfigLoader.load(config_path)
  assert cfg.model == "deepseek-chat"


def test_config_init_yes(tmp_path: Path):
  config_path = tmp_path / "config.yaml"
  args = argparse.Namespace(
    config=str(config_path),
    yes=True,
    model="deepseek-chat",
    auto_approve=False,
  )
  assert cmd_init(args) == 0
  assert config_path.exists()
  cfg = ConfigLoader.load(config_path)
  assert cfg.model == "deepseek-chat"
  assert cfg.cli.auto_approve is False
  assert cfg.hitl.policy == "interactive"


def test_write_env_file(tmp_path: Path):
  env_path = tmp_path / ".env"
  config_cmd._write_env_file(env_path, "DEEPSEEK_API_KEY", "sk-secret")
  assert env_path.exists()
  assert env_path.stat().st_mode & 0o777 == 0o600
  values = config_cmd._load_env_file(env_path)
  assert values.get("DEEPSEEK_API_KEY") == "sk-secret"


def test_config_init_saves_api_key_to_env(tmp_path: Path, monkeypatch):
  config_path = tmp_path / "config.yaml"
  env_path = tmp_path / ".env"

  inputs = iter(["deepseek-chat", "", "d", ""])
  def fake_read_input(prompt: str, default: str = "") -> str:
    return next(inputs) if default == "" else default
  monkeypatch.setattr(config_cmd, "_read_input", fake_read_input)
  monkeypatch.setattr(config_cmd, "_read_secret", lambda prompt: "sk-test-key")
  monkeypatch.setattr(config_cmd, "_confirm", lambda prompt, default=False: False)

  args = argparse.Namespace(config=str(config_path), yes=False)
  assert cmd_init(args) == 0

  assert config_path.exists()
  cfg = ConfigLoader.load(config_path)
  assert cfg.model == "deepseek/deepseek-v4-pro"
  assert cfg.api_key == ""

  values = config_cmd._load_env_file(env_path)
  assert values.get("DEEPSEEK_API_KEY") == "sk-test-key"
  assert os.getenv("DEEPSEEK_API_KEY") == "sk-test-key"

  # Clean up so the key does not leak into other tests.
  monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_config_init_uses_configured_directory_for_default_env(tmp_path: Path, monkeypatch):
  config_dir = tmp_path / "configured"
  monkeypatch.setenv("NONOKA_CONFIG_DIR", str(config_dir))

  assert config_cmd._env_path_for_config(None) == config_dir / ".env"
