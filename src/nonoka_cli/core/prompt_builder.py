"""System prompt builder for nonoka-cli's OpenCode-hosted mode.

Composes the final system prompt from:
- user-configured or host-provided base prompt
- model identity
- current working directory / path guidance
- OpenCode TODO workflow reminder
- OpenCode native tool names + namespace guidance for MCP/skill tools

Skill registry blocks are handled by nonoka-agent's SkillRegistry and injected
via ``Agent._expand_skills_lazy()``; this builder only deals with OpenCode-
specific prompt segments.
"""

from __future__ import annotations

from pathlib import Path

_OPENCODE_TODO_WORKFLOW_BLOCK = """\
MANDATORY TODO WORKFLOW for multi-step tasks:
1. FIRST, call the `todowrite` tool to create the complete todo list. Mark the
   first step as `in_progress` and the rest as `pending`.
2. BEFORE starting any step, call `todowrite` to mark that step as
   `in_progress` and any previously completed steps as `completed`.
3. AFTER a step finishes successfully, call `todowrite` to mark it as
   `completed` and the next step as `in_progress`.
4. If a step fails or is skipped, call `todowrite` to mark it as `cancelled`.
5. At the end, call `todowrite` with every item marked `completed`.

Do not skip these updates. The user sees progress through the OpenCode TODO
UI, so you must keep the list accurate at every transition.

Use these statuses exactly:
- `pending` for steps not yet started.
- `in_progress` for the step currently being worked on.
- `completed` for steps that finished successfully.
- `cancelled` for steps that failed or were skipped.
"""


class SystemPromptBuilder:
  """Build the effective system prompt for the OpenCode-hosted agent."""

  def __init__(
    self,
    base: str,
    model: str,
    cwd: str | Path | None = None,
    native_tools: list[str] | None = None,
    mcp_tools: list[str] | None = None,
    skill_tools: list[str] | None = None,
  ):
    """Args:
      base: Base system prompt (config, host, or default).
      model: Model identifier injected as an identity line.
      cwd: Current working directory to inject path guidance.
      native_tools: OpenCode native tool names available to the model.
      mcp_tools: Prefixed MCP tool names available to the model.
      skill_tools: Prefixed skill tool names available to the model.
    """
    self._base = base
    self._model = model.strip()
    self._cwd = cwd
    self._native_tools = native_tools or []
    self._mcp_tools = mcp_tools or []
    self._skill_tools = skill_tools or []

  def build(self) -> str:
    """Assemble and return the final system prompt."""
    parts: list[str] = [self._inject_identity(self._base)]

    cwd_block = self._build_cwd_block()
    if cwd_block:
      parts.append(cwd_block)

    todo_block = self._build_todo_block()
    if todo_block:
      parts.append(todo_block)

    namespaces_block = self._build_namespaces_block()
    if namespaces_block:
      parts.append(namespaces_block)

    return "\n\n".join(parts)

  def _inject_identity(self, base: str) -> str:
    """Ensure the model identity line is present exactly once."""
    identity_line = f"Your current model is: {self._model}."
    if identity_line in base:
      return base
    return base.rstrip() + f"\n\n{identity_line}"

  def _build_cwd_block(self) -> str:
    """Return cwd/path guidance if a cwd was provided."""
    if self._cwd is None:
      return ""

    cwd_str = str(Path(self._cwd).resolve())
    block = (
      f"Current working directory: {cwd_str}\n"
      "All file paths must be relative to this directory or use the absolute path above.\n"
      "Prefer write_file/edit_file over bash/execute_command for file mutations."
    )
    if "Current working directory:" in self._base:
      return ""
    return block

  def _build_todo_block(self) -> str:
    """Return the TODO workflow block if the base does not already contain it."""
    if "todowrite" in self._base.lower():
      return ""
    return _OPENCODE_TODO_WORKFLOW_BLOCK

  def _build_namespaces_block(self) -> str:
    """Return a block describing exact tool names/namespaces."""
    if not self._native_tools and not self._mcp_tools and not self._skill_tools:
      return ""

    lines: list[str] = [
      "## Tool Namespaces",
      "Use the EXACT tool names below when making calls. Do not invent names.",
      "Namespace separators are encoded as double underscores (``:`` -> ``__``).",
    ]

    if self._native_tools:
      lines.append(
        "- OpenCode native tools: "
        + ", ".join(f"`{n}`" for n in self._native_tools)
      )
    if self._mcp_tools:
      lines.append(
        "- MCP tools (call as ``mcp__<server>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._mcp_tools)
      )
    if self._skill_tools:
      lines.append(
        "- Skill tools (call as ``skill__<skill>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._skill_tools)
      )

    if self._mcp_tools:
      lines.append(
        "When the user asks for something only an MCP tool can do, "
        "prefer the MCP tool over generic bash/file commands."
      )

    return "\n".join(lines)
