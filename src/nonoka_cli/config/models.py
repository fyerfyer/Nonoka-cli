"""Pydantic models for CLI configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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


class SafetyConfig(BaseModel):
  """Runtime command and filesystem restrictions."""

  enabled: bool = True
  allowed_roots: list[Path] = Field(default_factory=list)
  # Preserve the legacy no-section behaviour. `config init` writes the new
  # secure profile explicitly, so upgrades are never silently disruptive.
  sandbox: str = "docker"  # auto | srt | docker | disabled
  required: bool = False
  allow_read: list[Path] = Field(default_factory=list)
  allow_write: list[Path] = Field(default_factory=list)
  deny_read: list[Path] = Field(default_factory=list)
  deny_write: list[Path] = Field(default_factory=lambda: [Path(".env"), Path(".git/hooks")])
  allowed_domains: list[str] = Field(default_factory=list)
  command_timeout_seconds: int = Field(default=120, ge=1)
  max_output_bytes: int = Field(default=1_000_000, ge=1024)
  docker_image: str = "alpine:3.20"


class CacheConfig(BaseModel):
  enabled: bool = True
  path: Path = Field(
    default_factory=lambda: Path.home() / ".cache" / "nonoka" / "llm-cache.sqlite3"
  )
  ttl_seconds: int = Field(default=604800, ge=1)
  semantic_enabled: bool = False
  embedding_model: str | None = None
  embedding_api_base: str | None = None
  embedding_api_key_env: str = "DASHSCOPE_API_KEY"
  embedding_dimensions: int | None = Field(default=None, ge=1)
  semantic_threshold: float = Field(default=0.92, ge=0.0, le=1.0)


class BudgetConfig(BaseModel):
  max_total_tokens: int | None = Field(default=None, ge=1)
  max_cost_usd: float | None = Field(default=None, gt=0)
  fail_on_unknown_cost: bool = True


class MCPServerConfigModel(BaseModel):
  """Single MCP server configuration."""

  transport: str
  command: str
  args: list[str] = Field(default_factory=list)
  startup_timeout_seconds: float = Field(default=20.0, ge=1.0, le=300.0)


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


class GitConfig(BaseModel):
  """Git checkpoint / rollback configuration."""

  enabled: bool = True
  auto_checkpoint: bool = True
  rollback_on_error: bool = True
  commit_message: str = "auto"  # auto | simple | custom template
  attribution: bool = True


class RepoMapConfig(BaseModel):
  """Repo map / symbol indexing configuration."""

  enabled: bool = True
  max_tokens: int = 2048
  languages: list[str] = Field(default_factory=list)
  index_path: str = ".nonoka/repo_map.jsonl"
  include: list[str] = Field(default_factory=list)
  lsp_languages: dict[str, str] = Field(
    default_factory=lambda: {
      ".py": "python",
      ".js": "javascript",
      ".jsx": "javascript",
      ".ts": "typescript",
      ".tsx": "typescript",
      ".rs": "rust",
      ".go": "go",
      ".java": "java",
      ".c": "c",
      ".cpp": "cpp",
      ".h": "c",
      ".hpp": "cpp",
      ".cs": "csharp",
      ".rb": "ruby",
      ".php": "php",
    }
  )
  exclude: list[str] = Field(
    default_factory=lambda: [
      ".git",
      "node_modules",
      ".venv",
      "venv",
      "__pycache__",
      ".pytest_cache",
      ".mypy_cache",
      ".tox",
      "dist",
      "build",
      ".eggs",
    ]
  )


class AgentRoleConfig(BaseModel):
  """Compatibility configuration for the main executor role."""

  model: str = ""
  # Long-lived interactive chats should not inherit a hidden five-turn cap.
  # Set this explicitly when a project needs a cumulative model-turn guard.
  max_turns: int | None = Field(default=None, ge=1)
  # Interactive OpenCode sessions are intentionally unbounded by default.
  # The framework otherwise applies its conservative legacy default (50),
  # which can terminate a long-lived chat after unrelated earlier work.
  max_steps: int | None = Field(default=None, ge=1)
  system_prompt: str = ""


class AgentsConfig(BaseModel):
  """Compatibility configuration for the main executor budget."""

  executor: AgentRoleConfig = Field(default_factory=AgentRoleConfig)


class PluginConfig(BaseModel):
  """Plugin manifest discovery configuration."""

  enabled: bool = True
  manifests: list[Path] = Field(default_factory=list)

  @field_validator("manifests", mode="before")
  @classmethod
  def _resolve_manifests(cls, v: Any) -> list[Path]:
    if v is None:
      return []
    return [Path(p).expanduser() for p in v]


class CLIConfig(BaseModel):
  """Top-level configuration for nonoka-cli.

  Matches the expected structure of ~/.config/nonoka/config.yaml.
  """

  model: str = "gpt-4o"
  system_prompt: str = ""
  api_key: str = ""
  base_url: str = ""
  permissions: dict[str, Literal["ask", "allow", "deny"]] = Field(default_factory=dict)
  mcp_servers: dict[str, MCPServerConfigModel] = Field(default_factory=dict)
  tool_paths: list[Path] = Field(default_factory=list)
  skills: list[str] = Field(default_factory=list)
  cli: CLIBehaviorConfig = Field(default_factory=CLIBehaviorConfig)
  hitl: HITLConfigModel = Field(default_factory=HITLConfigModel)
  safety: SafetyConfig = Field(default_factory=SafetyConfig)
  cache: CacheConfig = Field(default_factory=CacheConfig)
  budget: BudgetConfig = Field(default_factory=BudgetConfig)
  context: ContextConfig = Field(default_factory=ContextConfig)
  tool_output: ToolOutputConfig = Field(default_factory=ToolOutputConfig)
  task_state: TaskStateConfig = Field(default_factory=TaskStateConfig)
  git: GitConfig = Field(default_factory=GitConfig)
  repo_map: RepoMapConfig = Field(default_factory=RepoMapConfig)
  agents: AgentsConfig = Field(default_factory=AgentsConfig)
  plugins: PluginConfig = Field(default_factory=PluginConfig)

  @field_validator("tool_paths", mode="before")
  @classmethod
  def _resolve_tool_paths(cls, v: Any) -> list[Path]:
    if v is None:
      return []
    return [Path(p).expanduser() for p in v]
