"""Plugin manifest management commands for nonoka-cli."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import structlog

from nonoka_cli.core.plugin_manifest import PluginManifestLoader
from nonoka_cli.core.plugin_manifest_converter import (
  convert_to_opencode,
  write_opencode_files,
)
from nonoka_cli.core.project_agents import (
  compile_project_agents,
  effective_agent_definitions,
  effective_dynamic_agent_definition,
)
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.commands.plugin")


def _load_manifest(path: Path) -> Any:
  """Load the first plugin manifest found at *path*."""
  loader = PluginManifestLoader()
  candidates = loader.discover(path.parent if path.is_file() else path)
  if not candidates:
    raise ConfigError(f"No plugin manifest found at {path}")
  manifests = loader.load(path.parent if path.is_file() else path)
  if not manifests:
    raise ConfigError(f"Failed to load plugin manifest at {path}")
  return manifests[0]


def run_convert(args: argparse.Namespace) -> int:
  """Convert ``.nonoka/plugin.json`` to OpenCode artifacts."""
  manifest_path = Path(args.manifest)
  manifest = _load_manifest(manifest_path)
  output_dir = Path(args.output)

  written = write_opencode_files(manifest, output_dir)
  snippet = convert_to_opencode(manifest)

  snippet_file = output_dir / ".opencode" / "plugin-snippet.json"
  if snippet:
    snippet_file.parent.mkdir(parents=True, exist_ok=True)
    snippet_file.write_text(
      json.dumps(snippet, indent=2, ensure_ascii=False),
      encoding="utf-8",
    )
    written.append(snippet_file)

  for path in written:
    print(f"Wrote {path}")

  if not manifest.skills:
    print("Warning: manifest contains no skills; nothing was written for OpenCode.")
  return 0


def run_validate(args: argparse.Namespace) -> int:
  """Validate manifest-defined project agents and show effective tool names."""
  manifest_path = Path(args.manifest).expanduser().resolve()
  loaded = PluginManifestLoader().load_path(manifest_path)
  if loaded is None:
    raise ConfigError(f"Failed to load plugin manifest at {manifest_path}")

  compilation = compile_project_agents(
    effective_agent_definitions([loaded]),
    ToolOutputPolicy(),
    effective_dynamic_agent_definition([loaded]),
  )
  for diagnostic in compilation.diagnostics:
    location = f" ({diagnostic.source})" if diagnostic.source else ""
    role = f" [{diagnostic.role}]" if diagnostic.role else ""
    print(f"{diagnostic.level.upper()}{role}: {diagnostic.message}{location}")
  for tool in compilation.tools:
    if tool.name == "agent__spawn":
      print(
        f"OK [dynamic]: {tool.name} model={tool.config.model} "
        f"max_turns={tool.config.max_turns} "
        f"max_invocations={tool.config.max_invocations}"
      )
    else:
      print(
        f"OK [{tool.metadata['role']}]: {tool.name} "
        f"model={tool.agent.model} max_turns={tool.agent.max_turns} "
        f"max_invocations={tool.max_invocations}"
      )
  if compilation.errors:
    return 1
  if not compilation.tools:
    print("OK: manifest defines no project agents.")
  return 0


def add_subparser(subparsers: Any) -> None:
  """Register the ``plugin`` subcommand and its children."""
  plugin_parser = subparsers.add_parser(
    "plugin",
    help="Manage .nonoka/plugin.json manifests",
  )
  plugin_subparsers = plugin_parser.add_subparsers(dest="plugin_command", required=True)

  convert_parser = plugin_subparsers.add_parser(
    "convert",
    help="Convert plugin.json to OpenCode skill files",
  )
  convert_parser.add_argument(
    "--manifest",
    default=".nonoka/plugin.json",
    help="Path to plugin.json (default: .nonoka/plugin.json)",
  )
  convert_parser.add_argument(
    "--output",
    "-o",
    default=".",
    help="Directory to write OpenCode artifacts into (default: current directory)",
  )
  convert_parser.set_defaults(func=run_convert)

  validate_parser = plugin_subparsers.add_parser(
    "validate",
    help="Validate manifest-defined project agents",
  )
  validate_parser.add_argument(
    "--manifest",
    default=".nonoka/plugin.json",
    help="Path to plugin.json (default: .nonoka/plugin.json)",
  )
  validate_parser.set_defaults(func=run_validate)
