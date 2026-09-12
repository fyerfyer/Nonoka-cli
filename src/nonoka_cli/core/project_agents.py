"""Compile project manifest roles into bounded nonoka AgentTool capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonoka import AgentBuilder, AgentTool, MemoryStrategy
from nonoka.core.context import RunContext
from nonoka.core.execution import ToolExecution
from nonoka.core.types import Capability, RunResult

from nonoka_cli.core.plugin_manifest import (
  AgentEntry,
  LoadedPluginManifest,
)
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy

_ROLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_PROJECT_AGENTS = 8


@dataclass(frozen=True)
class ProjectAgentDefinition:
  entry: AgentEntry
  source: Path


@dataclass(frozen=True)
class ProjectAgentDiagnostic:
  level: str
  message: str
  role: str | None = None
  source: Path | None = None


@dataclass
class ProjectAgentCompilation:
  tools: list[Capability] = field(default_factory=list)
  diagnostics: list[ProjectAgentDiagnostic] = field(default_factory=list)

  @property
  def errors(self) -> list[ProjectAgentDiagnostic]:
    return [item for item in self.diagnostics if item.level == "error"]


def effective_agent_definitions(
  manifests: list[LoadedPluginManifest],
) -> list[ProjectAgentDefinition]:
  """Resolve manifest roles with the existing last-definition-wins policy."""
  effective: dict[str, ProjectAgentDefinition] = {}
  order: list[str] = []
  for loaded in manifests:
    for entry in loaded.manifest.agents:
      if entry.name not in effective:
        order.append(entry.name)
      effective[entry.name] = ProjectAgentDefinition(entry=entry, source=loaded.path)
  return [effective[name] for name in order]


class ProjectAgentTool(AgentTool):
  """AgentTool with per-parent invocation limits and structured results.

  ``entry.allowed_tools`` is the authorization ceiling: at invoke time the
  declared names are resolved against the *parent* agent's tool set and the
  matching capabilities are attached to the child agent so it can explore
  autonomously within that ceiling.  Names that resolve to host-external
  capabilities cannot be routed back from a child session (the bridge
  resumes only the top-level session), so they degrade the invocation to a
  structured error instead of failing silently mid-run.
  """

  def __init__(
    self,
    *,
    definition: ProjectAgentDefinition,
    output_policy: ToolOutputPolicy,
  ) -> None:
    entry = definition.entry
    tool_name = f"agent__{entry.name}"

    def extract(result: RunResult) -> dict[str, Any]:
      session = result.session
      termination = None
      if session is not None and session.runtime_state.termination is not None:
        termination = session.runtime_state.termination.model_dump(mode="json")
      payload: dict[str, Any] = {
        "role": entry.name,
        "session_id": session.session_id if session is not None else None,
        "success": result.success,
        "termination": termination,
      }
      if result.success:
        extracted: Any = result.data
        if entry.output_contract == "review" and isinstance(result.data, str):
          try:
            review = json.loads(result.data)
          except (TypeError, ValueError):
            payload["contract_warning"] = (
              "Reviewer did not return the configured JSON review contract."
            )
          else:
            if isinstance(review, dict):
              blocking = review.get("blocking_issues")
              suggestions = review.get("non_blocking_suggestions")
              if isinstance(blocking, list) and isinstance(suggestions, list):
                review["verdict"] = "CHANGES_REQUIRED" if blocking else "APPROVED"
                extracted = review
              else:
                payload["contract_warning"] = (
                  "Reviewer JSON omitted blocking_issues or non_blocking_suggestions arrays."
                )
        payload["result"] = output_policy.apply(tool_name, extracted)
      else:
        payload["error"] = result.error or "Sub-agent execution failed."
        payload["error_type"] = result.error_type or "unknown"
      return payload

    system_prompt = entry.system_prompt.strip()
    if entry.output_contract == "review":
      system_prompt += (
        "\n\nReview contract: Check each explicit acceptance criterion before "
        "general hardening concerns. Return exactly one JSON object with keys "
        "verdict, blocking_issues, and non_blocking_suggestions. "
        "blocking_issues is an array of objects with requirement, evidence, and "
        "remediation. Mark an issue blocking only for an explicit requirement, "
        "a safety violation, or missing/failed required verification. Put all "
        "optional robustness, concurrency, portability, and production-hardening "
        "ideas in non_blocking_suggestions. Use CHANGES_REQUIRED only when "
        "blocking_issues is non-empty; otherwise use APPROVED."
      )
    child = (
      AgentBuilder()
      .model(entry.model.strip())
      .system_prompt(system_prompt)
      .max_turns(entry.max_turns)
      .max_steps(entry.tool_budget if entry.allowed_tools else 0)
      .metadata(
        project_agent_role=entry.name,
        project_agent_source=str(definition.source),
      )
      .tag("subagent", "project-defined")
      .build()
    )
    if entry.allowed_tools:
      description = entry.description or (
        f"Delegate advisory work to the {entry.name} role. The child agent can "
        f"explore autonomously using these declared tools: "
        f"{', '.join(entry.allowed_tools)}."
      )
    else:
      description = entry.description or (
        f"Delegate advisory work to the {entry.name} role. The child agent is "
        "tool-free; embed file content in the task/context argument."
      )
    super().__init__(
      agent=child,
      name=tool_name,
      description=description,
      memory_strategy=MemoryStrategy.ISOLATE,
      max_depth=1,
      result_extractor=extract,
    )
    self.max_invocations = entry.max_invocations
    self._entry = entry
    self._allowed_tool_names = list(entry.allowed_tools)
    # Child calls are workspace-read-only. Model selection races on the
    # shared Runner slot were fixed in the framework (Runner.current_llm),
    # so child agents may run in the scheduler's read-only parallel wave.
    self._execution = ToolExecution(read_only=True)
    self.metadata = {
      "kind": "project_agent",
      "role": entry.name,
      "source": str(definition.source),
    }

  @property
  def execution(self) -> ToolExecution:
    return self._execution

  async def invoke(self, ctx: RunContext, arguments: dict[str, Any]) -> Any:
    state = ctx.session.extension_state.setdefault("project_agents", {})
    count = int(state.get(self.name, 0))
    if count >= self.max_invocations:
      return {
        "role": self.metadata["role"],
        "success": False,
        "error": (
          f"Project agent invocation limit reached for {self.name}: "
          f"{self.max_invocations} per parent session."
        ),
        "error_type": "invocation_limit",
      }

    unknown_tools: list[str] = []
    if self._allowed_tool_names:
      matched, unknown_tools, external_tools = self._resolve_allowed_tools(ctx)
      if external_tools:
        # Host-native / host-managed capabilities pause the *issuing* session
        # for the host to execute, but the bridge only resumes the top-level
        # session — a child-issued external call could never receive its
        # result. Degrade before spending an invocation.
        return {
          "role": self.metadata["role"],
          "success": False,
          "error": (
            f"This host does not support sub-agent tool execution; remove "
            f"these names from allowed_tools: {', '.join(external_tools)}."
          ),
          "error_type": "unsupported_sub_agent_tools",
        }
      self._apply_child_tools(matched)

    state[self.name] = count + 1
    result = await super().invoke(ctx, arguments)
    if unknown_tools and isinstance(result, dict):
      result["unknown_allowed_tools"] = unknown_tools
    return result

  def _resolve_allowed_tools(
    self, ctx: RunContext,
  ) -> tuple[list[Capability], list[str], list[str]]:
    """Resolve declared allowed_tools against the parent agent's tool set.

    Returns ``(matched, unknown, external)`` name lists: matched local
    capabilities are granted to the child, unknown names are reported back
    without failing the call, and external (host-executed) names degrade the
    invocation.
    """
    parent_tools = {
      tool.name: tool for tool in (getattr(ctx.session.agent, "tools", None) or [])
    }
    matched: list[Capability] = []
    unknown: list[str] = []
    external: list[str] = []
    for name in self._allowed_tool_names:
      tool = parent_tools.get(name)
      if tool is None:
        unknown.append(name)
      elif getattr(tool, "external", False):
        external.append(name)
      else:
        matched.append(tool)
    return matched, unknown, external

  def _apply_child_tools(self, matched: list[Capability]) -> None:
    """Attach the resolved ceiling to the immutable child agent configuration.

    Parallel invokes from one parent session resolve to the same capability
    objects, so the write is idempotent; sequential requests re-resolve, so a
    host switch always converges to the current parent's tool set.
    """
    object.__setattr__(self.agent, "tools", list(matched))
    object.__setattr__(
      self.agent, "max_steps", self._entry.tool_budget if matched else 0,
    )


def compile_project_agents(
  definitions: list[ProjectAgentDefinition],
  output_policy: ToolOutputPolicy,
) -> ProjectAgentCompilation:
  """Validate and compile project roles; errors disable the whole role set."""
  compilation = ProjectAgentCompilation()
  if len(definitions) > _MAX_PROJECT_AGENTS:
    compilation.diagnostics.append(
      ProjectAgentDiagnostic(
        level="error",
        message=f"At most {_MAX_PROJECT_AGENTS} project agents may be configured.",
      )
    )

  seen_tools: set[str] = set()
  valid: list[ProjectAgentDefinition] = []
  for definition in definitions:
    entry = definition.entry
    context = {"role": entry.name, "source": definition.source}
    if not _ROLE_NAME.fullmatch(entry.name):
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role name must match [A-Za-z0-9][A-Za-z0-9_-]*.",
          **context,
        )
      )
    if not entry.model.strip():
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role model must be configured explicitly.",
          **context,
        )
      )
    if not entry.system_prompt.strip():
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role system_prompt must not be empty.",
          **context,
        )
      )
    if not 1 <= entry.max_turns <= 5:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role max_turns must be between 1 and 5.",
          **context,
        )
      )
    if not 1 <= entry.max_invocations <= 5:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role max_invocations must be between 1 and 5.",
          **context,
        )
      )
    for tool_name in entry.allowed_tools:
      if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
        compilation.diagnostics.append(
          ProjectAgentDiagnostic(
            level="error",
            message=(
              f"allowed_tools entry {tool_name!r} must match [A-Za-z0-9_-]+."
            ),
            **context,
          )
        )
    if not 1 <= entry.tool_budget <= 32:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message="Role tool_budget must be between 1 and 32.",
          **context,
        )
      )
    tool_name = f"agent__{entry.name}"
    if tool_name in seen_tools:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message=f"Duplicate effective tool name: {tool_name}.",
          **context,
        )
      )
    seen_tools.add(tool_name)
    valid.append(definition)

  if compilation.errors:
    return compilation
  compilation.tools = [
    ProjectAgentTool(definition=definition, output_policy=output_policy) for definition in valid
  ]
  return compilation
