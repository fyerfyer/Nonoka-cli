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
