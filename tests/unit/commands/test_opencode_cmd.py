"""Tests for opencode command helpers."""

from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace
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


def test_provider_version_matches_monorepo_package():
  package_path = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "nonoka-opencode-provider"
    / "package.json"
  )
  expected = json.loads(package_path.read_text(encoding="utf-8"))["version"]
  assert opencode_cmd._resolve_provider_version() == expected
  assert opencode_cmd._PROVIDER_VERSION == expected


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
  assert data["provider"]["nonoka"]["options"]["cwd"] == str(tmp_path)
  assert "requireFocusedVerification" not in data["provider"]["nonoka"]["options"]
  assert "verificationEnforcement" not in data["provider"]["nonoka"]["options"]
  assert data["provider"]["nonoka"]["options"]["serverCommand"] == [
    sys.executable, "-m", "nonoka_cli", "--config", str(config_path), "--server",
  ]
  assert data["command"]["reload"]["template"] == "__NONOKA_RELOAD_CONFIG__"


def test_opencode_init_uses_litellm_prices_when_the_model_is_known(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek/deepseek-v4-pro"), config_path)
  prices = {"input": 0.000001, "output": 0.000002, "cache_read": 0.0000001}
  monkeypatch.setattr(opencode_cmd, "_model_cost_from_litellm", lambda _model: prices)

  args = argparse.Namespace(config=str(config_path), cwd=str(tmp_path), global_=False)
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  model = data["provider"]["nonoka"]["models"]["default"]
  assert model["cost"] == prices


def test_model_cost_lookup_resolves_provider_prefixed_model_names(
  monkeypatch: pytest.MonkeyPatch,
):
  model_info = {
    "input_cost_per_token": 0.000001,
    "output_cost_per_token": 0.000002,
    "cache_read_input_token_cost": 0.0000001,
  }
  fake_litellm = SimpleNamespace(
    model_cost={},
    get_model_info=lambda model: model_info if model == "openai/gpt-4o" else {},
  )
  monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

  assert opencode_cmd._model_cost_from_litellm("openai/gpt-4o") == {
    "input": 0.000001,
    "output": 0.000002,
    "cache_read": 0.0000001,
  }


def test_opencode_init_omits_price_when_litellm_has_no_model_entry(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="custom/private-model"), config_path)
  monkeypatch.setattr(opencode_cmd, "_model_cost_from_litellm", lambda _model: None)

  args = argparse.Namespace(config=str(config_path), cwd=str(tmp_path), global_=False)
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  model = data["provider"]["nonoka"]["models"]["default"]
  assert "cost" not in model


def test_opencode_init_removes_legacy_benchmark_contract_from_interactive_config(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), config_path)
  (tmp_path / "opencode.json").write_text(json.dumps({
    "provider": {"nonoka": {"options": {
      "requireWorkspaceMutation": True,
      "requireObservedEffect": True,
      "requireFocusedVerification": True,
      "verificationEnforcement": "strict",
      "maxCompletionCorrections": 1,
    }}},
  }))

  args = argparse.Namespace(config=str(config_path), cwd=str(tmp_path), global_=False)
  assert cmd_init(args) == 0

  options = json.loads((tmp_path / "opencode.json").read_text())["provider"]["nonoka"]["options"]
  for key in (
    "requireWorkspaceMutation", "requireObservedEffect", "requireFocusedVerification",
    "verificationEnforcement", "maxCompletionCorrections",
  ):
    assert key not in options


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
  # init selects the Nonoka execution path instead of silently bypassing it.
  assert data["model"] == "nonoka/default"
  assert data["custom"] is True
  assert data["provider"]["nonoka"]["options"]["configPath"] == str(config_path)
  assert data["provider"]["nonoka"]["options"]["serverCommand"] == [
    sys.executable, "-m", "nonoka_cli", "--config", str(config_path), "--server",
  ]


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


def test_opencode_init_applies_yaml_permission_overrides(tmp_path: Path):
  config_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(
    CLIConfig(
      model="deepseek-chat",
      cli={"auto_approve": True},
      permissions={"glob": "deny", "custom-safe-tool": "allow"},
    ),
    config_path,
  )

  args = argparse.Namespace(config=str(config_path), cwd=str(tmp_path), global_=False)
  assert cmd_init(args) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  for block in (data["permission"], data["agent"]["build"]["permission"]):
    assert block["glob"] == "deny"
    assert block["grep"] == "allow"
    assert block["custom-safe-tool"] == "allow"


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
  assert data["permission"]["skill"] == "deny"
  assert data["agent"]["build"]["permission"]["skill"] == "deny"


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


def test_opencode_init_prefers_project_config_without_explicit_flag(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
  global_path = tmp_path / "global.yaml"
  project_path = tmp_path / "nonoka.yaml"
  ConfigLoader.save(CLIConfig(model="global-model"), global_path)
  ConfigLoader.save(CLIConfig(model="project-model"), project_path)
  monkeypatch.setattr(ConfigLoader, "DEFAULT_PATH", global_path)

  assert cmd_init(argparse.Namespace(config=None, cwd=str(tmp_path), global_=False)) == 0

  data = json.loads((tmp_path / "opencode.json").read_text())
  options = data["provider"]["nonoka"]["options"]
  assert options["configPath"] == str(project_path)
  assert data["provider"]["nonoka"]["models"]["default"]["name"] == "Nonoka project-model"


def test_opencode_init_rejects_missing_working_directory(tmp_path: Path):
  missing = tmp_path / "does-not-exist"
  result = cmd_init(argparse.Namespace(config=None, cwd=str(missing), global_=False))
  assert result == 1
  assert not missing.exists()


def test_opencode_init_does_not_write_config_when_provider_install_fails(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
  ConfigLoader.save(CLIConfig(model="deepseek-chat"), tmp_path / "nonoka.yaml")
  monkeypatch.setattr(opencode_cmd, "_install_provider_locally", lambda *_args: False)

  result = cmd_init(argparse.Namespace(config=None, cwd=str(tmp_path), global_=False))

  assert result == 1
  assert not (tmp_path / "opencode.json").exists()
  assert not (tmp_path / ".opencode").exists()
