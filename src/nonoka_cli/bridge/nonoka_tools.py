"""Nonoka-managed capabilities available while OpenCode hosts a session.

These tools deliberately run in the bridge process rather than being sent back
to OpenCode.  They are small, capability-specific fallbacks for observations
that a host tool cannot represent reliably (for example a match inside a very
large logical JSON record).  Keep their names namespaced so they cannot shadow
the host's native coding tools.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Literal

from nonoka import tool
from nonoka.core.context import RunContext
from nonoka.core.execution import ToolExecution
from nonoka.core.types import Capability


_CHUNK_BYTES = 64 * 1024
_MAX_PATTERN_BYTES = 8 * 1024
_MAX_REGEX_FILE_BYTES = 8 * 1024 * 1024
_EXCLUDED_DIRECTORY_NAMES = {".git"}


def _inside(root: Path, candidate: Path) -> bool:
  """Return whether an already-resolved *candidate* is within *root*."""
  try:
    candidate.relative_to(root)
  except ValueError:
    return False
  return True


def _resolve_workspace_target(working_dir: Path, path: str) -> tuple[Path | None, str | None]:
  """Resolve a target while preventing paths and symlinks from escaping cwd."""
  root = working_dir.resolve()
  requested = Path(path).expanduser()
  target = (requested if requested.is_absolute() else root / requested).resolve()
  if not _inside(root, target):
    return None, f"path escapes the workspace: {path}"
  if not target.exists():
    return None, f"path does not exist: {path}"
  return target, None


def _iter_workspace_files(target: Path) -> tuple[list[Path], list[str]]:
  """Return regular, non-symlink files below *target* and excluded roots."""
  if target.is_file():
    return ([target] if not target.is_symlink() else []), []
  if not target.is_dir():
    return [], []

  files: list[Path] = []
  excluded: list[str] = []
  for directory, dir_names, file_names in os.walk(target, followlinks=False):
    current = Path(directory)
    kept: list[str] = []
    for name in dir_names:
      child = current / name
      if name in _EXCLUDED_DIRECTORY_NAMES:
        excluded.append(str(child))
      elif not child.is_symlink():
        kept.append(name)
    dir_names[:] = kept
    for name in file_names:
      candidate = current / name
      if candidate.is_file() and not candidate.is_symlink():
        files.append(candidate)
  return sorted(files), excluded


def _iter_literal_matches(file_path: Path, needle: bytes):
  """Yield ``(byte_offset, one_based_line)`` without materialising a file.

  The tail retained between chunks is exactly long enough for a match spanning
  a chunk boundary.  This is what makes the capability useful for huge
  single-line JSON documents, where line-oriented host grep output is often
  impractical.
  """
  overlap = max(0, len(needle) - 1)
  tail = b""
  base_offset = 0
  base_line = 1
  with file_path.open("rb") as source:
    while True:
      chunk = source.read(_CHUNK_BYTES)
      finished = not chunk
      data = tail + chunk
      safe_end = len(data) if finished else max(0, len(data) - overlap)

      start = 0
      while True:
        found = data.find(needle, start)
        if found < 0 or found >= safe_end:
          break
        yield base_offset + found, base_line + data[:found].count(b"\n")
        start = found + len(needle)

      if finished:
        break
      base_line += data[:safe_end].count(b"\n")
      base_offset += safe_end
      tail = data[safe_end:]


def _read_excerpt(file_path: Path, offset: int, length: int, context_chars: int) -> str:
  """Read a short decoded evidence window centered on a byte match."""
  start = max(0, offset - context_chars)
  size = length + (2 * context_chars)
  with file_path.open("rb") as source:
    source.seek(start)
    text = source.read(size).decode("utf-8", errors="replace")
  return text.replace("\n", "\\n")


def _iter_regex_matches(
  file_path: Path,
  expression: re.Pattern[str],
  context_chars: int,
  root: Path,
):
  """Yield compact regex evidence for a reasonably sized text file."""
  raw = file_path.read_bytes()
  text = raw.decode("utf-8", errors="replace")
  for found in expression.finditer(text):
    byte_offset = len(text[:found.start()].encode("utf-8"))
    byte_end = len(text[:found.end()].encode("utf-8"))
    start = max(0, found.start() - context_chars)
    end = min(len(text), found.end() + context_chars)
    yield {
      "path": str(file_path.relative_to(root)),
      "line": text.count("\n", 0, found.start()) + 1,
      "byte_offset": byte_offset,
      "byte_end": byte_end,
      "match": found.group(0),
      "excerpt": text[start:end].replace("\n", "\\n"),
    }


def _search_workspace_evidence(
  working_dir: Path,
  pattern: str,
  path: str,
  max_results: int,
  context_chars: int,
  mode: Literal["literal", "regex"],
) -> dict[str, Any]:
  """Perform a bounded literal search and return compact, structured evidence."""
  if not pattern:
    return {"ok": False, "error": "pattern must not be empty"}
  if len(pattern.encode("utf-8", errors="ignore")) > _MAX_PATTERN_BYTES:
    return {
      "ok": False,
      "error": f"pattern exceeds the {_MAX_PATTERN_BYTES}-byte safety limit",
    }

  target, error = _resolve_workspace_target(working_dir, path)
  if error or target is None:
    return {"ok": False, "error": error}

  root = working_dir.resolve()
  files, excluded = _iter_workspace_files(target)
  matches: list[dict[str, Any]] = []
  unreadable: list[str] = []
  skipped: list[str] = []
  truncated = False
  expression: re.Pattern[str] | None = None
  regex_literal = False
  literal_needle: bytes | None = None
  if mode == "regex":
    try:
      expression = re.compile(pattern)
    except re.error as exc:
      return {"ok": False, "error": f"invalid regular expression: {exc}"}
    # A regex with no metacharacters is equivalent to an exact search. Keep
    # this safe, streaming path available for large single-line records.
    try:
      literal_needle = pattern.encode("utf-8")
    except UnicodeEncodeError:
      literal_needle = None
    regex_literal = re.escape(pattern) == pattern and literal_needle is not None
  else:
    try:
      needle = pattern.encode("utf-8")
    except UnicodeEncodeError:
      return {"ok": False, "error": "pattern cannot be encoded as UTF-8"}
  for file_path in files:
    try:
      if expression is not None and regex_literal:
        file_matches = (
          {
            "path": str(file_path.relative_to(root)),
            "line": line,
            "byte_offset": byte_offset,
            "byte_end": byte_offset + len(literal_needle),
            "match": pattern,
            "excerpt": _read_excerpt(file_path, byte_offset, len(literal_needle), context_chars),
          }
          for byte_offset, line in _iter_literal_matches(file_path, literal_needle)
        )
      elif expression is not None:
        if file_path.stat().st_size > _MAX_REGEX_FILE_BYTES:
          if len(skipped) < 20:
            skipped.append(
              f"{file_path.relative_to(root)}: exceeds the "
              f"{_MAX_REGEX_FILE_BYTES}-byte regex observation limit"
            )
          continue
        file_matches = _iter_regex_matches(file_path, expression, context_chars, root)
      else:
        file_matches = (
          {
            "path": str(file_path.relative_to(root)),
            "line": line,
            "byte_offset": byte_offset,
            "byte_end": byte_offset + len(needle),
            "match": pattern,
            "excerpt": _read_excerpt(file_path, byte_offset, len(needle), context_chars),
          }
          for byte_offset, line in _iter_literal_matches(file_path, needle)
        )
      for match in file_matches:
        if len(matches) >= max_results:
          truncated = True
          break
        matches.append(match)
      if truncated:
        break
    except OSError as exc:
      if len(unreadable) < 20:
        unreadable.append(f"{file_path.relative_to(root)}: {exc}")

  return {
    "ok": True,
    "searched_path": str(target.relative_to(root)) or ".",
    "pattern": pattern,
    "mode": mode,
    "matches": matches,
    "truncated": truncated,
    "complete": not truncated and not unreadable and not skipped,
    "scanned_files": len(files),
    "unreadable_files": unreadable,
    "skipped_files": skipped,
    "excluded_roots": [str(Path(item).relative_to(root)) for item in excluded],
  }


@tool(
  description=(
    "Search exact text in the current workspace and return bounded structured "
    "evidence (path, line, byte coordinates, and a short excerpt). Use this "
    "for reliable inspection of large or single-line structured files."
  ),
  execution=ToolExecution(read_only=True),
)
async def nonoka__search_evidence(
  ctx: RunContext,
  pattern: str,
  path: str = ".",
  max_results: int = 20,
  context_chars: int = 120,
  mode: Literal["literal", "regex"] = "literal",
) -> dict[str, Any]:
  """Find *pattern* under *path* without returning whole records.

  ``mode=literal`` is streaming and exact. ``mode=regex`` accepts a Python
  regular expression for files up to the reported regex observation limit.
  ``truncated`` means more matches exist than were returned; ``complete`` is
  false when a result limit or unreadable file prevents exhaustive coverage.
  """
  max_results = max(1, min(max_results, 200))
  context_chars = max(20, min(context_chars, 400))
  return await asyncio.to_thread(
    _search_workspace_evidence,
    Path(ctx.deps.working_dir),
    pattern,
    path,
    max_results,
    context_chars,
    mode,
  )


# This is a framework-local observation, not a callable OpenCode host tool.
# The framework still persists it in the agent's memory, but suppresses its
# lifecycle event at the host boundary.
nonoka__search_evidence.host_visible = False
nonoka__search_evidence.metadata = {
  "kind": "observation_fallback",
  "scope": "workspace_search",
  "fallback": {
    "on_partial_external": True,
    "argument_map": {"pattern": "pattern", "path": "path"},
    "defaults": {"mode": "regex"},
  },
}


def get_hosted_tools() -> list[Capability]:
  """Return bridge-local capabilities safe to expose beside host tools."""
  return [nonoka__search_evidence]
