"""Pydantic models for CLI configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CLIBehaviorConfig(BaseModel):
  """CLI behavior configuration."""
  theme: str = "dark"
  auto_approve: bool = False
  editor: str = "${EDITOR:-nano}"
  max_history: int = 1000
  multi_line_trigger: str = '"""'


class HITLConfigModel(BaseModel):
  """HITL (Human-in-the-Loop) configuration."""
  policy: str = "interactive"
  dangerous_tools: list[str] = Field(default_factory=list)


class MCPServerConfigModel(BaseModel):
  """Single MCP server configuration."""
  transport: str
  command: str
  args: list[str] = Field(default_factory=list)


class ContextConfig(BaseModel):
  """In-context memory management configuration."""
  enabled: bool = True
  max_turns: int = 8
  max_tokens: int | None = None


class ToolOutputRuleConfig(BaseModel):
  """Per-tool output pruning rule configuration."""
  max_tokens: int = 4000
  max_lines: int | None = 200
  strategy: str = "tail_only"
  spill_dir: str | None = None


class ToolOutputConfig(BaseModel):
  """Tool output pruning / spill configuration."""
  enabled: bool = True
  rules: dict[str, ToolOutputRuleConfig] = Field(default_factory=dict)
  default_rule: ToolOutputRuleConfig = Field(default_factory=ToolOutputRuleConfig)


class TaskStateConfig(BaseModel):
  """Externalized task-state file configuration."""
  enabled: bool = True
  tasks_dir: str = ".nonoka/tasks"


class CLIConfig(BaseModel):
  """Top-level configuration for nonoka-cli.

  Matches the expected structure of ~/.config/nonoka/config.yaml.
  """
  model: str = "gpt-4o"
  system_prompt: str = ""
  api_key: str = ""
  base_url: str = ""
  mcp_servers: dict[str, MCPServerConfigModel] = Field(default_factory=dict)
  tool_paths: list[Path] = Field(default_factory=list)
  skills: list[str] = Field(default_factory=list)
  cli: CLIBehaviorConfig = Field(default_factory=CLIBehaviorConfig)
  hitl: HITLConfigModel = Field(default_factory=HITLConfigModel)
  context: ContextConfig = Field(default_factory=ContextConfig)
  tool_output: ToolOutputConfig = Field(default_factory=ToolOutputConfig)
  task_state: TaskStateConfig = Field(default_factory=TaskStateConfig)

  @field_validator("tool_paths", mode="before")
  @classmethod
  def _resolve_tool_paths(cls, v: Any) -> list[Path]:
    if v is None:
      return []
    return [Path(p).expanduser() for p in v]
