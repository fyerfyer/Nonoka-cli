"""Custom exceptions for nonoka-cli."""

from __future__ import annotations


class ConfigError(Exception):
  """Configuration loading or validation failed."""
  pass


class ConfigNotFoundError(ConfigError):
  """Configuration file not found."""
  pass


class AgentBuildError(Exception):
  """Failed to build Agent from configuration."""
  pass


class OrchestratorError(Exception):
  """Orchestrator execution error."""
  pass


class SessionError(Exception):
  """Session management error."""
  pass


class SessionNotFoundError(SessionError):
  """Session ID not found in store."""
  pass


class MCPError(Exception):
  """Base exception for MCP-related errors."""
  pass


class MCPConnectionError(MCPError):
  """Failed to connect to an MCP server."""
  pass


class MCPRestartExhaustedError(MCPError):
  """MCP server restart attempts exhausted."""
  pass
