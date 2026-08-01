"""Built-in file-system and shell tools for nonoka-cli.

These tools mirror the functionality commonly found in coding assistants
(Gemini CLI, Claude Code, Codex): reading, writing, editing, and deleting
files, listing directories, searching file contents, and running shell
commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from typing import Any

from nonoka import tool
from nonoka.core.context import RunContext

_MAX_READ_BYTES = 1024 * 1024  # 1 MiB cap for file reads
_MAX_GREP_HITS = 50
_MAX_LIST_ENTRIES = 200
_MAX_TREE_DEPTH = 5
_MAX_TREE_ENTRIES = 200

# Directories commonly skipped when rendering a tree view.
_TREE_SKIP_DIRS = {
  ".git",
  ".venv",
  "venv",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".tox",
  "dist",
  "build",
  ".eggs",
}


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
  """Stop a timed-out shell and every descendant it started.

  Killing only the shell returned by ``create_subprocess_shell`` leaves
  grandchildren (for example a long-running ``sqlite3`` process behind a
  pipeline) alive.  Those orphaned processes can retain locks and corrupt the
  next tool call or a benchmark verifier.  On POSIX each command has its own
  session, so terminating its process group is both bounded and isolated from
  the CLI process itself.
  """
  if proc.returncode is not None:
    return

  if os.name == "posix":
    with contextlib.suppress(ProcessLookupError):
      os.killpg(proc.pid, signal.SIGTERM)
    try:
      await asyncio.wait_for(proc.wait(), timeout=2)
      return
    except asyncio.TimeoutError:
      with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
  else:  # pragma: no cover - exercised on Windows.
    proc.kill()

  with contextlib.suppress(ProcessLookupError):
    await proc.wait()


def _resolve_path(ctx: RunContext, path: str) -> Path:
  """Resolve *path* relative to the CLI working directory."""
  target = Path(path).expanduser()
  if not target.is_absolute():
    target = ctx.deps.working_dir / target
  return target.resolve()


def _check_path(ctx: RunContext, target: Path) -> Path:
  """Enforce the configured filesystem policy before touching *target*."""
  policy = getattr(ctx.deps, "safety_policy", None)
  return policy.check_path(target) if policy is not None else target


def _truncate(text: str, limit: int = 10000) -> str:
  """Truncate *text* to *limit* characters with a clear marker."""
  if len(text) <= limit:
    return text
  return text[:limit] + f"\n\n... [truncated; total length {len(text)} chars]"


@tool(description="Read the contents of a text file.")
async def read_file(
  ctx: RunContext,
  path: str,
  offset: int = 1,
  limit: int = 2000,
) -> str:
  """Read up to *limit* lines from *path* starting at 1-based *offset*.

  Args:
    path: File path (absolute or relative to the CLI working directory).
    offset: 1-based starting line number.
    limit: Maximum number of lines to return.
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: file not found: {target}"
  if not target.is_file():
    return f"Error: not a file: {target}"

  try:
    raw = target.read_bytes()
    if len(raw) > _MAX_READ_BYTES:
      return (
        f"Error: file too large to read ({len(raw)} bytes > {_MAX_READ_BYTES}). "
        "Use grep_files or read a smaller section."
      )
    text = raw.decode("utf-8", errors="replace")
  except OSError as exc:
    return f"Error reading {target}: {exc}"

  lines = text.splitlines()
  start = max(0, offset - 1)
  end = start + limit
  selected = lines[start:end]

  header = f"--- {target} (lines {start + 1}-{min(end, len(lines))} of {len(lines)}) ---\n"
  return header + "\n".join(selected)


@tool(description="Read a file with line numbers for precise editing.")
async def view(
  ctx: RunContext,
  path: str,
  offset: int = 1,
  limit: int = 200,
) -> str:
  """Read *path* with line numbers, starting at 1-based *offset*.

  This is the preferred tool for reading code before editing. Use `offset`
  and `limit` to read specific sections.

  Args:
    path: File path (absolute or relative to the CLI working directory).
    offset: 1-based starting line number.
    limit: Maximum number of lines to return.
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: file not found: {target}"
  if not target.is_file():
    return f"Error: not a file: {target}"

  try:
    raw = target.read_bytes()
    if len(raw) > _MAX_READ_BYTES:
      return (
        f"Error: file too large to read ({len(raw)} bytes > {_MAX_READ_BYTES}). "
        "Use view with offset/limit or grep_files."
      )
    text = raw.decode("utf-8", errors="replace")
  except OSError as exc:
    return f"Error reading {target}: {exc}"

  lines = text.splitlines()
  start = max(0, offset - 1)
  end = min(start + limit, len(lines))

  max_width = len(str(end))
  numbered = [
    f"{i + 1:>{max_width}} | {lines[i]}"
    for i in range(start, end)
  ]

  header = f"--- {target} (lines {start + 1}-{end} of {len(lines)}) ---\n"
  return header + "\n".join(numbered)


@tool(description="Display a directory tree, skipping common build/dependency folders.")
async def view_dir(
  ctx: RunContext,
  path: str = ".",
  depth: int = 3,
) -> str:
  """Show a tree view of *path* up to *depth* levels.

  Args:
    path: Root directory (absolute or relative to working directory).
    depth: Maximum depth to display (default 3, max 5).
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: directory not found: {target}"
  if not target.is_dir():
    return f"Error: not a directory: {target}"

  depth = max(1, min(depth, _MAX_TREE_DEPTH))
  lines: list[str] = [f"--- {target} ---"]
  shown = 0

  def walk(current: Path, prefix: str, level: int) -> None:
    nonlocal shown
    if level > depth or shown >= _MAX_TREE_ENTRIES:
      return

    try:
      entries = sorted(
        current.iterdir(),
        key=lambda p: (not p.is_dir(), p.name.lower()),
      )
    except OSError:
      return

    for index, entry in enumerate(entries):
      if shown >= _MAX_TREE_ENTRIES:
        return

      is_last = index == len(entries) - 1
      connector = "└── " if is_last else "├── "
      name = f"{entry.name}/" if entry.is_dir() else entry.name
      lines.append(f"{prefix}{connector}{name}")
      shown += 1

      if entry.is_dir():
        if entry.name in _TREE_SKIP_DIRS:
          lines.append(f"{prefix}{('    ' if is_last else '│   ')}...")
          continue
        walk(
          entry,
          prefix + ("    " if is_last else "│   "),
          level + 1,
        )

  walk(target, "", 1)

  if shown >= _MAX_TREE_ENTRIES:
    lines.append("\n... (tree truncated)")

  return "\n".join(lines)


@tool(description="Write text to a file, creating parent directories if needed.")
async def write_file(
  ctx: RunContext,
  path: str,
  content: str,
  append: bool = False,
) -> str:
  """Write *content* to *path*.

  Args:
    path: File path (absolute or relative to the CLI working directory).
    content: Text to write.
    append: If true, append instead of overwriting.
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  try:
    target.parent.mkdir(parents=True, exist_ok=True)
    if append and target.exists():
      existing = target.read_text(encoding="utf-8")
      content = existing + content
    target.write_text(content, encoding="utf-8")
    action = "appended to" if append else "wrote"
    return f"Success: {action} {target} ({len(content)} chars)"
  except OSError as exc:
    return f"Error writing {target}: {exc}"


@tool(description="Replace a unique string in a file with another string.")
async def edit_file(
  ctx: RunContext,
  path: str,
  old_string: str,
  new_string: str,
) -> str:
  """Replace *old_string* with *new_string* in *path*.

  The replacement must be unique; if *old_string* occurs 0 or more than
  once, the edit is rejected so the model can refine its request.

  Args:
    path: File path (absolute or relative to the CLI working directory).
    old_string: Exact text to replace.
    new_string: Replacement text.
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: file not found: {target}"
  if not target.is_file():
    return f"Error: not a file: {target}"

  try:
    text = target.read_text(encoding="utf-8", errors="replace")
  except OSError as exc:
    return f"Error reading {target}: {exc}"

  count = text.count(old_string)
  if count == 0:
    return f"Error: old_string not found in {target}"
  if count > 1:
    return (
      f"Error: old_string is not unique in {target} (found {count} occurrences). "
      "Provide more context or use write_file to overwrite the whole file."
    )

  new_text = text.replace(old_string, new_string, 1)
  try:
    target.write_text(new_text, encoding="utf-8")
  except OSError as exc:
    return f"Error writing {target}: {exc}"

  return f"Success: edited {target}"


@tool(description="Replace occurrences of a string in a file.")
async def search_and_replace(
  ctx: RunContext,
  path: str,
  old_string: str,
  new_string: str,
  replace_all: bool = True,
) -> str:
  """Replace *old_string* with *new_string* in *path*.

  By default replaces all occurrences. Use `replace_all=false` to replace
  only the first occurrence.

  Args:
    path: File path (absolute or relative to the CLI working directory).
    old_string: Exact text to replace.
    new_string: Replacement text.
    replace_all: Whether to replace all occurrences (default) or just the first.
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: file not found: {target}"
  if not target.is_file():
    return f"Error: not a file: {target}"

  try:
    text = target.read_text(encoding="utf-8", errors="replace")
  except OSError as exc:
    return f"Error reading {target}: {exc}"

  count = text.count(old_string)
  if count == 0:
    return f"Error: old_string not found in {target}"

  new_text = text.replace(old_string, new_string, -1 if replace_all else 1)
  try:
    target.write_text(new_text, encoding="utf-8")
  except OSError as exc:
    return f"Error writing {target}: {exc}"

  scope = "all" if replace_all else "first"
  return f"Success: replaced {scope} {count} occurrence(s) in {target}"


@tool(description="Delete a file or empty directory.")
async def delete_file(
  ctx: RunContext,
  path: str,
) -> str:
  """Delete *path*.

  Args:
    path: File or empty directory path (absolute or relative to working dir).
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: path not found: {target}"

  try:
    if target.is_file():
      target.unlink()
      return f"Success: deleted file {target}"
    if target.is_dir():
      target.rmdir()  # only empty directories
      return f"Success: deleted empty directory {target}"
    return f"Error: unsupported path type: {target}"
  except OSError as exc:
    return f"Error deleting {target}: {exc}"


@tool(description="List files and directories.")
async def list_dir(
  ctx: RunContext,
  path: str = ".",
) -> str:
  """List entries in *path*.

  Args:
    path: Directory path (absolute or relative to working directory).
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: directory not found: {target}"
  if not target.is_dir():
    return f"Error: not a directory: {target}"

  try:
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
  except OSError as exc:
    return f"Error listing {target}: {exc}"

  lines: list[str] = [f"--- {target} ---"]
  for entry in entries[:_MAX_LIST_ENTRIES]:
    suffix = "/" if entry.is_dir() else ""
    lines.append(f"{entry.name}{suffix}")

  if len(entries) > _MAX_LIST_ENTRIES:
    lines.append(f"\n... and {len(entries) - _MAX_LIST_ENTRIES} more entries")

  return "\n".join(lines)


@tool(description="Search file contents with a glob pattern.")
async def grep_files(
  ctx: RunContext,
  pattern: str,
  path: str = ".",
  glob: str = "*",
) -> str:
  """Search for *pattern* in files under *path* matching *glob*.

  Args:
    pattern: Substring to search for.
    path: Root directory (absolute or relative to working directory).
    glob: Glob pattern for files to search (e.g. "*.py").
  """
  target = _resolve_path(ctx, path)
  _check_path(ctx, target)

  if not target.exists():
    return f"Error: directory not found: {target}"
  if not target.is_dir():
    return f"Error: not a directory: {target}"

  matches: list[str] = []
  try:
    for file_path in target.rglob(glob):
      if not file_path.is_file():
        continue
      try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
      except (OSError, UnicodeDecodeError):
        continue
      for lineno, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
          matches.append(f"{file_path.relative_to(target)}:{lineno}: {line.strip()}")
          if len(matches) >= _MAX_GREP_HITS:
            break
      if len(matches) >= _MAX_GREP_HITS:
        break
  except OSError as exc:
    return f"Error searching {target}: {exc}"

  if not matches:
    return f"No matches for '{pattern}' under {target} matching '{glob}'"

  header = f"--- matches for '{pattern}' under {target} ---\n"
  result = header + "\n".join(matches)
  if len(matches) >= _MAX_GREP_HITS:
    result += "\n... (results limited)"
  return result


@tool(description="Execute a shell command and return stdout/stderr.")
async def execute_command(
  ctx: RunContext,
  command: str,
  cwd: str | None = None,
  timeout: int = 60,
) -> str:
  """Run *command* in a shell and return the combined output.

  Args:
    command: Shell command to execute.
    cwd: Working directory for the command. Defaults to the CLI working dir.
    timeout: Maximum execution time in seconds.
  """
  working_dir = _resolve_path(ctx, cwd) if cwd else ctx.deps.working_dir
  policy = getattr(ctx.deps, "safety_policy", None)
  if policy is not None:
    _check_path(ctx, working_dir)
    decision = policy.check_command(command)
    if decision == "approval":
      return "Error: command requires explicit approval by safety policy"

  sandbox_mode = getattr(getattr(ctx.deps, "config", None), "safety", None)
  selected_sandbox = getattr(sandbox_mode, "sandbox", None)
  if selected_sandbox in {"docker", "srt", "auto"}:
    from nonoka_cli.safety import active_process_sandbox
    if active_process_sandbox():
      # The entire OpenCode/bridge process tree is already isolated. Running
      # another SRT inside it fails on the nested mux socket and adds no boundary.
      selected_sandbox = None
  if selected_sandbox in {"docker", "srt", "auto"}:
    from nonoka_cli.safety import DockerSandbox, SrtSandbox
    try:
      if selected_sandbox == "auto":
        selected_sandbox = "srt" if SrtSandbox.executable() else "docker"
      backend = (
        DockerSandbox()
        if selected_sandbox == "docker"
        else SrtSandbox(getattr(sandbox_mode, "allowed_domains", []))
      )
      code, output = await backend.run(command, working_dir, timeout)
      status = "success" if code == 0 else "error"
      return f"--- exit code {code} ({status}, {selected_sandbox}-sandbox) ---\n{_truncate(output, 10000)}"
    except (OSError, RuntimeError) as exc:
      if getattr(sandbox_mode, "required", False):
        return f"Error executing required sandbox: {exc}"
      if selected_sandbox == "auto":
        return f"Warning: sandbox unavailable ({exc}); command was not executed."
      return f"Error executing sandbox: {exc}"

  try:
    process_options: dict[str, Any] = {
      "cwd": str(working_dir),
      "stdout": asyncio.subprocess.PIPE,
      "stderr": asyncio.subprocess.STDOUT,
    }
    if os.name == "posix":
      # Give the command a dedicated process group so a timeout cannot leave
      # pipeline children or background grandchildren behind.
      process_options["start_new_session"] = True
    proc = await asyncio.create_subprocess_shell(
      command,
      **process_options,
    )
    communicate = asyncio.create_task(proc.communicate())
    stdout, _ = await asyncio.wait_for(asyncio.shield(communicate), timeout=timeout)
    output = stdout.decode("utf-8", errors="replace")
    output = _truncate(output, 10000)

    status = "success" if proc.returncode == 0 else "error"
    return f"--- exit code {proc.returncode} ({status}) ---\n{output}"
  except asyncio.TimeoutError:
    await _terminate_process_group(proc)
    # The shield above keeps communicate alive long enough to drain and close
    # its pipes after the process group exits.
    with contextlib.suppress(asyncio.CancelledError, ProcessLookupError):
      await communicate
    return f"Error: command timed out after {timeout}s"
  except OSError as exc:
    return f"Error executing command: {exc}"


def get_tools() -> list[Any]:
  """Return all built-in file tools as decorated Tool instances."""
  return [
    read_file,
    view,
    view_dir,
    write_file,
    edit_file,
    search_and_replace,
    delete_file,
    list_dir,
    grep_files,
    execute_command,
  ]
