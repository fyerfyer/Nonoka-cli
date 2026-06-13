"""Local tool loader — scans directories and imports ``@tool`` functions."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import structlog
from nonoka import tool as tool_decorator
from nonoka.core.registry import ToolRegistry
from nonoka.core.tool import Tool
from nonoka.core.types import Capability

from nonoka_cli.tools import builtins

logger = structlog.get_logger("nonoka_cli.tools")


class ToolLoaderError(Exception):
  """Raised when a local tool cannot be loaded."""

  pass


def _load_builtin_tools() -> ToolRegistry:
  """Load built-in tools shipped with nonoka-cli."""
  registry = ToolRegistry()
  for tool in builtins.get_tools():
    registry.add(tool)
  return registry


def _module_name_for_file(path: Path) -> str:
  """Create a unique, deterministic module name for a file path."""
  # Use the absolute path hashed so repeated loads do not collide and we can
  # still identify the source in tracebacks.
  abs_path = path.resolve()
  return f"nonoka_cli.tools.dynamic.{abs_path.stem}_{hash(str(abs_path)) & 0xFFFFFFFF}"


def _import_module_from_file(path: Path) -> types.ModuleType | None:
  """Import a single Python file as a module.

  Returns ``None`` if the file cannot be imported.
  """
  if not path.exists() or not path.is_file():
    logger.warning("tool_file_not_found", path=str(path))
    return None

  module_name = _module_name_for_file(path)
  try:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
      logger.warning("tool_spec_failed", path=str(path))
      return None

    module = importlib.util.module_from_spec(spec)
    # Make the module available so relative imports / recursion work.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
  except Exception as exc:  # noqa: BLE001
    logger.warning("tool_import_failed", path=str(path), error=str(exc))
    # Clean up failed module registration.
    sys.modules.pop(module_name, None)
    return None


def _discover_tools_in_module(module: types.ModuleType) -> list[Capability]:
  """Return all ``Tool`` instances defined in *module*."""
  tools: list[Capability] = []
  for attr_name in dir(module):
    # Skip dunder attributes to avoid accidentally picking up imported modules.
    if attr_name.startswith("_"):
      continue
    obj = getattr(module, attr_name)
    if isinstance(obj, Tool):
      tools.append(obj)
  return tools


def _wrap_callable_if_needed(obj: Any) -> Capability | None:
  """Wrap a raw callable with the ``@tool`` decorator if it is not already."""
  if isinstance(obj, Tool):
    return obj
  if callable(obj) and hasattr(obj, "__name__"):
    try:
      return tool_decorator(obj)
    except Exception as exc:  # noqa: BLE001
      logger.warning("tool_wrap_failed", obj=str(obj), error=str(exc))
      return None
  return None


class ToolLoader:
  """Scan local directories for ``.py`` files and load ``@tool`` functions.

  Supports:
  - Recursive scanning of ``tool_paths``.
  - Automatic discovery of ``nonoka.core.tool.Tool`` instances.
  - Optional wrapping of plain callables (if they are not already decorated).
  - Built-in tools are included unless ``include_builtins=False``.

  Usage::

    loader = ToolLoader([Path.home() / ".config/nonoka/tools"])
    registry = loader.load_all()
    for tool in registry.get_all():
      print(tool.name)
  """

  def __init__(
    self,
    search_paths: list[Path | str] | None = None,
    *,
    include_builtins: bool = True,
  ):
    """Args:
      search_paths: Directories to scan for ``.py`` tool files.
      include_builtins: Whether to include nonoka-cli's built-in tools.
    """
    self.search_paths: list[Path] = [
      Path(p).expanduser() for p in (search_paths or [])
    ]
    self._builtins_registry = (
      _load_builtin_tools() if include_builtins else ToolRegistry()
    )
    self._loaded_registry: ToolRegistry | None = None

  @property
  def builtins(self) -> ToolRegistry:
    """Built-in tools registry."""
    return self._builtins_registry

  def load_all(self) -> ToolRegistry:
    """Scan all search paths and return a registry of discovered tools.

    Built-in tools are always included first. User tools from ``tool_paths``
    are added afterwards and may override built-ins by name.

    Returns:
      A ``ToolRegistry`` containing all available tools.
    """
    registry = ToolRegistry()

    # Add built-ins first.
    for tool in self._builtins_registry.get_all():
      registry.add(tool)

    # Scan user directories.
    for directory in self.search_paths:
      for tool in self._scan_directory(Path(directory)):
        registry.add(tool)

    self._loaded_registry = registry
    logger.info(
      "tools_loaded",
      builtin_count=len(self._builtins_registry),
      local_count=len(registry) - len(self._builtins_registry),
      total_count=len(registry),
    )
    return registry

  def reload(self) -> ToolRegistry:
    """Reload tools from disk and return the new registry."""
    return self.load_all()

  def get_loaded(self) -> ToolRegistry:
    """Return the most recently loaded registry.

    Loads once if ``load_all()`` has not been called yet.
    """
    if self._loaded_registry is None:
      return self.load_all()
    return self._loaded_registry

  def _scan_directory(self, directory: Path) -> list[Capability]:
    """Recursively scan a directory for ``.py`` files and load tools."""
    directory = directory.expanduser()
    if not directory.exists():
      logger.warning("tool_directory_missing", path=str(directory))
      return []
    if not directory.is_dir():
      logger.warning("tool_path_not_directory", path=str(directory))
      return []

    tools: list[Capability] = []
    for py_file in sorted(directory.rglob("*.py")):
      module = _import_module_from_file(py_file)
      if module is None:
        continue
      discovered = _discover_tools_in_module(module)
      if not discovered:
        logger.debug("no_tools_found_in_file", path=str(py_file))
      for tool in discovered:
        tools.append(tool)
        logger.debug("tool_discovered", name=tool.name, path=str(py_file))

    return tools

  def list_tool_paths(self) -> list[Path]:
    """Return the resolved search paths."""
    return [p.expanduser() for p in self.search_paths]
