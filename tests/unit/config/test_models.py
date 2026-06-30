from pathlib import Path

from nonoka_cli.config.models import (
  CLIBehaviorConfig,
  CLIConfig,
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


def test_cli_config_roundtrip():
  data = {
    "model": "deepseek-chat",
    "system_prompt": "You are a helpful assistant.",
    "cli": {"theme": "light", "auto_approve": True},
    "hitl": {"policy": "auto", "dangerous_tools": ["execute_command"]},
  }
  cfg = CLIConfig.model_validate(data)
  assert cfg.model == "deepseek-chat"
  assert cfg.cli.theme == "light"
  assert cfg.cli.auto_approve is True
  assert cfg.hitl.dangerous_tools == ["execute_command"]
