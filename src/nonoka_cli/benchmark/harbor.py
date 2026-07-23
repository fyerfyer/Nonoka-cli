"""Harbor adapter for exercising the OpenCode -> nonoka-cli bridge.

The adapter deliberately provisions the bridge *inside* each Harbor task
environment.  Running OpenCode on the host would let it operate on the wrong
filesystem and would not be a Terminal-Bench result.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

try:  # pragma: no cover - integration-tested in an isolated Harbor environment.
  from harbor.agents.installed.opencode import OpenCode as _HarborOpenCode
  from harbor.environments.base import BaseEnvironment
  from harbor.models.agent.context import AgentContext

  _HAS_HARBOR = True
except ImportError:  # Keep normal nonoka-cli installs free of Harbor.
  _HAS_HARBOR = False

  class _HarborOpenCode:  # type: ignore[no-redef]
    def __init__(
      self,
      logs_dir: Path,
      model_name: str | None = None,
      extra_env: dict[str, str] | None = None,
      **_: Any,
    ) -> None:
      self.logs_dir = logs_dir
      self.model_name = model_name
      self._extra_env = extra_env or {}

    def version(self) -> str | None:
      return None

  BaseEnvironment = Any  # type: ignore[misc,assignment]
  AgentContext = Any  # type: ignore[misc,assignment]


_RUNTIME_DIR = "/opt/nonoka-runtime"
_PROVIDER_DIR = "/opt/nonoka-provider"
_CONFIG_PATH = f"{_RUNTIME_DIR}/nonoka-benchmark.yaml"
_PYTHON = f"{_RUNTIME_DIR}/venv/bin/python"
_OPENCODE = f"{_RUNTIME_DIR}/opencode"
_AGENT_LOG_DIR = "/logs/agent"
_UV_PYTHON_DIR = f"{_RUNTIME_DIR}/python"
_VERIFIER_UV_BIN_DIR = "/root/.local/bin"
_VERIFIER_UV_ENV = f"{_VERIFIER_UV_BIN_DIR}/env"


class OpenCodeHarborAgent(_HarborOpenCode):
  """Install and run this checkout's OpenCode bridge in a Harbor task.

  ``cli_wheel``, ``agent_wheel``, and ``provider_source`` must point to
  benchmark-local immutable artifacts prepared by ``nonoka-cli benchmark``.
  They are uploaded into the task container during Harbor's agent setup phase;
  no host workspace or host nonoka process is used while a task is scored.
  """

  def __init__(
    self,
    *args: Any,
    cli_wheel: str | None = None,
    agent_wheel: str | None = None,
    provider_source: str | None = None,
    uv_binary: str | None = None,
    opencode_binary: str | None = None,
    temperature: float = 0.0,
    max_turns: int = 24,
    timeout_seconds: float = 180.0,
    run_timeout_seconds: float = 900.0,
    tool_budget: int = 64,
    **kwargs: Any,
  ) -> None:
    super().__init__(*args, **kwargs)
    self._cli_wheel = self._require_wheel(cli_wheel, "cli_wheel")
    self._agent_wheel = self._require_wheel(agent_wheel, "agent_wheel")
    self._provider_source = self._require_provider(provider_source)
    self._uv_binary = self._require_file(uv_binary, "uv_binary")
    self._opencode_binary = self._require_file(opencode_binary, "opencode_binary")
    self._temperature = float(temperature)
    self._max_turns = int(max_turns)
    self._timeout_seconds = float(timeout_seconds)
    self._run_timeout_seconds = float(run_timeout_seconds)
    self._tool_budget = int(tool_budget)

  @staticmethod
  def name() -> str:
    return "nonoka-opencode"

  def version(self) -> str | None:
    """Identify the bridge adapter while retaining OpenCode's detected version."""
    parent_version = super().version()
    return f"nonoka-bridge/{parent_version}" if parent_version else "nonoka-bridge"

  @staticmethod
  def _require_wheel(value: str | None, name: str) -> Path:
    if not value:
      raise ValueError(f"{name} is required for the nonoka OpenCode Harbor adapter")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix != ".whl":
      raise ValueError(f"{name} must point to a wheel file: {path}")
    return path

  @staticmethod
  def _require_file(value: str | None, name: str) -> Path:
    if not value:
      raise ValueError(f"{name} is required for the nonoka OpenCode Harbor adapter")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
      raise ValueError(f"{name} must point to a file: {path}")
    return path

  @staticmethod
  def _require_provider(value: str | None) -> Path:
    if not value:
      raise ValueError("provider_source is required for the nonoka OpenCode Harbor adapter")
    path = Path(value).expanduser().resolve()
    if (
      not (path / "package.json").is_file()
      or not (path / "dist").is_dir()
      or not (path / "node_modules").is_dir()
    ):
      raise ValueError(
        "provider_source must contain package.json, built dist/, and node_modules/"
      )
    return path

  def _bridge_profile(self) -> str:
    """Return a project-agnostic OpenCode profile for the task container."""
    payload = {
      "$schema": "https://opencode.ai/config.json",
      "autoupdate": False,
      "model": "nonoka/default",
      "provider": {
        "nonoka": {
          "npm": f"file:{_PROVIDER_DIR}",
          "name": "Nonoka Terminal-Bench bridge",
          "options": {
            "serverCommand": [_PYTHON, "-m", "nonoka_cli", "--server"],
            "configPath": _CONFIG_PATH,
            "model": self.model_name or "deepseek-chat",
            "temperature": self._temperature,
            "maxTurns": self._max_turns,
            "timeoutSeconds": self._timeout_seconds,
            "toolBudget": self._tool_budget,
          },
          "models": {"default": {"name": f"Nonoka {self.model_name or 'default'}"}},
        }
      },
      "permission": {"*": "allow"},
      "tools": {"skill": False},
    }
    return json.dumps(payload, sort_keys=True)

  def _bridge_config(self) -> str:
    """Return only non-secret runtime configuration for the bridge server."""
    payload = {
      "model": self.model_name or "deepseek-chat",
      "cli": {"auto_approve": True},
      "agents": {"executor": {"max_turns": self._max_turns}},
    }
    # JSON is valid YAML. It avoids a runtime PyYAML dependency merely to
    # serialize a three-field benchmark configuration.
    return json.dumps(payload, sort_keys=True)

  async def install(self, environment: BaseEnvironment) -> None:
    """Install OpenCode and stable, task-independent verifier prerequisites.

    Some official Terminal-Bench verifiers bootstrap ``uv`` at scoring time.
    The download is unrelated to the task under test and makes a completed
    solution depend on transient verifier-network availability.  Stage the
    same pinned host ``uv`` used for the bridge at the conventional location
    those scripts source.  This deliberately provides no task-specific test
    packages, data, or solution artifacts.
    """
    if not _HAS_HARBOR:  # pragma: no cover - protects accidental direct use.
      raise RuntimeError("Harbor is required to install the OpenCode benchmark adapter")

    await environment.exec(f"mkdir -p {_RUNTIME_DIR} {_PROVIDER_DIR}", user="root")
    # uv validates wheel filenames, so preserve the PEP 427-compatible names
    # produced by ``uv build`` instead of using a convenient generic alias.
    cli_wheel_target = f"{_RUNTIME_DIR}/{self._cli_wheel.name}"
    agent_wheel_target = f"{_RUNTIME_DIR}/{self._agent_wheel.name}"
    await environment.upload_file(self._cli_wheel, cli_wheel_target)
    await environment.upload_file(self._agent_wheel, agent_wheel_target)
    await environment.upload_file(self._uv_binary, f"{_RUNTIME_DIR}/uv")
    await environment.upload_file(self._opencode_binary, _OPENCODE)
    await environment.upload_dir(self._provider_source, _PROVIDER_DIR)

    config = shlex.quote(self._bridge_config())
    await environment.exec(
      command=(
        "set -euo pipefail; "
        f"chmod +x {_RUNTIME_DIR}/uv {_OPENCODE}; "
        f"{_OPENCODE} --version; "
        "if command -v python3.13 >/dev/null 2>&1; then "
        "BENCHMARK_PYTHON=$(command -v python3.13); "
        "else "
        f"export UV_PYTHON_INSTALL_DIR={_UV_PYTHON_DIR}; "
        f"{_RUNTIME_DIR}/uv python install 3.13; "
        "BENCHMARK_PYTHON=3.13; "
        "fi; "
        f"{_RUNTIME_DIR}/uv venv {_RUNTIME_DIR}/venv --python \"$BENCHMARK_PYTHON\"; "
        f"{_RUNTIME_DIR}/uv pip install --python {_PYTHON} "
        f"{shlex.quote(agent_wheel_target)} {shlex.quote(cli_wheel_target)}; "
        f"chmod -R a+rX {_RUNTIME_DIR}/venv; "
        f"test ! -d {_UV_PYTHON_DIR} || chmod -R a+rX {_UV_PYTHON_DIR}; "
        f"mkdir -p {_VERIFIER_UV_BIN_DIR}; "
        f"ln -sf {_RUNTIME_DIR}/uv {_VERIFIER_UV_BIN_DIR}/uv; "
        f"ln -sf {_RUNTIME_DIR}/uv {_VERIFIER_UV_BIN_DIR}/uvx; "
        f"printf '%s\\n' 'export PATH={_VERIFIER_UV_BIN_DIR}:$PATH' > {_VERIFIER_UV_ENV}; "
        f"{_VERIFIER_UV_BIN_DIR}/uv --version; "
        f"printf '%s\\n' {config} > {_CONFIG_PATH}; "
        f"{_PYTHON} -c 'import nonoka, nonoka_cli'"
      ),
      user="root",
      env={"DEBIAN_FRONTEND": "noninteractive"},
    )
    # Harbor runs the agent as the task's default user, not root.  Write the
    # OpenCode profile in that user's config directory so the spawned CLI sees
    # the pinned local provider.
    profile = shlex.quote(self._bridge_profile())
    await environment.exec(
      command=(
        "mkdir -p ~/.config/opencode; "
        f"printf '%s\\n' {profile} > ~/.config/opencode/opencode.json"
      )
    )

  async def run(
    self,
    instruction: str,
    environment: BaseEnvironment,
    context: AgentContext,
  ) -> None:
    """Run OpenCode against its native task tools using the nonoka provider."""
    if not _HAS_HARBOR:  # pragma: no cover - direct tests exercise helpers only.
      raise RuntimeError("Harbor is required to run the OpenCode benchmark adapter")

    command = (
      f"{_OPENCODE} --model=nonoka/default run --format=json --thinking "
      "--dangerously-skip-permissions -- "
      f"{shlex.quote(instruction)} 2>&1 </dev/null | "
      f"stdbuf -oL tee {_AGENT_LOG_DIR}/opencode.txt"
    )
    result = await environment.exec(
      command=command,
      timeout_sec=self._run_timeout_seconds,
      env={
        "OPENCODE_FAKE_VCS": "git",
        "NONOKA_PROVIDER_LOG_PATH": f"{_AGENT_LOG_DIR}/provider.log",
        "NONOKA_LOG_FILE": f"{_AGENT_LOG_DIR}/bridge-server.log",
        "NONOKA_TRACE_DIR": f"{_AGENT_LOG_DIR}/bridge-events",
        "NONOKA_TIMELINE_PATH": f"{_AGENT_LOG_DIR}/bridge-timeline.ndjson",
      },
    )
    if result.return_code != 0:
      raise RuntimeError(f"OpenCode exited with status {result.return_code}")

    # Harbor's official OpenCode adapter uses this hook to parse the saved
    # NDJSON into an ATIF trajectory.  Preserve it for artifacts/diagnostics.
    populate = getattr(super(), "populate_context_post_run", None)
    if callable(populate):
      populate(context)
