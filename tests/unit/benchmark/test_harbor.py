"""Tests for the optional Harbor OpenCode bridge adapter."""

from __future__ import annotations

import json
from pathlib import Path

import nonoka_cli.benchmark.harbor as harbor_adapter
from nonoka_cli.benchmark.harbor import OpenCodeHarborAgent


def test_adapter_profile_pins_task_local_provider_and_bridge_wheels(tmp_path: Path):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  site_packages_archive = tmp_path / "site-packages.tar.gz"
  uv_binary = tmp_path / "uv"
  python_runtime_archive = tmp_path / "python-3.13.tar.gz"
  opencode_binary = tmp_path / "opencode"
  cli_wheel.touch()
  agent_wheel.touch()
  site_packages_archive.touch()
  uv_binary.touch()
  python_runtime_archive.touch()
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
    site_packages_archive=str(site_packages_archive),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    python_runtime_archive=str(python_runtime_archive),
    opencode_binary=str(opencode_binary),
    temperature=0.2,
    max_turns=12,
    timeout_seconds=90,
    run_timeout_seconds=480,
    tool_budget=33,
  )

  profile = json.loads(agent._bridge_profile())
  options = profile["provider"]["nonoka"]["options"]
  assert profile["provider"]["nonoka"]["npm"] == "file:/opt/nonoka-provider"
  assert options["serverCommand"] == [
    "/opt/nonoka-runtime/venv/bin/python",
    "-Es",
    "-m",
    "nonoka_cli",
    "--server",
  ]
  assert options["env"] == {"NONOKA_DISABLE_PROJECT_AGENTS": "1"}
  assert options["model"] == "deepseek-chat"
  assert options["temperature"] == 0.2
  assert options["maxTurns"] == 12
  assert options["timeoutSeconds"] == 90.0
  assert options["wallTimeoutSeconds"] == 480.0
  assert options["toolBudget"] == 33
  assert options["maxContextBytes"] == 256 * 1024
  assert options["maxExternalResultBytes"] == 64 * 1024
  assert options["requireObservedEffect"] is True
  assert options["requireFocusedVerification"] is True
  assert options["verificationEnforcement"] == "advisory"
  assert "requireWorkspaceMutation" not in options
  assert profile["permission"] == "allow"
  assert profile["agent"]["build"]["tools"] == {"skill": False, "task": False}
  assert profile["agent"]["build"]["permission"]["task"] == "deny"

  config = json.loads(agent._bridge_config())
  assert "autonomous coding benchmark agent" in config["system_prompt"]
  assert "Do not stop after an audit" in config["system_prompt"]
  assert "preserve all unrelated content" in config["system_prompt"]
  assert "narrowest valid edit" in config["system_prompt"]
  assert "candidate evidence" in config["system_prompt"]
  assert "smallest containing" in config["system_prompt"]
  assert "source record" in config["system_prompt"]
  assert "bounded match snippets" in config["system_prompt"]
  assert "byte-for-byte working copy" in config["system_prompt"]
  assert "sidecars, checkpoint, repair, mount, migrate" in config["system_prompt"]
  assert "stateful tools only on that working copy" in config["system_prompt"]
  assert "originals may be examined only" in config["system_prompt"]
  assert "never\ncreate, copy, or modify repository test files" in config["system_prompt"]
  assert "treat that test as stale input" in config["system_prompt"]
  assert "runner reports collected and executed results" in config["system_prompt"]


def test_adapter_requires_explicit_runtime_artifacts(tmp_path: Path):
  try:
    OpenCodeHarborAgent(logs_dir=tmp_path, model_name="deepseek-chat")
  except ValueError as exc:
    assert "cli_wheel" in str(exc)
  else:  # pragma: no cover - makes the contract failure explicit.
    raise AssertionError("adapter accepted missing runtime artifacts")


def test_adapter_omits_cumulative_budgets_by_default(tmp_path: Path):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  site_packages_archive = tmp_path / "site-packages.tar.gz"
  uv_binary = tmp_path / "uv"
  python_runtime_archive = tmp_path / "python-3.13.tar.gz"
  opencode_binary = tmp_path / "opencode"
  for path in (
    cli_wheel,
    agent_wheel,
    site_packages_archive,
    uv_binary,
    python_runtime_archive,
    opencode_binary,
  ):
    path.touch()
  provider = tmp_path / "provider"
  (provider / "dist").mkdir(parents=True)
  (provider / "node_modules").mkdir()
  (provider / "package.json").write_text("{}")

  agent = OpenCodeHarborAgent(
    logs_dir=tmp_path / "logs",
    cli_wheel=str(cli_wheel),
    agent_wheel=str(agent_wheel),
    site_packages_archive=str(site_packages_archive),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    python_runtime_archive=str(python_runtime_archive),
    opencode_binary=str(opencode_binary),
  )

  options = json.loads(agent._bridge_profile())["provider"]["nonoka"]["options"]
  assert "maxTurns" not in options
  assert "timeoutSeconds" not in options
  assert "toolBudget" not in options
  assert "agents" not in json.loads(agent._bridge_config())


async def test_adapter_installs_staged_runtime_and_task_agnostic_verifier_uv(
  tmp_path: Path, monkeypatch
):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  site_packages_archive = tmp_path / "site-packages.tar.gz"
  uv_binary = tmp_path / "uv"
  python_runtime_archive = tmp_path / "python-3.13.tar.gz"
  opencode_binary = tmp_path / "opencode"
  cli_wheel.touch()
  agent_wheel.touch()
  site_packages_archive.touch()
  uv_binary.touch()
  python_runtime_archive.touch()
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
      return type("Result", (), {"return_code": 0, "stdout": "", "stderr": ""})()

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
    site_packages_archive=str(site_packages_archive),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    python_runtime_archive=str(python_runtime_archive),
    opencode_binary=str(opencode_binary),
  )
  environment = FakeEnvironment()

  await agent.install(environment)

  assert environment.commands[0] == "mkdir -p /opt/nonoka-runtime /opt/nonoka-provider"
  provision_command = next(
    command for command in environment.commands if "/root/.local/bin/uvx" in command
  )
  assert "test -x /usr/bin/python3.13" in provision_command
  assert "tar -xzf /opt/nonoka-runtime/python-3.13.tar.gz" in provision_command
  assert "BENCHMARK_PYTHON=/opt/nonoka-runtime/python-host/bin/python3.13" in provision_command
  assert "platform.python_version()" in provision_command
  assert "platform.machine()" in provision_command
  managed_python_dir = (
    "/root/.local/share/uv/python/cpython-${STAGED_VERSION}-linux-${STAGED_ARCH}-gnu"
  )
  staged_python_link = (
    "ln -sfn /opt/nonoka-runtime/python-host/bin/python3.13 /usr/local/bin/python3.13"
  )
  assert managed_python_dir in provision_command
  assert staged_python_link in provision_command
  assert "BENCHMARK_PYTHON=/usr/bin/python3.13" in provision_command
  assert "test -x /usr/local/bin/python3.13" in provision_command
  assert "BENCHMARK_PYTHON=/usr/local/bin/python3.13" in provision_command
  assert "command -v python3.13" not in provision_command
  assert "uv python install 3.13" in provision_command
  site_packages_extract = (
    "tar -xzf /opt/nonoka-runtime/site-packages.tar.gz -C /opt/nonoka-runtime/venv"
  )
  assert site_packages_extract in provision_command
  assert any(
    "UV_PYTHON_INSTALL_DIR=/opt/nonoka-runtime/python" in command
    for command in environment.commands
  )
  assert any(
    "test ! -d /opt/nonoka-runtime/python || chmod -R a+rX /opt/nonoka-runtime/python" in command
    for command in environment.commands
  )
  runtime_import_check = (
    "/opt/nonoka-runtime/venv/bin/python -Es -c "
    "'import nonoka, nonoka_cli, nonoka_cli.benchmark.watchdog'"
  )
  assert runtime_import_check in provision_command
  assert any(command == runtime_import_check for command in environment.commands)
  assert "ln -sf /opt/nonoka-runtime/uv /root/.local/bin/uv" in provision_command
  assert 'exec /opt/nonoka-runtime/uv tool run "$@"' in provision_command
  assert "chmod +x /root/.local/bin/uvx" in provision_command
  assert "export PATH=/root/.local/bin:$PATH" in provision_command
  assert "*https://astral.sh/uv/*/install.sh*) exit 0 ;;" in provision_command
  assert 'exec /usr/bin/curl "$@"' in provision_command
  assert "> /usr/local/bin/curl" in provision_command
  assert "chmod +x /usr/local/bin/curl" in provision_command
  assert "/root/.local/bin/uv --version" in provision_command
  assert (cli_wheel, f"/opt/nonoka-runtime/{cli_wheel.name}") in environment.uploads
  assert (agent_wheel, f"/opt/nonoka-runtime/{agent_wheel.name}") in environment.uploads
  assert (site_packages_archive, "/opt/nonoka-runtime/site-packages.tar.gz") in environment.uploads
  assert (python_runtime_archive, "/opt/nonoka-runtime/python-3.13.tar.gz") in environment.uploads
  assert all("apt-get" not in command for command in environment.commands)


async def test_adapter_applies_hard_run_timeout_to_opencode(tmp_path: Path, monkeypatch):
  cli_wheel = tmp_path / "nonoka_cli-0.0.0-py3-none-any.whl"
  agent_wheel = tmp_path / "nonoka-0.0.0-py3-none-any.whl"
  site_packages_archive = tmp_path / "site-packages.tar.gz"
  uv_binary = tmp_path / "uv"
  python_runtime_archive = tmp_path / "python-3.13.tar.gz"
  opencode_binary = tmp_path / "opencode"
  for path in (
    cli_wheel,
    agent_wheel,
    site_packages_archive,
    uv_binary,
    python_runtime_archive,
    opencode_binary,
  ):
    path.touch()
  provider = tmp_path / "provider"
  (provider / "dist").mkdir(parents=True)
  (provider / "node_modules").mkdir()
  (provider / "package.json").write_text("{}")

  class FakeEnvironment:
    def __init__(self):
      self.command = ""
      self.timeout_sec: float | None = None
      self.env: dict[str, str] | None = None

    async def exec(self, command: str, **kwargs):
      self.command = command
      self.timeout_sec = kwargs.get("timeout_sec")
      self.env = kwargs.get("env")
      return type("Result", (), {"return_code": 0})()

  monkeypatch.setattr(harbor_adapter, "_HAS_HARBOR", True)
  agent = OpenCodeHarborAgent(
    logs_dir=tmp_path / "logs",
    model_name="deepseek-chat",
    cli_wheel=str(cli_wheel),
    agent_wheel=str(agent_wheel),
    site_packages_archive=str(site_packages_archive),
    provider_source=str(provider),
    uv_binary=str(uv_binary),
    python_runtime_archive=str(python_runtime_archive),
    opencode_binary=str(opencode_binary),
    run_timeout_seconds=321,
  )
  environment = FakeEnvironment()

  await agent.run("Solve the task", environment, object())

  assert "opencode" in environment.command
  assert "nonoka_cli.benchmark.watchdog" in environment.command
  assert "--grace 5" in environment.command
  assert "--artifact-dir /logs/artifacts/agent" in environment.command
  assert "--evidence-log /logs/agent/run-evidence.ndjson" in environment.command
  assert "--allow-scorable-budget-exit" in environment.command
  assert "--dangerously-skip-permissions" not in environment.command
  assert "mkdir -p /logs/agent /logs/artifacts/agent" in environment.command
  assert "> /logs/agent/watchdog-launcher.log 2>&1" in environment.command
  assert "cp /logs/agent/watchdog-launcher.log /logs/artifacts/agent/" in environment.command
  assert "exit $WATCHDOG_STATUS" in environment.command
  assert environment.timeout_sec == 336.0
  assert environment.env is not None
  assert environment.env["NONOKA_PROTECTED_PATHS"] == "/tests"
