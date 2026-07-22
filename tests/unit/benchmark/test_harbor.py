"""Tests for the optional Harbor OpenCode bridge adapter."""

from __future__ import annotations

import json
from pathlib import Path

import nonoka_cli.benchmark.harbor as harbor_adapter
from nonoka_cli.benchmark.harbor import OpenCodeHarborAgent


def test_adapter_profile_pins_task_local_provider_and_bridge_wheels(tmp_path: Path):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  uv_binary = tmp_path / "uv"
  opencode_binary = tmp_path / "opencode"
  cli_wheel.touch()
  agent_wheel.touch()
  uv_binary.touch()
  opencode_binary.touch()
  provider = tmp_path / "provider"
  (provider / "dist").mkdir(parents=True)
  (provider / "node_modules").mkdir()
  (provider / "package.json").write_text("{}")

  agent = OpenCodeHarborAgent(
    logs_dir=tmp_path / "logs",
    model_name="deepseek-chat",
    cli_wheel=str(cli_wheel),
    agent_wheel=str(agent_wheel),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    opencode_binary=str(opencode_binary),
    temperature=0.2,
    max_turns=12,
    timeout_seconds=90,
    tool_budget=33,
  )

  profile = json.loads(agent._bridge_profile())
  options = profile["provider"]["nonoka"]["options"]
  assert profile["provider"]["nonoka"]["npm"] == "file:/opt/nonoka-provider"
  assert options["serverCommand"] == [
    "/opt/nonoka-runtime/venv/bin/python", "-m", "nonoka_cli", "--server"
  ]
  assert options["model"] == "deepseek-chat"
  assert options["temperature"] == 0.2
  assert options["maxTurns"] == 12
  assert options["timeoutSeconds"] == 90.0
  assert options["toolBudget"] == 33


def test_adapter_requires_explicit_runtime_artifacts(tmp_path: Path):
  try:
    OpenCodeHarborAgent(logs_dir=tmp_path, model_name="deepseek-chat")
  except ValueError as exc:
    assert "cli_wheel" in str(exc)
  else:  # pragma: no cover - makes the contract failure explicit.
    raise AssertionError("adapter accepted missing runtime artifacts")


async def test_adapter_installs_staged_runtime_without_task_container_downloads(
  tmp_path: Path, monkeypatch
):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  uv_binary = tmp_path / "uv"
  opencode_binary = tmp_path / "opencode"
  cli_wheel.touch()
  agent_wheel.touch()
  uv_binary.touch()
  opencode_binary.touch()
  provider = tmp_path / "provider"
  (provider / "dist").mkdir(parents=True)
  (provider / "node_modules").mkdir()
  (provider / "package.json").write_text("{}")

  class FakeEnvironment:
    def __init__(self):
      self.commands: list[str] = []
      self.uploads: list[tuple[object, object]] = []

    async def exec(self, command: str, **_):
      self.commands.append(command)

    async def upload_file(self, source, target):
      self.uploads.append((source, target))
      return None

    async def upload_dir(self, *_):
      return None

  monkeypatch.setattr(harbor_adapter, "_HAS_HARBOR", True)
  agent = OpenCodeHarborAgent(
    logs_dir=tmp_path / "logs",
    model_name="deepseek-chat",
    cli_wheel=str(cli_wheel),
    agent_wheel=str(agent_wheel),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    opencode_binary=str(opencode_binary),
  )
  environment = FakeEnvironment()

  await agent.install(environment)

  assert environment.commands[0] == "mkdir -p /opt/nonoka-runtime /opt/nonoka-provider"
  assert any("uv python install 3.13" in command for command in environment.commands)
  assert any(
    "UV_PYTHON_INSTALL_DIR=/opt/nonoka-runtime/python" in command
    for command in environment.commands
  )
  assert any(
    "chmod -R a+rX /opt/nonoka-runtime/python /opt/nonoka-runtime/venv" in command
    for command in environment.commands
  )
  assert (cli_wheel, f"/opt/nonoka-runtime/{cli_wheel.name}") in environment.uploads
  assert (agent_wheel, f"/opt/nonoka-runtime/{agent_wheel.name}") in environment.uploads
  assert all("apt-get" not in command and "curl" not in command for command in environment.commands)
