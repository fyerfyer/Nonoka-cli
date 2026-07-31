"""OpenCode integration commands for nonoka-cli."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError

_PROVIDER_PACKAGE = "nonoka-opencode-provider"


def _resolve_provider_version() -> str | None:
  """Read the provider version from the monorepo package.json when available."""
  candidate = Path(__file__).resolve().parents[3] / "packages" / _PROVIDER_PACKAGE / "package.json"
  if candidate.exists():
    try:
      data = json.loads(candidate.read_text(encoding="utf-8"))
      return data.get("version")
    except (json.JSONDecodeError, OSError):
      pass
  return None


# Keep published CLI installs on a protocol-compatible provider even though
# their wheel does not contain the monorepo's package.json. This fallback must
# be bumped together with the provider package during a coordinated release.
_PROVIDER_VERSION = _resolve_provider_version() or "0.2.17"

# Tool categories that nonoka-cli needs OpenCode to auto-approve when
# ``cli.auto_approve`` is enabled. These are OpenCode's native permission
# keys, not nonoka-cli's internal tool names.
_OPENCODE_AUTO_APPROVED_TOOLS = ["read", "bash", "edit", "write", "todowrite"]


def _build_opencode_permission(config: CLIConfig) -> dict[str, str]:
  """Build an OpenCode permission block from nonoka's CLI config.

  When ``cli.auto_approve`` is true, allow the core coding tools so the
  agent can edit files and run commands without a HITL prompt. Otherwise
  require explicit approval for mutating operations.

  User-supplied ``permissions`` in nonoka.yaml override the defaults, letting
  nonoka.yaml remain the single source of truth for tool policies.
  """
  auto_approve = getattr(config.cli, "auto_approve", False)
  action = "allow" if auto_approve else "ask"
  permission: dict[str, str] = {"*": "ask"}
  for tool in _OPENCODE_AUTO_APPROVED_TOOLS:
    permission[tool] = action

  # nonoka.yaml permissions take precedence over auto_approve defaults.
  user_permissions = getattr(config, "permissions", None)
  if user_permissions:
    permission.update(user_permissions)
  # OpenCode 1.18 migrates ``tools.skill=false`` to a top-level permission,
  # but an agent-specific permission block overrides that migration.  Deny it
  # explicitly in both generated blocks so Build cannot prompt for the native
  # skill tool and bypass Nonoka's namespaced skill registry.
  permission["skill"] = "deny"
  return permission


_DEFAULT_OPENCODE_CONFIG = {
  "$schema": "https://opencode.ai/config.json",
  "model": "nonoka/default",
  # Disable OpenCode's auto-updater so that a verified provider version keeps
  # working. Silent upgrades have broken custom provider initialization in the
  # past (e.g. 1.17 -> 1.18 changed provider resolution).
  "autoupdate": False,
  "provider": {
    "nonoka": {
      "npm": "nonoka-opencode-provider",
      "name": "Nonoka",
      "options": {
        "serverCommand": ["nonoka-cli", "--server"],
        "cwd": ".",
        "requireFocusedVerification": True,
        "verificationEnforcement": "strict",
      },
      "models": {"default": {"name": "Nonoka Default"}},
    }
  },
  # Permission is regenerated from nonoka.yaml in cmd_init.
  "permission": {
    "*": "ask",
    "read": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "todowrite": "ask",
  },
  "agent": {
    "build": {
      "mode": "primary",
      # Permission is regenerated from nonoka.yaml in cmd_init.
      "permission": {
        "*": "ask",
        "read": "ask",
        "bash": "ask",
        "edit": "ask",
        "write": "ask",
        "todowrite": "ask",
      },
    }
  },
  # Disable OpenCode's native skill tool. nonoka-cli registers its own
  # skill__<skill>__<tool> and load_skill tools; leaving OpenCode's native
  # skill enabled would cause the model to receive conflicting instructions
  # (skill:<name> vs skill__<name>__<tool>).
  "tools": {"skill": False},
}


def _install_provider_locally(cwd: Path, version: str | None = None) -> bool:
  """Try to install the OpenCode provider package into the project directory.

  OpenCode 1.18+ resolves custom providers from the project's node_modules
  instead of auto-installing them into its own cache. We therefore install the
  provider as a dev dependency in the directory where opencode.json lives.
  """
  pkg_spec = _PROVIDER_PACKAGE
  if version:
    pkg_spec = f"{_PROVIDER_PACKAGE}@{version}"

  installed_manifest = cwd / "node_modules" / _PROVIDER_PACKAGE / "package.json"
  if installed_manifest.is_file():
    try:
      installed_version = json.loads(installed_manifest.read_text(encoding="utf-8")).get("version")
      if installed_version and (version is None or installed_version == version):
        print(f"Provider already ready: {_PROVIDER_PACKAGE}@{installed_version}")
        return True
      print(
        f"Provider version {installed_version or 'unknown'} does not match required "
        f"{version}; reinstalling."
      )
    except (OSError, json.JSONDecodeError):
      print("Provider manifest is unreadable; reinstalling.")

  # Prefer bun for speed, then npm/pnpm/yarn.
  managers = [
    ("bun", ["add", "-d", pkg_spec]),
    ("npm", ["install", "--save-dev", "--no-audit", "--no-fund", pkg_spec]),
    ("pnpm", ["add", "-D", pkg_spec]),
    ("yarn", ["add", "-D", pkg_spec]),
  ]

  for manager, args in managers:
    if not shutil.which(manager):
      continue
    print(f"Installing provider with {manager}...")
    try:
      result = subprocess.run(
        [manager, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
      )
      if result.returncode == 0:
        print(f"Provider installed locally at {cwd / 'node_modules' / _PROVIDER_PACKAGE}")
        return True
      print(f"{manager} install failed:\n{result.stderr.strip()}")
    except subprocess.TimeoutExpired:
      print(f"{manager} install timed out.")
    except OSError as exc:
      print(f"{manager} install error: {exc}")
  return False


_OPENCODE_PROMPT_GUIDELINES = (
  "\n"
  "OpenCode-specific guidelines:\n"
  "- You are running inside OpenCode. Use only the tools provided by OpenCode.\n"
  "- Tool names available in this environment include bash, read, write, edit, and todowrite.\n"
  "- For multi-step tasks, keep the OpenCode TODO panel up to date using the todowrite tool.\n"
  "- Inspect the smallest relevant part of the repository needed to implement the request.\n"
  "- When writing or editing files, use absolute paths under the current working directory "
  "unless the user provides a different path.\n"
  "- Prefer reading a file before editing it when you need context.\n"
  "- Prefix the final focused acceptance check with `NONOKA_VERIFY=focused`.\n"
)


def _build_opencode_agent_prompt(config: CLIConfig) -> str:
  """Wrap the canonical nonoka system prompt for OpenCode's agent file.

  nonoka owns the system prompt (via ``system_prompt`` in nonoka.yaml). The
  OpenCode adapter places it in ``.opencode/agents/build.md`` and appends
  OpenCode-specific tool guidelines so the model behaves correctly inside
  OpenCode without leaking frontend details into nonoka's core prompt.
  """
  body = config.system_prompt.strip() or "You are a helpful coding assistant."
  permission = _build_opencode_permission(config)
  yaml_lines = "\n".join(
    f"  {json.dumps(k)}: {v}" if k == "*" else f"  {k}: {v}" for k, v in permission.items()
  )
  hitl_note = (
    "- Tool calls are auto-approved because nonoka.yaml has cli.auto_approve enabled.\n"
    if getattr(config.cli, "auto_approve", False)
    else "- Tool calls require user approval; keep changes focused and explain commands.\n"
  )
  return f"""<!-- Generated by nonoka-cli; edit nonoka.yaml and rerun opencode init. -->
---
permission:
{yaml_lines}
---

{body}{_OPENCODE_PROMPT_GUIDELINES}{hitl_note}"""


def _load_config(args: argparse.Namespace, cwd: Path) -> tuple[CLIConfig, Path]:
  """Load the exact config that will be passed to the bridge.

  Configuration errors are deliberately not replaced with defaults.  A
  generated ``opencode.json`` that points at a missing or invalid file is a
  much harder failure to diagnose than a failed init.
  """
  path = ConfigLoader.find_config_file(
    getattr(args, "config", None),
    search_dir=cwd,
  )
  return ConfigLoader.load(path), path


def _global_opencode_path() -> Path:
  """Return the user-level OpenCode config path."""
  return Path.home() / ".config" / "opencode" / "opencode.json"


def cmd_init(args: argparse.Namespace) -> int:
  """Generate or merge an opencode.json for the current project."""
  cwd = Path(getattr(args, "cwd", ".")).expanduser().resolve()
  if not getattr(args, "global_", False):
    if not cwd.exists():
      print(f"Error: working directory does not exist: {cwd}")
      return 1
    if not cwd.is_dir():
      print(f"Error: working directory is not a directory: {cwd}")
      return 1

  try:
    config, resolved_config_path = _load_config(args, cwd)
  except ConfigError as exc:
    print(f"Error: cannot initialize OpenCode: {exc}")
    return 1

  print(f"Using Nonoka config: {resolved_config_path}")

  target: Path
  if args.global_:
    target = _global_opencode_path()
  else:
    target = cwd / "opencode.json"

  print(f"Writing OpenCode config to: {target}")

  existing: dict[str, Any] = {}
  if target.exists():
    if not target.is_file():
      print(f"OpenCode config path is not a file: {target}")
      return 1
    try:
      existing = json.loads(target.read_text(encoding="utf-8"))
      if not isinstance(existing, dict):
        print(f"OpenCode config must contain a top-level object: {target}")
        return 1
      print("Existing opencode.json found; merging provider block.")
    except (OSError, json.JSONDecodeError) as exc:
      print(f"Failed to parse existing {target}: {exc}")
      return 1

  merged = {**_DEFAULT_OPENCODE_CONFIG, **existing}

  # Inject/update the nonoka provider block.
  provider_block = copy.deepcopy(_DEFAULT_OPENCODE_CONFIG["provider"]["nonoka"])
  existing_provider = existing.get("provider", {})
  existing_nonoka = (
    existing_provider.get("nonoka", {}) if isinstance(existing_provider, dict) else {}
  )
  if isinstance(existing_nonoka, dict):
    existing_options = existing_nonoka.get("options", {})
    provider_block.update({k: v for k, v in existing_nonoka.items() if k != "options"})
    if isinstance(existing_options, dict):
      provider_block["options"].update(existing_options)
  if config.model:
    provider_block["models"] = {"default": {"name": f"Nonoka {config.model}"}}
  provider_block["options"]["configPath"] = str(resolved_config_path)
  provider_block["options"]["serverCommand"] = ["nonoka-cli", "--server"]

  if not isinstance(merged.get("provider"), dict):
    merged["provider"] = {}
  merged["provider"]["nonoka"] = provider_block

  # Reflect nonoka.yaml's cli.auto_approve in OpenCode's permission layer.
  permission_block = _build_opencode_permission(config)
  merged["permission"] = permission_block
  if not isinstance(merged.get("agent"), dict):
    merged["agent"] = {}
  merged["agent"].setdefault("build", {})
  if not isinstance(merged["agent"].get("build"), dict):
    merged["agent"]["build"] = {}
  merged["agent"]["build"]["permission"] = dict(permission_block)

  # Preserve unrelated OpenCode tool settings while always disabling the
  # conflicting native skill tool owned by OpenCode.
  existing_tools = existing.get("tools", {})
  tools_block = dict(existing_tools) if isinstance(existing_tools, dict) else {}
  tools_block["skill"] = False
  merged["tools"] = tools_block

  # This command configures the Nonoka execution path.  Keeping an unrelated
  # existing model would produce a valid-looking file that bypasses Nonoka.
  merged["model"] = "nonoka/default"

  # Install before writing managed OpenCode files.  Package managers may still
  # create their own lockfile, but a failed install no longer leaves a config
  # claiming that the provider is ready.
  if not args.global_:
    installed = _install_provider_locally(cwd, _PROVIDER_VERSION)
    if not installed:
      print("\nProvider is not ready; OpenCode config was not written.")
      print("To finish setup, run one of:")
      print(f"  bun add -d {_PROVIDER_PACKAGE}@{_PROVIDER_VERSION}")
      print(f"  npm install --save-dev {_PROVIDER_PACKAGE}@{_PROVIDER_VERSION}")
      print(f"  pnpm add -D {_PROVIDER_PACKAGE}@{_PROVIDER_VERSION}")
      return 1

  target.parent.mkdir(parents=True, exist_ok=True)
  temporary_target = target.with_name(f".{target.name}.nonoka-tmp")
  temporary_target.write_text(
    json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
  )
  temporary_target.replace(target)

  # Create the OpenCode agent prompt file for project-level installs.
  # nonoka owns the system prompt; this is the OpenCode adapter that places it
  # where OpenCode expects its primary agent prompt.
  if not args.global_:
    agent_dir = cwd / ".opencode" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_dir / "build.md"
    generated = _build_opencode_agent_prompt(config)
    if not agent_file.exists():
      agent_file.write_text(
        generated,
        encoding="utf-8",
      )
      print(f"Agent prompt saved to {agent_file}")
    else:
      existing_prompt = agent_file.read_text(encoding="utf-8")
      if (
        "Generated by nonoka-cli" in existing_prompt
        or "OpenCode-specific guidelines:" in existing_prompt
      ):
        agent_file.write_text(generated, encoding="utf-8")
        print(f"Managed agent prompt refreshed at {agent_file}")
      else:
        print(f"Custom agent prompt already exists at {agent_file}; not overwriting.")

  print(f"OpenCode config saved to {target}")

  if args.global_:
    print(
      f"\nFor global installs, ensure the provider is available: npm install -g {_PROVIDER_PACKAGE}"
    )

  print(f"Provider: {_PROVIDER_PACKAGE}@{_PROVIDER_VERSION} ready")
  print("Ready. Then run: nonoka-cli run")
  return 0


def _add_config_arg(parser: Any) -> None:
  parser.add_argument(
    "--config",
    dest="config",
    help="Path to the nonoka config file (default: ~/.config/nonoka/config.yaml)",
  )


def add_subparser(subparsers: Any) -> None:
  """Register the ``opencode`` subcommand and its children."""
  opencode_parser = subparsers.add_parser("opencode", help="Manage OpenCode integration")
  _add_config_arg(opencode_parser)
  opencode_parser.add_argument(
    "--cwd",
    dest="cwd",
    default=".",
    help="Directory to write opencode.json into (default: current directory)",
  )
  opencode_parser.add_argument(
    "--global",
    dest="global_",
    action="store_true",
    help="Write to ~/.config/opencode/opencode.json instead of ./opencode.json",
  )

  opencode_subparsers = opencode_parser.add_subparsers(dest="opencode_command", required=True)
  init_parser = opencode_subparsers.add_parser("init", help="Generate or merge opencode.json")
  _add_config_arg(init_parser)
  init_parser.add_argument(
    "--yes",
    "-y",
    action="store_true",
    help="Non-interactive mode (default; kept for script compatibility)",
  )
  init_parser.add_argument(
    "--cwd",
    dest="cwd",
    default=".",
    help="Directory to write opencode.json into (default: current directory)",
  )
  init_parser.add_argument(
    "--global",
    dest="global_",
    action="store_true",
    help="Write to ~/.config/opencode/opencode.json instead of ./opencode.json",
  )
  init_parser.set_defaults(func=cmd_init)
