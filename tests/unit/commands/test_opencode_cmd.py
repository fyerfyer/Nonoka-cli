"""Tests for opencode command helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from nonoka_cli.commands import opencode_cmd
from nonoka_cli.commands.opencode_cmd import _OPENCODE_AUTO_APPROVED_TOOLS, cmd_init
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig


@pytest.fixture(autouse=True)
def _skip_provider_install(monkeypatch: pytest.MonkeyPatch) -> None:
  """Keep config-generation unit tests independent of npm/bun and network."""
  monkeypatch.setattr(opencode_cmd, "_install_provider_locally", lambda *_args: True)


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
  assert data["provider"]["nonoka"]["options"]["requireFocusedVerification"] is True
  assert data["provider"]["nonoka"]["options"]["verificationEnforcement"] == "strict"
  assert data["provider"]["nonoka"]["options"]["serverCommand"] == ["nonoka-cli", "--server"]


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
  assert data["provider"]["nonoka"]["options"]["serverCommand"] == ["nonoka-cli", "--server"]


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
  assert "NONOKA_VERIFY=focused" in content


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


def test_opencode_init_keeps_native_skill_disabled_when_existing_tools_present(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)
  target = tmp_path / "opencode.json"
  target.write_text(json.dumps({"tools": {"bash": False}}))

  assert (
    cmd_init(
      argparse.Namespace(
        config=str(config_path),
        cwd=str(tmp_path),
        global_=False,
      )
    )
    == 0
  )

  data = json.loads(target.read_text())
  assert data["tools"] == {"bash": False, "skill": False}


def test_opencode_init_refreshes_managed_agent_prompt(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat", system_prompt="First prompt"), config_path)
  args = argparse.Namespace(config=str(config_path), cwd=str(tmp_path), global_=False)
  assert cmd_init(args) == 0

  ConfigLoader.save(CLIConfig(model="deepseek-chat", system_prompt="Second prompt"), config_path)
  assert cmd_init(args) == 0

  content = (tmp_path / ".opencode" / "agents" / "build.md").read_text()
  assert "Second prompt" in content
  assert "First prompt" not in content
