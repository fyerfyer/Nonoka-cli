"""Custom exceptions for nonoka-cli."""

from __future__ import annotations


class CLIError(Exception):
  """Base exception for all CLI errors."""
  pass


class ConfigError(CLIError):
  """Configuration loading or validation failed."""
  pass


class ConfigNotFoundError(ConfigError):
  """Configuration file not found."""
  pass


class AgentBuildError(CLIError):
  """Failed to build Agent from configuration."""
  pass


class OrchestratorError(CLIError):
  """Orchestrator execution error."""
  pass


class UnknownCommandError(CLIError):
  """Unknown / invalid CLI command."""

  def __init__(self, command: str):
    self.command = command
    super().__init__(f"Unknown command: /{command}. Type /help for available commands.")


class SessionError(CLIError):
  """Session management error."""
  pass


class SessionNotFoundError(SessionError):
  """Session ID not found in store."""
  pass


class MCPError(CLIError):
  """Base exception for MCP-related errors."""
  pass


class MCPConnectionError(MCPError):
  """Failed to connect to an MCP server."""
  pass


class MCPRestartExhaustedError(MCPError):
  """MCP server restart attempts exhausted."""
  pass
