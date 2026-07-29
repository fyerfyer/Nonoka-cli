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
_VENV_PYTHON = f"{_RUNTIME_DIR}/venv/bin/python"
# Task images routinely carry their own Python configuration.  ``-Es`` is
# supplied at every bridge entrypoint: it ignores PYTHON* variables and the
# per-user site directory without adding a shell-script interpreter dependency.
_PYTHON = _VENV_PYTHON
_OPENCODE = f"{_RUNTIME_DIR}/opencode"
_AGENT_LOG_DIR = "/logs/agent"
_UV_PYTHON_DIR = f"{_RUNTIME_DIR}/python"
_STAGED_PYTHON_DIR = f"{_RUNTIME_DIR}/python-host"
_STAGED_PYTHON_ARCHIVE = f"{_RUNTIME_DIR}/python-3.13.tar.gz"
_SITE_PACKAGES_ARCHIVE = f"{_RUNTIME_DIR}/site-packages.tar.gz"
_STAGED_PYTHON = f"{_STAGED_PYTHON_DIR}/bin/python3.13"
_VERIFIER_UV_BIN_DIR = "/root/.local/bin"
_VERIFIER_UV_ENV = f"{_VERIFIER_UV_BIN_DIR}/env"
_VERIFIER_CURL_SHIM = "/usr/local/bin/curl"
_BENCHMARK_SYSTEM_PROMPT = """\
You are an autonomous coding benchmark agent. Implement the requested change in
the task workspace; the task instruction is authorization to modify files.
Do not stop after an audit, report, plan, or request for confirmation when a
remediation or implementation is explicitly requested. Inspect only the files
needed to identify the target, make the required edits promptly, then run a
focused check that demonstrates the acceptance criteria. Avoid broad delegated
searches and do not read large unrelated trees into the conversation.
When remediating an existing artifact, preserve all unrelated content and
structure. Prefer the narrowest valid edit, inspect the resulting diff, and
verify that the requested transformation did not rewrite adjacent semantics.
Before opening opaque, damaged, forensic, migration, or recovery inputs with a
tool that may create sidecars, checkpoint, repair, mount, migrate, or otherwise
alter state, make a byte-for-byte working copy of the complete related input set
in an isolated location. Use stateful tools only on that working copy; do not
open or probe the originals with a tool that can alter them merely because a
separate output copy already exists. The originals may be examined only with
operations known to be read-only. If an unexpected input-state change occurs,
stop and reassess from the preserved copy rather than continuing a destructive
investigation.
Treat search output as candidate evidence, not a conclusion. For every
candidate occurrence reported by a search, inspect its smallest containing
source record or region before deciding it is benign. A partial, truncated, or
errored search leaves its candidate set unresolved; do not use a different
broad query to dismiss it. Partition the source by path, record, or line range
until the relevant evidence is bounded, then verify the completed edit.
For a content search that encounters a large record, ask the available shell
or search tool for bounded match snippets and source coordinates, including
structured-data files; do not infer that an oversized record contains no
relevant content.
If a shell command is unavailable, switch once to an available host tool or a
standard fallback command; do not retry the missing command.
Treat expensive end-to-end checks as a bounded verification budget. Do not
repeat a command that has already shown itself to be slow merely with a longer
timeout, a different output formatter, or an equivalent wrapper. After one
focused bounded investigation identifies a credible implementation, make that
change promptly. Reserve a full-scale check for after the candidate change,
and use a smaller representative check when the baseline is known to be
expensive. A pre-change benchmark alone is not completion evidence.
The benchmark harness is outside the task workspace. In particular, never
create, copy, or modify repository test files; those are benchmark-owned
verifier assets and may intentionally describe behavior that the requested
source change supersedes. Read them when useful, but implement the fix in
production code. If a checked-in test asserts behavior contradicted by the
task statement or explicit verifier feedback, treat that test as stale input,
not as authority to preserve the old behavior. Once the exact production guard
or implementation point is identified, make the requested edit before doing
more broad archaeology. When the task refers to an error or behavior that already
exists nearby, inspect that analogous implementation and align the exception
type, message style, and edge cases. For generated text or code, compare the
exact output rather than accepting a merely plausible result. A focused check
must be a real test, build, lint, or typecheck command; a custom `python -c`
assertion is diagnostic evidence only and is not accepted for completion. A
test is valid only when its runner reports collected and executed results.
Directly running a Python source file that declares tests is not a passing
verification unless that file intentionally provides an executable test
entrypoint. If the runner is unavailable, collection is empty, or output is
inconclusive, leave verification unresolved rather than claiming success.
Benchmark images contain pinned project environments. Do not install or
upgrade dependencies to obtain a test runner. If a familiar runner such as
pytest is unavailable, inspect the repository's documented or executable test
entrypoints (for example `bin/test`, tox, nox, or a project script) and use the
existing environment. Generated target-language code must use valid operators
and syntax for that target; source-language constructor text is not a valid
substitute merely because it appears in an intermediate pretty-print example.
"""


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
    site_packages_archive: str | None = None,
    provider_source: str | None = None,
    uv_binary: str | None = None,
    python_runtime_archive: str | None = None,
    opencode_binary: str | None = None,
    temperature: float = 0.0,
    max_turns: int | None = None,
    timeout_seconds: float | None = None,
    run_timeout_seconds: float = 3600.0,
    tool_budget: int | None = None,
    **kwargs: Any,
  ) -> None:
    super().__init__(*args, **kwargs)
    self._cli_wheel = self._require_wheel(cli_wheel, "cli_wheel")
    self._agent_wheel = self._require_wheel(agent_wheel, "agent_wheel")
    self._site_packages_archive = self._require_file(site_packages_archive, "site_packages_archive")
    self._provider_source = self._require_provider(provider_source)
    self._uv_binary = self._require_file(uv_binary, "uv_binary")
    self._python_runtime_archive = self._require_file(
      python_runtime_archive, "python_runtime_archive"
    )
    self._opencode_binary = self._require_file(opencode_binary, "opencode_binary")
    self._temperature = float(temperature)
    self._max_turns = int(max_turns) if max_turns is not None else None
    self._timeout_seconds = float(timeout_seconds) if timeout_seconds is not None else None
    self._run_timeout_seconds = float(run_timeout_seconds)
    self._tool_budget = int(tool_budget) if tool_budget is not None else None

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
      raise ValueError("provider_source must contain package.json, built dist/, and node_modules/")
    return path

  @staticmethod
  def _require_success(result: Any, phase: str) -> None:
    """Turn non-zero Harbor environment commands into actionable setup failures."""
    if result.return_code == 0:
      return
    output = (getattr(result, "stderr", None) or getattr(result, "stdout", None) or "").strip()
    detail = f": {output[-2000:]}" if output else ""
    raise RuntimeError(f"{phase} failed with status {result.return_code}{detail}")

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
            "serverCommand": [_PYTHON, "-Es", "-m", "nonoka_cli", "--server"],
            "configPath": _CONFIG_PATH,
            "model": self.model_name or "deepseek/deepseek-v4-pro",
            "temperature": self._temperature,
            "wallTimeoutSeconds": self._run_timeout_seconds,
            "maxContextBytes": 256 * 1024,
            "maxExternalResultBytes": 64 * 1024,
            # Terminal-Bench tasks may modify the cwd, system packages,
            # services, databases, or repositories outside /app. Require a
            # typed host-observed effect instead of forcing every task to
            # manufacture a workspace file solely to satisfy the bridge.
            "requireObservedEffect": True,
            "requireFocusedVerification": True,
            "verificationEnforcement": "advisory",
          },
          "models": {"default": {"name": f"Nonoka {self.model_name or 'default'}"}},
        }
      },
      "permission": "allow",
      # Native task delegation can return an unbounded research report into
      # the parent transcript. The benchmark bridge is already an autonomous
      # agent and should use its direct task tools instead.
      "agent": {
        "build": {
          "permission": {"skill": "deny", "task": "deny"},
          "tools": {"skill": False, "task": False},
        }
      },
    }
    options = payload["provider"]["nonoka"]["options"]
    if self._max_turns is not None:
      options["maxTurns"] = self._max_turns
    if self._timeout_seconds is not None:
      options["timeoutSeconds"] = self._timeout_seconds
    if self._tool_budget is not None:
      options["toolBudget"] = self._tool_budget
    return json.dumps(payload, sort_keys=True)

  def _bridge_config(self) -> str:
    """Return only non-secret runtime configuration for the bridge server."""
    payload = {
      "model": self.model_name or "deepseek/deepseek-v4-pro",
      "system_prompt": _BENCHMARK_SYSTEM_PROMPT,
      "cli": {"auto_approve": True},
    }
    if self._max_turns is not None:
      payload["agents"] = {"executor": {"max_turns": self._max_turns}}
    # JSON is valid YAML. It avoids a runtime PyYAML dependency merely to
    # serialize a three-field benchmark configuration.
    return json.dumps(payload, sort_keys=True)

  async def install(self, environment: BaseEnvironment) -> None:
    """Install OpenCode and stable, task-independent verifier prerequisites.

    Some official Terminal-Bench verifiers bootstrap ``uv`` at scoring time.
    The download is unrelated to the task under test and makes a completed
    solution depend on transient verifier-network availability.  Stage the
    same host ``uv`` used for the bridge at the conventional location those
    scripts source, and make its official installer a no-op when it is invoked
    through ``curl | sh``.  All other curl requests retain the image's normal
    implementation.  This deliberately provides no task-specific test
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
    await environment.upload_file(self._site_packages_archive, _SITE_PACKAGES_ARCHIVE)
    await environment.upload_file(self._uv_binary, f"{_RUNTIME_DIR}/uv")
    await environment.upload_file(self._python_runtime_archive, _STAGED_PYTHON_ARCHIVE)
    await environment.upload_file(self._opencode_binary, _OPENCODE)
    await environment.upload_dir(self._provider_source, _PROVIDER_DIR)

    config = shlex.quote(self._bridge_config())
    setup_result = await environment.exec(
      command=(
        "set -euo pipefail; "
        f"chmod +x {_RUNTIME_DIR}/uv {_OPENCODE}; "
        f"{_OPENCODE} --version; "
        f"mkdir -p {_STAGED_PYTHON_DIR}; "
        f"tar -xzf {_STAGED_PYTHON_ARCHIVE} -C {_STAGED_PYTHON_DIR} "
        "--strip-components=1; "
        f"if {_STAGED_PYTHON} -c 'import sys; assert sys.version_info[:2] == (3, 13)'; then "
        f"BENCHMARK_PYTHON={_STAGED_PYTHON}; "
        f"STAGED_VERSION=$({_STAGED_PYTHON} "
        "-c 'import platform; print(platform.python_version())'); "
        f"STAGED_ARCH=$({_STAGED_PYTHON} -c 'import platform; print(platform.machine())'); "
        "mkdir -p /root/.local/share/uv/python /usr/local/bin; "
        f"ln -sfn {_STAGED_PYTHON_DIR} "
        '"/root/.local/share/uv/python/cpython-${STAGED_VERSION}-linux-${STAGED_ARCH}-gnu"; '
        f"ln -sfn {_STAGED_PYTHON} /usr/local/bin/python3.13; "
        # Do not use an arbitrary root-only python3.13 found on PATH. Harbor
        # may expose one below /root during setup; uv then creates a venv whose
        # interpreter symlink is invisible to the non-root agent phase.
        "elif test -x /usr/bin/python3.13; then "
        "BENCHMARK_PYTHON=/usr/bin/python3.13; "
        "elif test -x /usr/local/bin/python3.13; then "
        "BENCHMARK_PYTHON=/usr/local/bin/python3.13; "
        "else "
        f"export UV_PYTHON_INSTALL_DIR={_UV_PYTHON_DIR}; "
        f"{_RUNTIME_DIR}/uv python install 3.13; "
        "BENCHMARK_PYTHON=3.13; "
        "fi; "
        f'{_RUNTIME_DIR}/uv venv {_RUNTIME_DIR}/venv --python "$BENCHMARK_PYTHON"; '
        # Dependencies are materialized on the host and uploaded as a regular
        # package tree.  Task containers cannot reliably reach a package index.
        f"tar -xzf {_SITE_PACKAGES_ARCHIVE} -C {_RUNTIME_DIR}/venv; "
        f"chmod -R a+rX {_RUNTIME_DIR}/venv; "
        f"test ! -d {_UV_PYTHON_DIR} || chmod -R a+rX {_UV_PYTHON_DIR}; "
        f"mkdir -p {_VERIFIER_UV_BIN_DIR}; "
        f"ln -sf {_RUNTIME_DIR}/uv {_VERIFIER_UV_BIN_DIR}/uv; "
        f"printf '%s\\n' '#!/bin/sh' 'exec {_RUNTIME_DIR}/uv tool run \"$@\"' "
        f"> {_VERIFIER_UV_BIN_DIR}/uvx; "
        f"chmod +x {_VERIFIER_UV_BIN_DIR}/uvx; "
        f"printf '%s\\n' 'export PATH={_VERIFIER_UV_BIN_DIR}:$PATH' > {_VERIFIER_UV_ENV}; "
        # A number of official verifier scripts unconditionally run Astral's
        # installer even after uv is available on PATH.  Keep the verifier
        # offline-capable by accepting that specific bootstrap request; the
        # script it would install is already represented by uv/uvx/env above.
        # The match intentionally permits any Astral uv installer version.
        f"printf '%s\\n' '#!/bin/sh' 'case \"$*\" in' "
        "'*https://astral.sh/uv/*/install.sh*) exit 0 ;;' 'esac' "
        "'exec /usr/bin/curl \"$@\"' "
        f"> {_VERIFIER_CURL_SHIM}; "
        f"chmod +x {_VERIFIER_CURL_SHIM}; "
        f"{_VERIFIER_UV_BIN_DIR}/uv --version; "
        f"printf '%s\\n' {config} > {_CONFIG_PATH}; "
        f"{_PYTHON} -Es -c 'import nonoka, nonoka_cli, nonoka_cli.benchmark.watchdog'"
      ),
      user="root",
      env={"DEBIAN_FRONTEND": "noninteractive"},
    )
    self._require_success(setup_result, "Harbor bridge runtime setup")
    # Harbor runs the agent as the task's default user, not root.  Write the
    # OpenCode profile in that user's config directory so the spawned CLI sees
    # the pinned local provider.
    profile = shlex.quote(self._bridge_profile())
    profile_result = await environment.exec(
      command=(
        f"mkdir -p ~/.config/opencode; printf '%s\\n' {profile} > ~/.config/opencode/opencode.json"
      )
    )
    self._require_success(profile_result, "Harbor OpenCode profile setup")
    # The bridge itself runs as this default task user.  Verify the exact
    # launcher under that identity before a costly model call begins.
    import_result = await environment.exec(
      command=f"{_PYTHON} -Es -c 'import nonoka, nonoka_cli, nonoka_cli.benchmark.watchdog'"
    )
    self._require_success(import_result, "Harbor bridge runtime verification")

  async def run(
    self,
    instruction: str,
    environment: BaseEnvironment,
    context: AgentContext,
  ) -> None:
    """Run OpenCode against its native task tools using the nonoka provider."""
    if not _HAS_HARBOR:  # pragma: no cover - direct tests exercise helpers only.
      raise RuntimeError("Harbor is required to run the OpenCode benchmark adapter")

    watchdog_command = " ".join(
      [
        shlex.quote(_PYTHON),
        "-Es",
        "-m",
        "nonoka_cli.benchmark.watchdog",
        "--timeout",
        shlex.quote(str(self._run_timeout_seconds)),
        "--grace",
        "5",
        "--log",
        shlex.quote(f"{_AGENT_LOG_DIR}/opencode.txt"),
        "--evidence-log",
        shlex.quote(f"{_AGENT_LOG_DIR}/run-evidence.ndjson"),
        "--artifact-dir",
        shlex.quote("/logs/artifacts/agent"),
        "--allow-scorable-budget-exit",
        "--",
        shlex.quote(_OPENCODE),
        "--model=nonoka/default",
        "run",
        "--format=json",
        "--thinking",
        "--",
        shlex.quote(instruction),
      ]
    )
    # Keep launcher failures observable too. The watchdog captures OpenCode's
    # own NDJSON, but an incompatible task image can fail before the watchdog
    # starts (for example, an unusable staged Python interpreter). Harbor's
    # environment result does not otherwise persist that shell diagnostic.
    command = (
      f"mkdir -p {_AGENT_LOG_DIR} /logs/artifacts/agent; "
      f"{watchdog_command} > {_AGENT_LOG_DIR}/watchdog-launcher.log 2>&1; "
      "WATCHDOG_STATUS=$?; "
      f"cp {_AGENT_LOG_DIR}/watchdog-launcher.log /logs/artifacts/agent/; "
      "exit $WATCHDOG_STATUS"
    )
    result = await environment.exec(
      command=command,
      timeout_sec=self._run_timeout_seconds + 15,
      env={
        "OPENCODE_FAKE_VCS": "git",
        "NONOKA_PROVIDER_LOG_PATH": f"{_AGENT_LOG_DIR}/provider.log",
        "NONOKA_LOG_FILE": f"{_AGENT_LOG_DIR}/bridge-server.log",
        "NONOKA_TRACE_DIR": f"{_AGENT_LOG_DIR}/bridge-events",
        "NONOKA_TIMELINE_PATH": f"{_AGENT_LOG_DIR}/bridge-timeline.ndjson",
        "NONOKA_RUN_EVIDENCE_PATH": f"{_AGENT_LOG_DIR}/run-evidence.ndjson",
        "NONOKA_PROTECTED_PATHS": "/tests",
      },
    )
    if result.return_code == 124:
      raise TimeoutError(f"OpenCode process group exceeded {self._run_timeout_seconds} seconds")
    if result.return_code != 0:
      raise RuntimeError(f"OpenCode exited with status {result.return_code}")

    # Harbor's official OpenCode adapter uses this hook to parse the saved
    # NDJSON into an ATIF trajectory.  Preserve it for artifacts/diagnostics.
    populate = getattr(super(), "populate_context_post_run", None)
    if callable(populate):
      populate(context)
