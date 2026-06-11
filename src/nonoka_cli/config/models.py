"""Pydantic models for CLI configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CLIConfigModel(BaseModel):
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


class CLIConfig(BaseModel):
  """Top-level configuration for nonoka-cli.

  Matches the expected structure of ~/.config/nonoka/config.yaml.
  """
  model: str = "gpt-4o"
  system_prompt: str = "You are a helpful AI assistant."
  mcp_servers: dict[str, MCPServerConfigModel] = Field(default_factory=dict)
  tool_paths: list[Path] = Field(default_factory=list)
  skills: list[str] = Field(default_factory=list)
  cli: CLIConfigModel = Field(default_factory=CLIConfigModel)
  hitl: HITLConfigModel = Field(default_factory=HITLConfigModel)

  @field_validator("tool_paths", mode="before")
  @classmethod
  def _resolve_tool_paths(cls, v: Any) -> list[Path]:
    if v is None:
      return []
    return [Path(p).expanduser() for p in v]
