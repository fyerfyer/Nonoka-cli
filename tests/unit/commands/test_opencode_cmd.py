"""Tests for opencode command helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nonoka_cli.commands.opencode_cmd import _OPENCODE_AUTO_APPROVED_TOOLS, cmd_init
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
  assert "--config" in " ".join(data["provider"]["nonoka"]["options"]["serverCommand"])


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
  assert "--config" in " ".join(data["provider"]["nonoka"]["options"]["serverCommand"])


def test_opencode_init_creates_agent_prompt(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  agent_file = tmp_path / ".opencode" / "agents" / "build.md"
  assert agent_file.exists()
  content = agent_file.read_text()
  assert "permission:" in content
  assert '"*": ask' in content
  assert "bash: ask" in content
  assert "OpenCode-specific guidelines" in content


def test_opencode_init_uses_nonoka_system_prompt(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  custom_prompt = "You are a pirate. Speak like one."
  ConfigLoader.save(
    CLIConfig(model="deepseek-chat", system_prompt=custom_prompt),
    config_path,
  )

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  agent_file = tmp_path / ".opencode" / "agents" / "build.md"
  content = agent_file.read_text()
  assert custom_prompt in content
  assert "OpenCode-specific guidelines" in content


def test_opencode_init_has_hitl_permissions(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  assert data["permission"]["*"] == "ask"
  assert data["permission"]["bash"] == "ask"
  assert data["permission"]["edit"] == "ask"
  assert data["permission"]["write"] == "ask"
  assert data["agent"]["build"]["permission"]["*"] == "ask"


def test_opencode_init_auto_approve_permissions(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(
    CLIConfig(model="deepseek-chat", cli={"auto_approve": True}),
    config_path,
  )

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  for tool in _OPENCODE_AUTO_APPROVED_TOOLS:
    assert data["permission"][tool] == "allow"
    assert data["agent"]["build"]["permission"][tool] == "allow"
  assert data["permission"]["*"] == "ask"

  agent_file = tmp_path / ".opencode" / "agents" / "build.md"
  content = agent_file.read_text()
  assert "auto-approved" in content or "auto-approve" in content


def test_opencode_init_disables_native_skill_tool(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)

  args = argparse.Namespace(
    config=str(config_path),
    cwd=str(tmp_path),
    global_=False,
  )
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  assert data.get("tools", {}).get("skill") is False
