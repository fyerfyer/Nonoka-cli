"""Diagnose the nonoka + OpenCode installation."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@dataclass
class CheckResult:
  """Result of a single doctor check."""

  status: str  # "ok", "warn", "error"
  message: str
  remedy: str = ""


_STATUS_ICONS = {
  "ok": "✓",
  "warn": "⚠",
  "error": "✗",
}


def _api_key_env_for_model(model: str) -> str:
  """Return a conventional API-key env var name for a model identifier."""
  lowered = model.lower()
  if "openai" in lowered or lowered.startswith("gpt-"):
    return "OPENAI_API_KEY"
  if "anthropic" in lowered or "claude" in lowered:
    return "ANTHROPIC_API_KEY"
  if "deepseek" in lowered:
    return "DEEPSEEK_API_KEY"
  if "openrouter" in lowered:
    return "OPENROUTER_API_KEY"
  if "gemini" in lowered or "google" in lowered:
    return "GOOGLE_API_KEY"
  return "OPENAI_API_KEY"


def _run(
  cmd: list[str],
  timeout: float = 10.0,
  check: bool = False,
) -> subprocess.CompletedProcess[str]:
  """Run a command and return its result."""
  return subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    timeout=timeout,
    check=check,
  )


def check_nonoka_cli_version() -> CheckResult:
  """Check the installed nonoka-cli version."""
  try:
    version = importlib.metadata.version("nonoka-cli")
    return CheckResult("ok", f"nonoka-cli {version}")
  except Exception as exc:
    return CheckResult("error", "nonoka-cli version unknown", str(exc))


def check_python_version() -> CheckResult:
  """Check the Python interpreter version."""
  major, minor = sys.version_info[:2]
  if (major, minor) >= (3, 10):
    return CheckResult("ok", f"Python {major}.{minor}")
  return CheckResult(
    "error",
    f"Python {major}.{minor}",
    "Upgrade to Python 3.10 or newer.",
  )


def check_opencode() -> CheckResult:
  """Check whether OpenCode is installed and on PATH."""
  opencode = shutil.which("opencode")
  if not opencode:
    return CheckResult(
      "error",
      "opencode not found in PATH",
      "Install OpenCode: curl -fsSL https://opencode.ai/install | bash",
    )

  result = _run(["opencode", "--version"], timeout=5.0)
  version = result.stdout.strip() if result.returncode == 0 else "unknown"
  if result.returncode == 0:
    return CheckResult("ok", f"opencode {version}")
  return CheckResult(
    "warn",
    f"opencode found but returned an error (reported {version})",
    "Try reinstalling OpenCode.",
  )


def _provider_version_from_npm_global() -> str | None:
  """Parse the globally installed provider version from npm."""
  npm = shutil.which("npm")
  if not npm:
    return None
  try:
    result = _run(
      [npm, "list", "-g", "nonoka-opencode-provider", "--depth=0", "--json"],
      timeout=10.0,
    )
    if result.returncode != 0 and result.returncode != 1:
      return None
    data = json.loads(result.stdout)
    dependencies = data.get("dependencies", {})
    pkg = dependencies.get("nonoka-opencode-provider")
    if isinstance(pkg, dict):
      return pkg.get("version")
  except Exception:
    return None
  return None


def _provider_version_from_opencode_dir() -> str | None:
  """Look for the provider inside OpenCode's plugin directory."""
  candidate = (
    Path.home()
    / ".config"
    / "opencode"
    / "node_modules"
    / "nonoka-opencode-provider"
    / "package.json"
  )
  if not candidate.exists():
    return None
  try:
    data = json.loads(candidate.read_text(encoding="utf-8"))
    return data.get("version")
  except Exception:
    return None


def check_provider() -> CheckResult:
  """Check whether the OpenCode provider is installed."""
  version = _provider_version_from_npm_global() or _provider_version_from_opencode_dir()
  if version:
    return CheckResult("ok", f"provider nonoka-opencode-provider@{version}")
  return CheckResult(
    "warn",
    "provider nonoka-opencode-provider not found",
    "Install with: npm install -g nonoka-opencode-provider  (OpenCode may also auto-install it)",
  )


def check_harbor() -> CheckResult:
  """Check whether the official Terminal-Bench 2 runner is available."""
  harbor = shutil.which("harbor")
  if not harbor:
    return CheckResult(
      "warn",
      "harbor not found",
      "Install it in an isolated benchmark environment: uv tool install harbor",
    )
  result = _run([harbor, "--version"], timeout=10.0)
  if result.returncode == 0:
    return CheckResult("ok", f"harbor {result.stdout.strip()}")
  return CheckResult("warn", "harbor is installed but did not report a version")


def check_docker() -> CheckResult:
  """Verify real Docker daemon access, not merely the client binary."""
  docker = shutil.which("docker")
  if not docker:
    return CheckResult("error", "docker not found", "Install Docker before running Terminal-Bench.")
  result = _run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=10.0)
  if result.returncode == 0:
    return CheckResult("ok", f"docker daemon {result.stdout.strip()}")
  return CheckResult(
    "error",
    "docker daemon is unavailable",
    "Start Docker and grant the current user access to the Docker socket.",
  )


def check_config(config_path: str | None) -> tuple[CheckResult, CLIConfig | None]:
  """Check whether a nonoka config file exists and is valid."""
  try:
    cfg = ConfigLoader.load(config_path)
    path = config_path or ConfigLoader.find_config_file(config_path)
    return CheckResult("ok", f"config {path}"), cfg
  except ConfigError as exc:
    return (
      CheckResult(
        "error",
        f"config error: {exc}",
        "Run `nonoka-cli config init` to create a config file.",
      ),
      None,
    )
  except Exception as exc:
    return (
      CheckResult(
        "error",
        f"failed to load config: {exc}",
        "Check the file path and YAML syntax.",
      ),
      None,
    )


def check_api_key(config: CLIConfig | None) -> CheckResult:
  """Check whether the expected API key environment variable is set."""
  if config is None:
    return CheckResult(
      "warn",
      "API key not checked (no valid config)",
      "Fix the config first, then re-run doctor.",
    )

  env_var = _api_key_env_for_model(config.model)
  value = os.getenv(env_var)
  if value:
    return CheckResult("ok", f"API key {env_var} set")

  return CheckResult(
    "error",
    f"API key {env_var} not set",
    f"Export your key: export {env_var}=<your-key>",
  )


def _find_opencode_config() -> Path | None:
  """Find an OpenCode config file in priority order."""
  local = Path.cwd() / "opencode.json"
  if local.exists():
    return local
  global_ = Path.home() / ".config" / "opencode" / "opencode.json"
  if global_.exists():
    return global_
  return None


def check_opencode_config() -> CheckResult:
  """Check whether an OpenCode config references the nonoka provider."""
  path = _find_opencode_config()
  if not path:
    return CheckResult(
      "error",
      "OpenCode config (opencode.json) not found",
      "Run `nonoka-cli opencode init` (or `--global`) to create it.",
    )

  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except Exception as exc:
    return CheckResult(
      "error",
      f"OpenCode config {path} is not valid JSON: {exc}",
      "Fix or regenerate the file with `nonoka-cli opencode init`.",
    )

  provider = data.get("provider", {})
  if "nonoka" in provider:
    return CheckResult("ok", f"OpenCode provider config in {path}")

  return CheckResult(
    "error",
    f"OpenCode config {path} does not include the nonoka provider",
    "Run `nonoka-cli opencode init` to add the nonoka provider block.",
  )


async def _ping_llm(config: CLIConfig) -> CheckResult:
  """Try a minimal LLM call to verify the API key and network."""
  try:
    from nonoka import AgentBuilder, Runner
  except Exception as exc:
    return CheckResult(
      "error",
      f"cannot import nonoka: {exc}",
      "Ensure nonoka-cli dependencies are installed.",
    )

  try:
    agent = (
      AgentBuilder()
      .model(config.model)
      .system_prompt("You are a helpful assistant. Reply with the word 'ok' only.")
      .build()
    )
    runner = Runner()
    result = await asyncio.wait_for(
      runner.run_react(agent, "ping", deps=None),
      timeout=30.0,
    )
    if result.success:
      return CheckResult("ok", f"can reach {config.model} API")
    return CheckResult(
      "error",
      f"LLM ping failed: {result.error}",
      "Check your API key and model identifier.",
    )
  except Exception as exc:
    return CheckResult(
      "error",
      f"LLM ping failed: {exc}",
      "Check your API key, network, and model identifier.",
    )


def check_llm(config: CLIConfig | None) -> CheckResult:
  """Entry point for the optional LLM connectivity check."""
  if config is None:
    return CheckResult(
      "warn",
      "LLM connectivity not checked (no valid config)",
      "Fix the config first, then re-run doctor --check-llm.",
    )
  return asyncio.run(_ping_llm(config))


def run_doctor(args: argparse.Namespace) -> int:
  """Run all diagnostic checks and print a report."""
  results: list[CheckResult] = []

  results.append(check_nonoka_cli_version())
  results.append(check_python_version())
  results.append(check_opencode())
  results.append(check_provider())

  config_result, config = check_config(args.config)
  results.append(config_result)
  results.append(check_api_key(config))

  if getattr(args, "check_llm", False) is True:
    results.append(check_llm(config))

  results.append(check_opencode_config())
  if getattr(args, "check_benchmarks", False) is True:
    results.append(check_harbor())
    results.append(check_docker())

  for result in results:
    icon = _STATUS_ICONS.get(result.status, "?")
    print(f"{icon} {result.message}")
    if result.remedy:
      print(f"  → {result.remedy}")

  failed = any(r.status == "error" for r in results)
  return 1 if failed else 0


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
  parser.add_argument(
    "--config",
    dest="config",
    help="Path to the nonoka config file (default: auto-detect)",
  )
  parser.add_argument(
    "--check-benchmarks",
    action="store_true",
    help="Check Harbor and Docker prerequisites for Terminal-Bench 2.",
  )


def add_subparser(subparsers: Any) -> None:
  """Register the ``doctor`` subcommand."""
  parser = subparsers.add_parser(
    "doctor",
    help="Diagnose the nonoka + OpenCode installation",
  )
  _add_config_arg(parser)
  parser.add_argument(
    "--check-llm",
    action="store_true",
    help="Perform a real (small) LLM call to verify the API key (costs tokens)",
  )
  parser.set_defaults(func=run_doctor)
