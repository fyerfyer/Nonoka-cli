"""System prompt builder for nonoka-cli's host-managed mode.

Composes the final system prompt from:
- user-configured or host-provided base prompt
- model identity
- current working directory / path guidance
- TODO workflow reminder
- tool namespace guidance for host, MCP, and skill tools

Skill registry blocks are handled by nonoka-agent's SkillRegistry and injected
via ``Agent._expand_skills_lazy()``; this builder only deals with host-specific
prompt segments.
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
  """Build the effective system prompt for the host-managed agent."""

  def __init__(
    self,
    base: str,
    model: str,
    cwd: str | Path | None = None,
    host_tools: list[str] | None = None,
    external_mcp_tools: list[str] | None = None,
    external_skill_tools: list[str] | None = None,
    internal_mcp_tools: list[str] | None = None,
    internal_skill_tools: list[str] | None = None,
  ):
    """Args:
      base: Base system prompt (config, host, or default).
      model: Model identifier injected as an identity line.
      cwd: Current working directory to inject path guidance.
      host_tools: Host native tool names available to the model.
      external_mcp_tools: Prefixed external MCP tool names.
      external_skill_tools: Prefixed external skill tool names.
      internal_mcp_tools: Prefixed internal MCP tool names.
      internal_skill_tools: Prefixed internal skill tool names.
    """
    self._base = base
    self._model = model.strip()
    self._cwd = cwd
    self._host_tools = host_tools or []
    self._external_mcp_tools = external_mcp_tools or []
    self._external_skill_tools = external_skill_tools or []
    self._internal_mcp_tools = internal_mcp_tools or []
    self._internal_skill_tools = internal_skill_tools or []

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
    has_any = (
      self._host_tools
      or self._external_mcp_tools
      or self._external_skill_tools
      or self._internal_mcp_tools
      or self._internal_skill_tools
    )
    if not has_any:
      return ""

    lines: list[str] = [
      "## Tool Namespaces",
      "Use the EXACT tool names below when making calls. Do not invent names.",
      "Namespace separators are encoded as double underscores (``:`` -> ``__``).",
    ]

    if self._host_tools:
      lines.append(
        "- Host native tools (OpenCode executes): "
        + ", ".join(f"`{n}`" for n in self._host_tools)
      )
    if self._external_mcp_tools:
      lines.append(
        "- External MCP tools (host executes, call as ``mcp__<server>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._external_mcp_tools)
      )
    if self._external_skill_tools:
      lines.append(
        "- External skill tools (host executes, call as ``skill__<skill>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._external_skill_tools)
      )
    if self._internal_mcp_tools:
      lines.append(
        "- Internal MCP tools (nonoka executes, call as ``mcp__<server>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._internal_mcp_tools)
      )
    if self._internal_skill_tools:
      lines.append(
        "- Internal skill tools (nonoka executes, call as ``skill__<skill>__<tool>``): "
        + ", ".join(f"`{n}`" for n in self._internal_skill_tools)
      )

    if self._external_mcp_tools or self._internal_mcp_tools:
      lines.append(
        "When the user asks for something only an MCP tool can do, "
        "prefer the MCP tool over generic bash/file commands."
      )

    if self._external_skill_tools or self._internal_skill_tools:
      lines.append(
        "To use a skill, first call ``load_skill`` with the exact skill name. "
        "After loading, use the skill's tools via the ``skill__<skill>__<tool>`` namespace. "
        "Do NOT use ``skill:<name>``; OpenCode's native skill tool is disabled in this environment."
      )

    return "\n".join(lines)
