"""Tests for opencode command helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nonoka_cli.commands.opencode_cmd import cmd_init
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig


def test_opencode_init_creates_file(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="openai/gpt-4o"), config_path)

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  opencode_path = tmp_path / "opencode.json"
  assert opencode_path.exists()

  data = json.loads(opencode_path.read_text())
  assert data["model"] == "nonoka/default"
  assert data["provider"]["nonoka"]["options"]["configPath"] == str(config_path)


def test_opencode_init_merges_existing(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)

  existing = tmp_path / "opencode.json"
  existing.write_text(json.dumps({"model": "other/model", "custom": True}))

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  data = json.loads(existing.read_text())
  # Existing top-level model should be preserved.
  assert data["model"] == "other/model"
  assert data["custom"] is True
  assert data["provider"]["nonoka"]["options"]["configPath"] == str(config_path)
