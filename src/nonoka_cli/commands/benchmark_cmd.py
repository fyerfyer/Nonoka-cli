"""Reproducible OpenCode bridge benchmark commands."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from nonoka_cli.benchmark import swe_bench
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.scorecard import (
  LaneOutcome,
  RuntimeBudgets,
  build_scorecard,
  read_lane_outcome,
)

TERMINAL_BENCH_TASKS = (
  "adaptive-rejection-sampler",
  "break-filter-js-from-html",
  "cancel-async-tasks",
  "configure-git-webserver",
  "count-dataset-tokens",
  "db-wal-recovery",
  "query-optimize",
  "regex-log",
  "sanitize-git-repo",
  "sqlite-db-truncate",
)

_SECRET_PATTERNS = (
  re.compile(r"\b(?:sk|rk|AIza)[-_A-Za-z0-9]{16,}\b"),
  re.compile(r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*[^\s\"']+"),
)


def _redact_text(value: str) -> str:
  """Remove common credential forms before persisting a benchmark artifact."""
  for pattern in _SECRET_PATTERNS:
    value = pattern.sub("[REDACTED]", value)
  return value


def _version(command: list[str]) -> str | None:
  try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
  except (OSError, subprocess.TimeoutExpired):
    return None
  return result.stdout.strip() if result.returncode == 0 else None


def _artifact_dir(value: str | None) -> Path:
  if value:
    return Path(value).expanduser().resolve()
  stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  return (Path.cwd() / ".nonoka" / "eval" / "opencode" / stamp).resolve()


def _write_manifest(directory: Path, args: argparse.Namespace, command: list[str]) -> None:
  directory.mkdir(parents=True, exist_ok=True)
  payload = {
    "schema_version": 1,
    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "mode": args.mode,
    "model": args.model,
    "temperature": args.temperature,
    "max_turns": args.max_turns,
    "timeout_seconds": args.timeout,
    "run_timeout_seconds": getattr(args, "run_timeout", None),
    "tool_budget": args.tool_budget,
    "cwd": str(Path(args.cwd).resolve()),
    "config": getattr(
      args,
      "_benchmark_config",
      str(Path(args.config).expanduser().resolve()) if args.config else None,
    ),
    "provider_source": getattr(
      args,
      "_resolved_provider_source",
      str(Path(args.provider_source).resolve()) if args.provider_source else None,
    ),
    "runtime_artifacts": getattr(args, "_runtime_artifacts", None),
    "opencode_version": _version(["opencode", "--version"]),
    "harbor_version": _version(["harbor", "--version"]),
    "python": sys.version,
    "platform": platform.platform(),
    "tasks": list(getattr(args, "tasks", None) or TERMINAL_BENCH_TASKS),
    "command": command,
    "credential_policy": (
      "Credentials are supplied only through environment variables and are excluded from artifacts."
    ),
  }
  (directory / "manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )


def _common_env(directory: Path) -> dict[str, str]:
  env = os.environ.copy()
  env["NONOKA_TRACE_DIR"] = str(directory / "bridge-events")
  env["NONOKA_TIMELINE_PATH"] = str(directory / "timeline.ndjson")
  env["NONOKA_PROVIDER_LOG_PATH"] = str(directory / "provider.log")
  env["NONOKA_LOG_FILE"] = str(directory / "server.log")
  env["XDG_CONFIG_HOME"] = str(directory / "xdg-config")
  return env


def _harbor_env(directory: Path) -> dict[str, str]:
  """Return Harbor's environment with its unsupported SOCKS proxy removed.

  Harbor's registry client currently constructs an httpx client without SOCKS
  transport support. A local ``ALL_PROXY=socks://...`` therefore prevents
  even public dataset metadata from loading. Scope this workaround to the
  Harbor child process; it never changes the user's shell environment.
  """
  env = _common_env(directory)
  for key in ("ALL_PROXY", "all_proxy"):
    if env.get(key, "").lower().startswith("socks://"):
      env.pop(key, None)
  return env


def _default_provider_source() -> Path | None:
  """Return this checkout's built provider package when available."""
  source = Path(__file__).resolve().parents[3] / "packages" / "nonoka-opencode-provider"
  return source if (source / "package.json").is_file() and (source / "dist").is_dir() else None


def _checkout_root() -> Path:
  """Return the nonoka-cli checkout containing this benchmark command."""
  return Path(__file__).resolve().parents[3]


def _build_runtime_wheel(*, directory: Path, project: Path, distribution: str) -> Path:
  """Build a current wheel without modifying the checkout's ``dist/`` directory."""
  uv = shutil.which("uv")
  if not uv:
    raise ValueError("uv is required to build isolated Harbor runtime wheels")
  if not (project / "pyproject.toml").is_file():
    raise ValueError(f"Cannot build {distribution}; missing pyproject.toml at {project}")

  wheel_dir = directory / "runtime-wheels"
  wheel_dir.mkdir(parents=True, exist_ok=True)
  command = [uv, "build", "--wheel", "--out-dir", str(wheel_dir)]
  result = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False)
  with (directory / "runtime-build.log").open("a", encoding="utf-8") as log:
    log.write(f"$ {' '.join(command)}  # cwd={project}\n")
    log.write(_redact_text(result.stdout))
    log.write(_redact_text(result.stderr))
  if result.returncode:
    raise ValueError(f"Failed to build {distribution} runtime wheel; see runtime-build.log")

  normalized = distribution.replace("-", "_")
  candidates = sorted(wheel_dir.glob(f"{normalized}-*.whl"), key=lambda item: item.stat().st_mtime)
  if not candidates:
    raise ValueError(f"uv build did not produce a {distribution} wheel")
  return candidates[-1]


def _stage_provider_source(args: argparse.Namespace, directory: Path) -> Path:
  """Copy the built, self-contained provider required inside Harbor tasks."""
  source = (
    Path(args.provider_source).expanduser().resolve()
    if args.provider_source
    else _default_provider_source()
  )
  if source is None:
    raise ValueError("No built local provider is available; pass --provider-source with dist/.")
  if not (source / "package.json").is_file() or not (source / "dist").is_dir():
    raise ValueError("--provider-source must contain package.json and a built dist/ directory")
  if not (source / "node_modules").is_dir():
    raise ValueError(
      "--provider-source must include node_modules for offline Harbor execution; "
      "run `bun install` in the provider directory first"
    )

  staged = directory / "runtime-provider"
  if staged.exists():
    shutil.rmtree(staged)
  staged.mkdir(parents=True)
  shutil.copy2(source / "package.json", staged / "package.json")
  shutil.copytree(source / "dist", staged / "dist")
  shutil.copytree(source / "node_modules", staged / "node_modules", symlinks=True)
  return staged


def _stage_uv_binary(directory: Path) -> Path:
  """Copy the host UV executable so task Python setup is UV-managed."""
  uv = shutil.which("uv")
  if not uv:
    raise ValueError("uv is required to provision the Harbor bridge runtime")
  source = Path(uv).resolve()
  if not source.is_file():
    raise ValueError(f"uv executable was not found: {source}")
  staged = directory / "runtime-uv"
  staged.mkdir(parents=True, exist_ok=True)
  target = staged / "uv"
  shutil.copy2(source, target)
  target.chmod(target.stat().st_mode | 0o111)
  return target


def _stage_search_binary(directory: Path) -> Path:
  """Stage ripgrep for benchmark images that lack OpenCode's search dependency."""
  rg = shutil.which("rg")
  if not rg:
    raise ValueError("rg is required to provision benchmark search tools")
  source = Path(rg).resolve()
  if not source.is_file():
    raise ValueError(f"rg executable was not found: {source}")
  staged = directory / "runtime-tools"
  staged.mkdir(parents=True, exist_ok=True)
  target = staged / "rg"
  shutil.copy2(source, target)
  target.chmod(target.stat().st_mode | 0o111)
  return target


def _stage_python_runtime_archive(directory: Path) -> Path:
  """Package the host's uv-managed Python 3.13 for offline task setup.

  Terminal-Bench images are intentionally heterogeneous and many do not ship
  Python 3.13.  Downloading it independently inside every task container makes
  bridge startup depend on transient network access and discards the host uv
  cache.  A uv-managed CPython install is relocatable on compatible Linux
  images, so stage it alongside the other immutable runtime artifacts.  The
  adapter still validates it in the container and retains its system/uv
  fallbacks for incompatible images.
  """
  uv = shutil.which("uv")
  if not uv:
    raise ValueError("uv is required to locate the Harbor Python runtime")
  tar = shutil.which("tar")
  if not tar:
    raise ValueError("tar is required to package the Harbor Python runtime")

  located = subprocess.run(
    [uv, "python", "find", "3.13"],
    capture_output=True,
    text=True,
    check=False,
  )
  executable = Path(located.stdout.strip()).expanduser().resolve()
  runtime = executable.parent.parent
  if located.returncode or not executable.is_file() or not (runtime / "bin").is_dir():
    raise ValueError("A uv-managed Python 3.13 runtime is required; run `uv python install 3.13`")

  staged = directory / "runtime-python"
  staged.mkdir(parents=True, exist_ok=True)
  archive = staged / "python-3.13.tar.gz"
  packed = subprocess.run(
    [tar, "-czf", str(archive), "-C", str(runtime.parent), runtime.name],
    capture_output=True,
    text=True,
    check=False,
  )
  if packed.returncode or not archive.is_file():
    raise ValueError("Failed to package the Harbor Python 3.13 runtime")
  return archive


def _stage_runtime_site_packages(*, directory: Path, agent_wheel: Path, cli_wheel: Path) -> Path:
  """Materialize bridge dependencies for network-free task-container startup.

  Harbor task containers intentionally have no dependable package-index
  access.  Installing just the two local wheels in the container therefore
  leaves the runtime partially installed when their dependencies cannot be
  resolved.  Build a disposable, non-editable venv on the host and archive
  its site-packages tree instead.  The task adapter creates its own venv
  against the staged Python runtime, then overlays this portable package
  tree.
  """
  uv = shutil.which("uv")
  tar = shutil.which("tar")
  if not uv or not tar:
    raise ValueError("uv and tar are required to stage Harbor Python dependencies")

  runtime = subprocess.run(
    [uv, "python", "find", "3.13"], capture_output=True, text=True, check=False
  )
  python = Path(runtime.stdout.strip()).expanduser().resolve()
  if runtime.returncode or not python.is_file():
    raise ValueError("A uv-managed Python 3.13 runtime is required for Harbor dependencies")

  staging = directory / "runtime-site-packages-staging"
  if staging.exists():
    shutil.rmtree(staging)
  venv = staging / "venv"
  archive_dir = directory / "runtime-site-packages"
  archive_dir.mkdir(parents=True, exist_ok=True)
  archive = archive_dir / "site-packages.tar.gz"

  commands = (
    [uv, "venv", str(venv), "--python", str(python)],
    [
      uv,
      "pip",
      "install",
      "--link-mode=copy",
      "--python",
      str(venv / "bin" / "python"),
      str(agent_wheel),
      str(cli_wheel),
    ],
  )
  try:
    for command in commands:
      result = subprocess.run(command, capture_output=True, text=True, check=False)
      with (directory / "runtime-build.log").open("a", encoding="utf-8") as log:
        log.write(f"$ {' '.join(command)}\n")
        log.write(_redact_text(result.stdout))
        log.write(_redact_text(result.stderr))
      if result.returncode:
        raise ValueError("Failed to materialize Harbor runtime dependencies; see runtime-build.log")

    location = subprocess.run(
      [
        str(venv / "bin" / "python"),
        "-c",
        "import sysconfig; print(sysconfig.get_paths()['purelib'])",
      ],
      capture_output=True,
      text=True,
      check=False,
    )
    site_packages = Path(location.stdout.strip()).resolve()
    if location.returncode or not site_packages.is_dir() or venv not in site_packages.parents:
      raise ValueError("Could not locate materialized Harbor site-packages")
    relative = site_packages.relative_to(venv)
    packed = subprocess.run(
      [tar, "-czf", str(archive), "-C", str(venv), str(relative)],
      capture_output=True,
      text=True,
      check=False,
    )
    if packed.returncode or not archive.is_file():
      raise ValueError("Failed to package Harbor runtime dependencies")
    return archive
  finally:
    shutil.rmtree(staging, ignore_errors=True)


def _stage_opencode_binary(directory: Path) -> Path:
  """Copy a verified host OpenCode executable for network-free task setup."""
  opencode = shutil.which("opencode")
  if not opencode:
    raise ValueError("opencode is required to provision the Harbor bridge runtime")
  source = Path(opencode).resolve()
  if not source.is_file():
    raise ValueError(f"opencode executable was not found: {source}")
  staged = directory / "runtime-opencode"
  staged.mkdir(parents=True, exist_ok=True)
  target = staged / "opencode"
  shutil.copy2(source, target)
  target.chmod(target.stat().st_mode | 0o111)
  return target


def _prepare_harbor_runtime(args: argparse.Namespace, directory: Path) -> dict[str, str]:
  """Build and stage the exact bridge bits copied into every task container."""
  root = _checkout_root()
  agent_root = root.parent / "nonoka-agent"
  cli_wheel = _build_runtime_wheel(directory=directory, project=root, distribution="nonoka-cli")
  agent_wheel = _build_runtime_wheel(directory=directory, project=agent_root, distribution="nonoka")
  site_packages_archive = _stage_runtime_site_packages(
    directory=directory, agent_wheel=agent_wheel, cli_wheel=cli_wheel
  )
  provider = _stage_provider_source(args, directory)
  uv_binary = _stage_uv_binary(directory)
  _stage_search_binary(directory)
  python_runtime_archive = _stage_python_runtime_archive(directory)
  opencode_binary = _stage_opencode_binary(directory)
  return {
    "cli_wheel": str(cli_wheel),
    "agent_wheel": str(agent_wheel),
    "site_packages_archive": str(site_packages_archive),
    "provider_source": str(provider),
    "uv_binary": str(uv_binary),
    "python_runtime_archive": str(python_runtime_archive),
    "opencode_binary": str(opencode_binary),
  }


def _api_key_env_for_model(model: str) -> str:
  """Map common model names to the environment key consumed by nonoka."""
  lowered = model.lower()
  if "deepseek" in lowered:
    return "DEEPSEEK_API_KEY"
  if "anthropic" in lowered or "claude" in lowered:
    return "ANTHROPIC_API_KEY"
  if "openrouter" in lowered:
    return "OPENROUTER_API_KEY"
  if "gemini" in lowered or "google" in lowered:
    return "GOOGLE_API_KEY"
  return "OPENAI_API_KEY"


def _prepare_provider_source(args: argparse.Namespace) -> Path | None:
  """Expose a locally built provider package to OpenCode for an eval run."""
  source = (
    Path(args.provider_source).expanduser().resolve()
    if args.provider_source
    else _default_provider_source()
  )
  if source is None:
    return None
  package = source / "package.json"
  build = source / "dist"
  if not package.is_file() or not build.is_dir():
    raise ValueError("--provider-source must contain package.json and a built dist/ directory")
  link = Path(args.cwd).resolve() / "node_modules" / "nonoka-opencode-provider"
  link.parent.mkdir(parents=True, exist_ok=True)
  if link.exists() or link.is_symlink():
    if link.resolve() != source:
      raise ValueError(f"Provider path already exists and does not point to {source}: {link}")
    return source
  link.symlink_to(source, target_is_directory=True)
  return source


def _benchmark_config(args: argparse.Namespace, directory: Path) -> Path:
  """Create a self-contained config when no user config was supplied."""
  if args.config:
    path = Path(args.config).expanduser().resolve()
    if not path.is_file():
      raise ValueError(f"--config does not exist: {path}")
    return path
  path = directory / "nonoka.benchmark.yaml"
  values: dict[str, Any] = {
    "model": args.model,
    "cli": {"auto_approve": True},
  }
  if args.max_turns is not None:
    values["agents"] = {"executor": {"max_turns": args.max_turns}}
  ConfigLoader.save(CLIConfig(**values), path)
  return path


def _write_opencode_profile(
  args: argparse.Namespace,
  directory: Path,
  config_path: Path,
  provider_source: Path,
) -> Path:
  """Write a temporary project profile that pins the local bridge process."""
  workspace = Path(args.cwd).resolve()
  profile = workspace / "opencode.json"
  if profile.exists():
    raise ValueError(
      f"Benchmark workspace already has {profile}; use a clean --cwd so the run is isolated."
    )
  payload = {
    "$schema": "https://opencode.ai/config.json",
    "autoupdate": False,
    "model": "nonoka/default",
    "provider": {
      "nonoka": {
        "npm": f"file:{provider_source}",
        "name": "Nonoka benchmark bridge",
        "options": {
          "serverCommand": [sys.executable, "-m", "nonoka_cli", "--server"],
          "cwd": str(workspace),
          "configPath": str(config_path),
          "model": args.model,
          "temperature": args.temperature,
          "requireFocusedVerification": True,
          "verificationEnforcement": "advisory",
        },
        "models": {"default": {"name": f"Nonoka {args.model}"}},
      }
    },
    "permission": {"*": "allow"},
    "tools": {"skill": False},
  }
  options = payload["provider"]["nonoka"]["options"]
  if args.max_turns is not None:
    options["maxTurns"] = args.max_turns
  if args.timeout is not None:
    options["timeoutSeconds"] = args.timeout
  if args.tool_budget is not None:
    options["toolBudget"] = args.tool_budget
  profile.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  (directory / "opencode.profile.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  return profile


def cmd_smoke(args: argparse.Namespace) -> int:
  if not shutil.which("opencode"):
    print("Error: opencode is not installed.", file=sys.stderr)
    return 2
  directory = _artifact_dir(args.artifact_dir)
  temporary_profile: Path | None = None
  try:
    if args.mode == "opencode-nonoka":
      source = _prepare_provider_source(args)
      if source is None:
        raise ValueError(
          "No built local provider is available; pass --provider-source to a package with dist/."
        )
      args._resolved_provider_source = str(source)
      config_path = _benchmark_config(args, directory)
      args._benchmark_config = str(config_path)
      temporary_profile = _write_opencode_profile(args, directory, config_path, source)
  except ValueError as exc:
    print(f"Error: {exc}", file=sys.stderr)
    return 2
  command = ["opencode", "run", "--format", "json", "--dir", str(Path(args.cwd).resolve())]
  if args.mode == "opencode-nonoka":
    command.extend(["--model", "nonoka/default"])
  elif args.model:
    command.extend(["--model", args.model])
  command.append(args.message)
  _write_manifest(directory, args, command)
  try:
    result = subprocess.run(
      command,
      cwd=args.cwd,
      capture_output=True,
      text=True,
      env=_common_env(directory),
      check=False,
    )
    (directory / "opencode.stdout.ndjson").write_text(_redact_text(result.stdout), encoding="utf-8")
    (directory / "opencode.stderr.log").write_text(_redact_text(result.stderr), encoding="utf-8")
    (directory / "result.json").write_text(
      json.dumps({"returncode": result.returncode}, indent=2) + "\n", encoding="utf-8"
    )
  finally:
    if temporary_profile is not None:
      temporary_profile.unlink(missing_ok=True)
  print(f"Artifacts: {directory}")
  return result.returncode


def cmd_terminal_bench(args: argparse.Namespace) -> int:
  if not shutil.which("harbor"):
    print(
      "Error: harbor is not installed. Run `nonoka-cli doctor --check-benchmarks`.",
      file=sys.stderr,
    )
    return 2
  docker = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
  if docker.returncode:
    print("Error: Docker daemon is unavailable for Harbor.", file=sys.stderr)
    return 2
  workspace = Path(args.cwd).expanduser().resolve()
  if workspace.exists() and not workspace.is_dir():
    print(f"Error: --cwd is not a directory: {workspace}", file=sys.stderr)
    return 2
  workspace.mkdir(parents=True, exist_ok=True)
  args.cwd = str(workspace)
  directory = _artifact_dir(args.artifact_dir)
  directory.mkdir(parents=True, exist_ok=True)
  agent = (
    "nonoka_cli.benchmark.harbor:OpenCodeHarborAgent"
    if args.mode == "opencode-nonoka"
    else "nonoka.ext.eval.harbor:NonokaHarborAgent"
  )
  try:
    runtime = _prepare_harbor_runtime(args, directory) if args.mode == "opencode-nonoka" else {}
  except ValueError as exc:
    print(f"Error: {exc}", file=sys.stderr)
    return 2
  args._runtime_artifacts = runtime or None
  command = [
    "harbor",
    "run",
    "--dataset",
    "terminal-bench@2.0",
    "--agent",
    agent,
    "--model",
    args.model,
    "--n-concurrent",
    "1",
    "--yes",
    "--jobs-dir",
    str(directory / "harbor-jobs"),
  ]
  if args.mode == "opencode-nonoka":
    for key, value in runtime.items():
      command.extend(["--agent-kwarg", f"{key}={value}"])
    command.extend(
      [
        "--agent-kwarg",
        f"temperature={args.temperature}",
        "--agent-kwarg",
        f"run_timeout_seconds={args.run_timeout}",
      ]
    )
    if args.timeout is not None:
      command.extend(["--agent-kwarg", f"timeout_seconds={args.timeout}"])
    if args.max_turns is not None:
      command.extend(["--agent-kwarg", f"max_turns={args.max_turns}"])
    if args.tool_budget is not None:
      command.extend(["--agent-kwarg", f"tool_budget={args.tool_budget}"])
    if not getattr(args, "install_only", False):
      api_key_env = _api_key_env_for_model(args.model)
      forwarded_env: list[str] = []
      if os.environ.get(api_key_env):
        forwarded_env.append(api_key_env)
      # OpenAI-compatible gateways commonly serve models whose alias still
      # contains "deepseek". Forward the generic endpoint variables when
      # configured instead of forcing the container onto DeepSeek's public
      # endpoint or dropping the user's base URL.
      for env_name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if os.environ.get(env_name) and env_name not in forwarded_env:
          forwarded_env.append(env_name)
      if not any(name.endswith("API_KEY") for name in forwarded_env):
        print(
          f"Error: no API credential is configured for model '{args.model}'.",
          file=sys.stderr,
        )
        return 2
      for env_name in forwarded_env:
        command.extend(["--agent-env", f"{env_name}=${{{env_name}}}"])
  if getattr(args, "install_only", False):
    command.append("--install-only")
  for task in getattr(args, "tasks", None) or TERMINAL_BENCH_TASKS:
    command.extend(["--include-task-name", task])
  _write_manifest(directory, args, command)
  result = subprocess.run(command, cwd=args.cwd, env=_harbor_env(directory), check=False)
  (directory / "result.json").write_text(
    json.dumps({"returncode": result.returncode, "official_artifact": "harbor-jobs"}, indent=2)
    + "\n",
    encoding="utf-8",
  )
  print(f"Artifacts: {directory}")
  return result.returncode


def cmd_swe_bench(args: argparse.Namespace) -> int:
  """Generate bridge patches in official images, then run the verifier."""
  directory = _artifact_dir(args.artifact_dir)
  args.artifact_dir = str(directory)
  if not args.predictions:
    if not args.instance_ids:
      print("Error: automatic generation requires at least one --instance-id.", file=sys.stderr)
      return 2
    try:
      _prepare_harbor_runtime(args, directory)
    except ValueError as exc:
      print(f"Error: {exc}", file=sys.stderr)
      return 2
    python = args.swebench_python or os.environ.get("NONOKA_SWEBENCH_PYTHON")
    if not python:
      print("Error: pass --swebench-python with the official harness environment.", file=sys.stderr)
      return 2
    command = [
      python,
      "-m",
      "nonoka_cli.benchmark.swe_runner",
      "--runtime-root",
      str(directory),
      "--output",
      str(directory),
      "--model",
      args.model,
      "--temperature",
      str(args.temperature),
      "--run-timeout",
      str(args.run_timeout),
      "--max-workers",
      str(args.max_workers),
      "--run-id",
      directory.name,
      "--dataset",
      args.dataset_path or swe_bench.SWE_BENCH_LITE,
    ]
    for instance_id in args.instance_ids:
      command.extend(["--instance-id", instance_id])
    environment = _harbor_env(directory)
    result = subprocess.run(command, env=environment, check=False)
    if result.returncode:
      return result.returncode
    args.predictions = str(directory / "predictions.jsonl")
  return swe_bench.run(args, common_env=_common_env(directory), redact=_redact_text)


def _lane_outcome(path: str | None) -> LaneOutcome:
  """Load an optional lane outcome, defaulting to a pending lane."""
  if path is None:
    return LaneOutcome()
  return read_lane_outcome(Path(path).expanduser().resolve())


def cmd_scorecard(args: argparse.Namespace) -> int:
  """Write a fixed release manifest without collapsing evaluation lanes."""
  output = Path(args.output).expanduser().resolve()
  artifact_root = Path(args.artifact_root or output.parent).expanduser().resolve()
  root = _checkout_root()
  scorecard = build_scorecard(
    release_candidate=args.release_candidate,
    cli_root=root,
    framework_root=root.parent / "nonoka-agent",
    model=args.model,
    temperature=args.temperature,
    budgets=RuntimeBudgets(
      max_turns=args.max_turns,
      tool_budget=args.tool_budget,
      timeout_seconds=args.timeout,
      wall_timeout_seconds=args.run_timeout,
      max_context_bytes=args.max_context_bytes,
      max_cost_usd=args.max_cost_usd,
    ),
    sample_ids=list(args.sample_ids),
    verifier=args.verifier,
    artifact_root=artifact_root,
    deterministic=_lane_outcome(args.deterministic_outcome),
    framework=_lane_outcome(args.framework_outcome),
    opencode=_lane_outcome(args.opencode_outcome),
  )
  try:
    scorecard.write(output)
  except FileExistsError as exc:
    print(f"Error: {exc}", file=sys.stderr)
    return 2
  print(f"Scorecard: {output}")
  return 0


def add_subparser(subparsers: Any) -> None:
  parser = subparsers.add_parser("benchmark", help="Run reproducible OpenCode bridge benchmarks")
  common = argparse.ArgumentParser(add_help=False)
  common.add_argument("--config")
  common.add_argument("--cwd", default=".")
  common.add_argument("--model", default="deepseek/deepseek-v4-pro")
  common.add_argument("--temperature", type=float, default=0.0)
  common.add_argument(
    "--max-turns",
    type=int,
    default=None,
    help="Optional cumulative model-turn budget (default: unlimited).",
  )
  common.add_argument(
    "--timeout",
    type=float,
    default=None,
    help="Optional timeout for each model API call (default: unlimited).",
  )
  common.add_argument(
    "--run-timeout",
    type=float,
    default=3600.0,
    help="Hard per-task benchmark deadline in seconds (separate from LLM-call timeout).",
  )
  common.add_argument(
    "--tool-budget",
    type=int,
    default=None,
    help="Optional cumulative tool-call budget (default: unlimited).",
  )
  common.add_argument("--artifact-dir")
  common.add_argument("--provider-source")
  modes = ("opencode-nonoka", "opencode-direct", "nonoka-framework")
  children = parser.add_subparsers(dest="benchmark_command", required=True)
  smoke = children.add_parser("smoke", parents=[common], help="Run OpenCode JSON-mode smoke test")
  smoke.add_argument("--mode", choices=modes[:2], default="opencode-nonoka")
  smoke.add_argument(
    "--message",
    default="Create smoke.txt containing exactly nonoka bridge smoke, then read it.",
  )
  smoke.set_defaults(func=cmd_smoke)
  terminal = children.add_parser(
    "terminal-bench", parents=[common], help="Run the pinned Terminal-Bench 2 slice"
  )
  terminal.add_argument(
    "--mode", choices=("opencode-nonoka", "nonoka-framework"), default="opencode-nonoka"
  )
  terminal.add_argument(
    "--task",
    dest="tasks",
    action="append",
    choices=TERMINAL_BENCH_TASKS,
    help="Run one or more tasks from the pinned slice (defaults to all ten).",
  )
  terminal.add_argument(
    "--install-only",
    action="store_true",
    help="Provision the adapter in the selected task containers without scoring them.",
  )
  terminal.set_defaults(func=cmd_terminal_bench)
  swe = children.add_parser(
    "swe-bench",
    parents=[common],
    help="Verify SWE-bench Lite predictions with the official harness",
  )
  swe.add_argument(
    "--instance-id",
    dest="instance_ids",
    action="append",
    help="Run an explicit Lite instance; permits constrained-host diagnostics.",
  )
  swe.add_argument(
    "--predictions", help="Official predictions.jsonl from the OpenCode/Nonoka bridge."
  )
  swe.add_argument(
    "--swebench-python",
    help="Python interpreter with the official swebench package installed.",
  )
  swe.add_argument(
    "--max-workers",
    type=int,
    default=2,
    help="Maximum concurrent SWE-bench agent/verifier workers.",
  )
  swe.add_argument(
    "--dataset-path",
    help="Local official SWE-bench JSON dataset; avoids Hub access during a run.",
  )
  swe.add_argument(
    "--skip-verify",
    action="store_true",
    help="Write the reproducibility manifest without calling the official verifier.",
  )
  swe.set_defaults(func=cmd_swe_bench)
  scorecard = children.add_parser(
    "scorecard",
    help="Write a lane-separated release-candidate scorecard",
  )
  scorecard.add_argument("--output", required=True)
  scorecard.add_argument("--release-candidate", required=True)
  scorecard.add_argument("--artifact-root")
  scorecard.add_argument("--model", required=True)
  scorecard.add_argument("--temperature", type=float, default=0.0)
  scorecard.add_argument("--max-turns", type=int)
  scorecard.add_argument("--tool-budget", type=int)
  scorecard.add_argument("--timeout", type=float)
  scorecard.add_argument("--run-timeout", type=float)
  scorecard.add_argument("--max-context-bytes", type=int)
  scorecard.add_argument("--max-cost-usd", type=float)
  scorecard.add_argument(
    "--sample-id",
    dest="sample_ids",
    action="append",
    required=True,
  )
  scorecard.add_argument("--verifier", required=True)
  scorecard.add_argument("--deterministic-outcome")
  scorecard.add_argument("--framework-outcome")
  scorecard.add_argument("--opencode-outcome")
  scorecard.set_defaults(func=cmd_scorecard)
