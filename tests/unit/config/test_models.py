from pathlib import Path

import pytest
from pydantic import ValidationError

from nonoka_cli.config.models import (
  CLIBehaviorConfig,
  CLIConfig,
  ContextConfig,
  HITLConfigModel,
  MCPServerConfigModel,
)


def test_cli_behavior_defaults():
  cfg = CLIBehaviorConfig()
  assert cfg.theme == "dark"
  assert cfg.auto_approve is False
  assert cfg.editor == "${EDITOR:-nano}"
  assert cfg.max_history == 1000
  assert cfg.multi_line_trigger == '"""'


def test_cli_config_defaults():
  cfg = CLIConfig()
  assert cfg.model == "gpt-4o"
  assert cfg.system_prompt == ""
  assert cfg.mcp_servers == {}
  assert cfg.tool_paths == []
  assert cfg.skills == []
  assert cfg.permissions == {}
  assert isinstance(cfg.cli, CLIBehaviorConfig)
  assert isinstance(cfg.hitl, HITLConfigModel)


def test_cli_config_tool_paths_expanded():
  cfg = CLIConfig(tool_paths=["~/tools", "./local_tools"])
  assert cfg.tool_paths[0] == Path.home() / "tools"
  assert cfg.tool_paths[1] == Path("./local_tools")


def test_mcp_server_config():
  cfg = MCPServerConfigModel(transport="stdio", command="npx", args=["-y", "server"])
  assert cfg.transport == "stdio"
  assert cfg.command == "npx"
  assert cfg.args == ["-y", "server"]
  assert cfg.startup_timeout_seconds == 20.0


def test_mcp_server_config_rejects_unknown_timeout_alias():
  with pytest.raises(ValidationError, match="start_timeout_seconds"):
    MCPServerConfigModel(
      transport="stdio",
      command="npx",
      start_timeout_seconds=60,
    )


def test_context_config_defaults():
  cfg = ContextConfig()
  assert cfg.enabled is True
  assert cfg.max_tokens is None
  assert cfg.reserve_output_tokens is None
  assert cfg.compaction_buffer_tokens is None
  assert cfg.summary_enabled is False


def test_context_config_parses_compaction_fields():
  cfg = ContextConfig.model_validate({
    "max_tokens": 60000,
    "reserve_output_tokens": 8192,
    "compaction_buffer_tokens": 4096,
    "summary_enabled": True,
  })
  assert cfg.max_tokens == 60000
  assert cfg.reserve_output_tokens == 8192
  assert cfg.compaction_buffer_tokens == 4096
  assert cfg.summary_enabled is True


def test_cli_config_roundtrip():
  data = {
    "model": "deepseek-chat",
    "system_prompt": "You are a helpful assistant.",
    "cli": {"theme": "light", "auto_approve": True},
    "hitl": {"policy": "auto", "dangerous_tools": ["execute_command"]},
    "permissions": {"glob": "allow", "bash": "deny"},
  }
  cfg = CLIConfig.model_validate(data)
  assert cfg.model == "deepseek-chat"
  assert cfg.cli.theme == "light"
  assert cfg.cli.auto_approve is True
  assert cfg.hitl.dangerous_tools == ["execute_command"]
  assert cfg.permissions == {"glob": "allow", "bash": "deny"}


def test_cli_config_rejects_invalid_permission_action():
  with pytest.raises(ValidationError, match="permissions.bad-tool"):
    CLIConfig(permissions={"bad-tool": "sometimes"})
