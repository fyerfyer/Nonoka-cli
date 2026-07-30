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
  DynamicAgentEntry,
  LoadedPluginManifest,
)
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy

_ROLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MAX_PROJECT_AGENTS = 8


@dataclass(frozen=True)
class ProjectAgentDefinition:
  entry: AgentEntry
  source: Path


@dataclass(frozen=True)
class DynamicAgentDefinition:
  entry: DynamicAgentEntry
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


def effective_dynamic_agent_definition(
  manifests: list[LoadedPluginManifest],
) -> DynamicAgentDefinition | None:
  """Return the last explicitly configured dynamic-agent policy."""
  effective: DynamicAgentDefinition | None = None
  for loaded in manifests:
    if loaded.manifest.dynamic_agent is not None:
      effective = DynamicAgentDefinition(
        entry=loaded.manifest.dynamic_agent,
        source=loaded.path,
      )
  return effective


class ProjectAgentTool(AgentTool):
  """AgentTool with per-parent invocation limits and structured results."""

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
      .max_steps(0)
      .metadata(
        project_agent_role=entry.name,
        project_agent_source=str(definition.source),
      )
      .tag("subagent", "project-defined")
      .build()
    )
    super().__init__(
      agent=child,
      name=tool_name,
      description=entry.description or f"Delegate advisory work to the {entry.name} role.",
      memory_strategy=MemoryStrategy.ISOLATE,
      max_depth=1,
      result_extractor=extract,
    )
    self.max_invocations = entry.max_invocations
    # Child calls are workspace-read-only, but they temporarily select their
    # model on the shared Runner. Keep them serial within a parent turn.
    self._execution = ToolExecution(read_only=True, stateful_action=True)
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
    state[self.name] = count + 1
    return await super().invoke(ctx, arguments)


class DynamicProjectAgentTool(Capability):
  """Create one policy-bounded, tool-free advisory child per invocation."""

  def __init__(
    self,
    *,
    definition: DynamicAgentDefinition,
    output_policy: ToolOutputPolicy,
  ) -> None:
    self.definition = definition
    self.config = definition.entry
    self.output_policy = output_policy
    self.metadata = {
      "kind": "dynamic_project_agent",
      "source": str(definition.source),
    }
    self._execution = ToolExecution(read_only=True, stateful_action=True)

  @property
  def name(self) -> str:
    return "agent__spawn"

  @property
  def description(self) -> str:
    return self.config.description

  @property
  def execution(self) -> ToolExecution:
    return self._execution

  @property
  def parameters(self) -> dict[str, Any]:
    return {
      "type": "object",
      "properties": {
        "role": {
          "type": "string",
          "maxLength": self.config.max_role_chars,
          "description": "Short advisory role name, such as api-reviewer.",
        },
        "instructions": {
          "type": "string",
          "maxLength": self.config.max_instruction_chars,
          "description": (
            "Bounded role-specific guidance. It cannot grant tools or change the model."
          ),
        },
        "task": {
          "type": "string",
          "maxLength": self.config.max_task_chars,
          "description": "The concrete question or task to delegate.",
        },
        "context": {
          "type": "string",
          "maxLength": self.config.max_context_chars,
          "description": "Optional self-contained context needed to answer the task.",
        },
      },
      "required": ["role", "task"],
      "additionalProperties": False,
    }

  def to_json_schema(self) -> dict[str, Any]:
    return {
      "type": "function",
      "function": {
        "name": self.name,
        "description": self.description,
        "parameters": self.parameters,
      },
    }

  def _invalid_arguments(self, arguments: dict[str, Any]) -> str | None:
    limits = {
      "role": self.config.max_role_chars,
      "instructions": self.config.max_instruction_chars,
      "task": self.config.max_task_chars,
      "context": self.config.max_context_chars,
    }
    for key, limit in limits.items():
      value = arguments.get(key, "")
      if not isinstance(value, str):
        return f"{key} must be a string."
      if len(value) > limit:
        return f"{key} exceeds the configured limit of {limit} characters."
    if not arguments.get("role", "").strip():
      return "role must not be empty."
    if not _ROLE_NAME.fullmatch(arguments["role"].strip()):
      return "role must match [A-Za-z0-9][A-Za-z0-9_-]*."
    if not arguments.get("task", "").strip():
      return "task must not be empty."
    return None

  async def invoke(self, ctx: RunContext, arguments: dict[str, Any]) -> Any:
    invalid = self._invalid_arguments(arguments)
    if invalid:
      return {"success": False, "error": invalid, "error_type": "invalid_arguments"}

    state = ctx.session.extension_state.setdefault("project_agents", {})
    count = int(state.get(self.name, 0))
    if count >= self.config.max_invocations:
      return {
        "success": False,
        "error": (
          f"Dynamic agent invocation limit reached: "
          f"{self.config.max_invocations} per parent session."
        ),
        "error_type": "invocation_limit",
      }
    state[self.name] = count + 1

    role = arguments["role"].strip()
    instructions = arguments.get("instructions", "").strip()
    system_prompt = self.config.base_system_prompt.strip()
    if instructions:
      system_prompt += f"\n\nRole: {role}\nRole-specific instructions:\n{instructions}"
    else:
      system_prompt += f"\n\nRole: {role}"
    child = (
      AgentBuilder()
      .model(self.config.model.strip())
      .system_prompt(system_prompt)
      .max_turns(self.config.max_turns)
      .max_steps(0)
      .metadata(
        project_agent_role=role,
        project_agent_source=str(self.definition.source),
        project_agent_dynamic=True,
      )
      .tag("subagent", "project-defined", "dynamic")
      .build()
    )

    def extract(result: RunResult) -> dict[str, Any]:
      session = result.session
      termination = None
      if session is not None and session.runtime_state.termination is not None:
        termination = session.runtime_state.termination.model_dump(mode="json")
      payload: dict[str, Any] = {
        "role": role,
        "session_id": session.session_id if session is not None else None,
        "success": result.success,
        "termination": termination,
      }
      if result.success:
        payload["result"] = self.output_policy.apply(self.name, result.data)
      else:
        payload["error"] = result.error or "Dynamic sub-agent execution failed."
        payload["error_type"] = result.error_type or "unknown"
      return payload

    delegate = AgentTool(
      agent=child,
      name=self.name,
      description=self.description,
      memory_strategy=MemoryStrategy.ISOLATE,
      max_depth=1,
      result_extractor=extract,
    )
    return await delegate.invoke(
      ctx,
      {"task": arguments["task"].strip(), "context": arguments.get("context", "")},
    )


def compile_project_agents(
  definitions: list[ProjectAgentDefinition],
  output_policy: ToolOutputPolicy,
  dynamic_definition: DynamicAgentDefinition | None = None,
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
    if entry.allowed_tools:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="warning",
          message=(
            "allowed_tools is ignored in the first project-agent release; "
            "child agents remain tool-free."
          ),
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
  if dynamic_definition is not None and dynamic_definition.entry.enabled:
    dynamic = dynamic_definition.entry
    context = {"role": "dynamic", "source": dynamic_definition.source}
    checks = [
      (not dynamic.model.strip(), "Dynamic agent model must be configured explicitly."),
      (
        not dynamic.base_system_prompt.strip(),
        "Dynamic agent base_system_prompt must not be empty.",
      ),
      (not 1 <= dynamic.max_turns <= 5, "Dynamic agent max_turns must be between 1 and 5."),
      (
        not 1 <= dynamic.max_invocations <= 5,
        "Dynamic agent max_invocations must be between 1 and 5.",
      ),
      (not 1 <= dynamic.max_role_chars <= 200, "max_role_chars must be between 1 and 200."),
      (
        not 1 <= dynamic.max_instruction_chars <= 8000,
        "max_instruction_chars must be between 1 and 8000.",
      ),
      (not 1 <= dynamic.max_task_chars <= 32000, "max_task_chars must be between 1 and 32000."),
      (
        not 1 <= dynamic.max_context_chars <= 64000,
        "max_context_chars must be between 1 and 64000.",
      ),
    ]
    for failed, message in checks:
      if failed:
        compilation.diagnostics.append(
          ProjectAgentDiagnostic(level="error", message=message, **context)
        )
    if "agent__spawn" in seen_tools:
      compilation.diagnostics.append(
        ProjectAgentDiagnostic(
          level="error",
          message=(
            "Dynamic agent tool name agent__spawn collides with the static "
            "project role named spawn."
          ),
          **context,
        )
      )
    if compilation.errors:
      compilation.tools = []
      return compilation
    compilation.tools.append(
      DynamicProjectAgentTool(
        definition=dynamic_definition,
        output_policy=output_policy,
      )
    )
  return compilation
