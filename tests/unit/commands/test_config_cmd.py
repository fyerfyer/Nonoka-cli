"""Tests for config command helpers."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from nonoka_cli.commands.config_cmd import _coerce_value, _set_dotted, cmd_set
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
