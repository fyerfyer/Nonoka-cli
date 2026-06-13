"""REPL — Read-Eval-Print Loop for nonoka-cli.

Provides interactive terminal session with command parsing,
interrupt handling, tab completion, and prompt execution.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from functools import partial

import structlog

from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.shell.commands import CommandContext, CommandRegistry, CommandRouter
from nonoka_cli.shell.prompt_input import PromptInput
from nonoka_cli.ui.presenter import UIPresenter
from nonoka_cli.ui.renderer import Renderer
from nonoka_cli.utils.errors import (
  CLIError,
  ConfigError,
  MCPError,
  OrchestratorError,
  SessionError,
  SessionNotFoundError,
  UnknownCommandError,
)

logger = structlog.get_logger("nonoka_cli.shell")


class REPL:
  """Interactive REPL for nonoka-cli.

  Features:
  - Read user input line by line with a rich-styled prompt
  - Route /-prefixed commands to handlers via a CommandRouter
  - Provide tab completion for commands
  - Pass plain text to orchestrator for Agent execution
  - Handle Ctrl+C (interrupt) and Ctrl+D (exit)
  """

  def __init__(
    self,
    orchestrator: Orchestrator,
    renderer: Renderer | None = None,
    presenter: UIPresenter | None = None,
    prompt_input: PromptInput | None = None,
  ):
    self._orchestrator = orchestrator
    self._renderer = renderer or Renderer()
    self._presenter = presenter or UIPresenter()
    self._running = False
    self._current_task: asyncio.Task | None = None

    self._registry = CommandRegistry()
    self._register_commands()
    self._router = CommandRouter(self._registry)
    self._prompt_input_instance: PromptInput | None = prompt_input

  @property
  def registry(self) -> CommandRegistry:
    """Command registry (exposed for completion / external use)."""
    return self._registry

  @property
  def _prompt_input(self) -> PromptInput:
    """Lazy prompt input backed by current config."""
    if self._prompt_input_instance is None:
      self._prompt_input_instance = self._create_prompt_input()
    return self._prompt_input_instance

  def _create_prompt_input(self) -> PromptInput:
    """Create the prompt input using current config settings."""
    config = self._orchestrator.config
    return PromptInput(
      self._registry,
      max_history=config.cli.max_history,
      multi_line_trigger=config.cli.multi_line_trigger,
    )

  def _register_commands(self) -> None:
    """Register all internal CLI command handlers."""
    self._registry.register(
      "exit",
      partial(self._cmd_exit),
      description="Exit the CLI",
      aliases=("quit",),
    )
    self._registry.register(
      "new",
      partial(self._cmd_new),
      description="Start a new conversation session",
      usage="[name]",
    )
    self._registry.register(
      "session",
      partial(self._cmd_session),
      description="Manage conversation sessions",
      usage="[list | switch <id> | rename <name> | delete <id>]",
    )
    self._registry.register(
      "model",
      partial(self._cmd_model),
      description="Switch the active LLM model",
      usage="<model>",
    )
    self._registry.register(
      "config",
      partial(self._cmd_config),
      description="Open config in $EDITOR or reload it",
      usage="[reload]",
    )
    self._registry.register(
      "mcp",
      partial(self._cmd_mcp),
      description="Manage MCP servers",
      usage="[list | restart <name> | add <name> <command> [args...]]",
    )
    self._registry.register(
      "tool",
      partial(self._cmd_tool),
      description="List and inspect available tools",
      usage="[list | info <name> | reload]",
    )
    self._registry.register(
      "help",
      partial(self._cmd_help),
      description="Show help for commands",
      usage="[command]",
    )

  # ------------------------------------------------------------------ #
  # Command handlers
  # ------------------------------------------------------------------ #

  async def _cmd_exit(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /exit and /quit."""
    self._running = False
    self._presenter.show_goodbye()

  async def _cmd_new(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /new [name] — create a new session."""
    name = " ".join(args) if args else None
    session_id = await self._orchestrator.new_session(name=name)
    self._presenter.show_new_session(session_id)

  async def _cmd_session(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /session sub-commands."""
    if not args:
      try:
        info = await self._orchestrator.get_current_session()
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_session_info(info)
      return

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "list":
      try:
        sessions = await self._orchestrator.list_sessions()
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_session_list(sessions)

    elif subcommand == "switch":
      if not sub_args:
        self._presenter.error("Usage: /session switch <session_id>")
        return
      session_id = sub_args[0]
      try:
        await self._orchestrator.switch_session(session_id)
      except SessionNotFoundError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_session_switched(session_id)

    elif subcommand == "rename":
      if not sub_args:
        self._presenter.error("Usage: /session rename <name>")
        return
      name = " ".join(sub_args)
      try:
        await self._orchestrator.rename_session(name)
      except (OrchestratorError, SessionNotFoundError) as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_session_renamed(name)

    elif subcommand == "delete":
      if not sub_args:
        self._presenter.error("Usage: /session delete <session_id>")
        return
      session_id = sub_args[0]
      try:
        await self._orchestrator.delete_session(session_id)
      except (SessionError, SessionNotFoundError, OrchestratorError) as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_session_deleted(session_id)

    else:
      self._presenter.error(f"Unknown /session subcommand: {subcommand}")

  async def _cmd_model(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /model <model> — switch model and rebuild Agent."""
    if not args:
      self._presenter.show_current_model(self._orchestrator.config.model)
      return

    model = args[0].strip()
    old_session_id = self._orchestrator.session_id

    try:
      await self._orchestrator.switch_model(model)
    except (ConfigError, OrchestratorError) as exc:
      self._presenter.error(str(exc))
      return

    self._presenter.show_model_switched(model, old_session_id)

  async def _cmd_config(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /config and /config reload."""
    if args and args[0].lower() == "reload":
      try:
        await self._orchestrator.reload_config()
      except ConfigError as exc:
        self._presenter.error(f"Config reload failed:\n{exc}")
        return
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return

      self._presenter.show_config_reloaded(self._orchestrator.config)
      # Rebuild prompt input so new max_history / multi_line_trigger take effect.
      self._prompt_input = self._create_prompt_input()
      return

    # Open config in $EDITOR
    config_manager = self._orchestrator.config_manager
    if config_manager is None or config_manager.config_path is None:
      self._presenter.error("No config file path is available.")
      return

    editor = self._orchestrator.config.cli.editor
    if not editor:
      editor = os.environ.get("EDITOR", "nano")

    config_path = str(config_manager.config_path)
    try:
      cmd = shlex.split(editor) + [config_path]
      subprocess.run(cmd, check=False)
      self._presenter.show_config_opened(config_path, editor)
    except FileNotFoundError:
      self._presenter.error(f"Editor not found: {editor}")
    except Exception as exc:
      self._presenter.error(f"Failed to open config: {exc}")

  async def _cmd_tool(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /tool list, /tool info <name>, and /tool reload."""
    if not args:
      try:
        tools = self._orchestrator.list_tools()
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_tool_list(tools)
      return

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "list":
      try:
        tools = self._orchestrator.list_tools()
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_tool_list(tools)

    elif subcommand == "info":
      if not sub_args:
        self._presenter.error("Usage: /tool info <tool_name>")
        return
      name = sub_args[0]
      schema = self._orchestrator.get_tool_info(name)
      if schema is None:
        self._presenter.show_tool_not_found(name)
        return
      self._presenter.show_tool_info(name, schema)

    elif subcommand == "reload":
      try:
        tools = await self._orchestrator.reload_tools()
      except OrchestratorError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_tool_reload_summary(len(tools))

    else:
      self._presenter.error(f"Unknown /tool subcommand: {subcommand}")

  async def _cmd_mcp(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /mcp list and /mcp restart <name>."""
    if not args:
      statuses = self._orchestrator.list_mcp_status()
      self._presenter.show_mcp_list(statuses)
      return

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "list":
      statuses = self._orchestrator.list_mcp_status()
      self._presenter.show_mcp_list(statuses)

    elif subcommand == "restart":
      if not sub_args:
        self._presenter.error("Usage: /mcp restart <server_name>")
        return
      name = sub_args[0]
      try:
        status = await self._orchestrator.restart_mcp(name)
      except MCPError as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_mcp_restarted(status)

    elif subcommand == "add":
      if len(sub_args) < 2:
        self._presenter.error("Usage: /mcp add <name> <command> [args...]")
        return
      name = sub_args[0]
      command = sub_args[1]
      args = sub_args[2:]
      from nonoka_cli.config.models import MCPServerConfigModel
      config = MCPServerConfigModel(transport="stdio", command=command, args=args)
      try:
        status = await self._orchestrator.add_mcp_server(name, config)
      except (MCPError, ConfigError) as exc:
        self._presenter.error(str(exc))
        return
      self._presenter.show_mcp_added(status)

    else:
      self._presenter.error(f"Unknown /mcp subcommand: {subcommand}")

  async def _cmd_help(self, ctx: CommandContext, args: list[str]) -> None:
    """Handle /help [command]."""
    if args:
      name = args[0].lower().lstrip("/")
      info = self._registry.get(name)
      if info is None:
        self._presenter.show_unknown_command(name)
        return

      self._presenter.show_command_help(info)
      return

    self._presenter.show_help(self._registry)

  # ------------------------------------------------------------------ #
  # REPL loop
  # ------------------------------------------------------------------ #

  async def run(self) -> None:
    """Start the REPL main loop.

    Reads from stdin, dispatches commands, and executes prompts
    via the orchestrator until the user exits.
    """
    self._running = True
    logger.info("repl_started")

    while self._running:
      try:
        user_input = await self._prompt_input.read()
      except EOFError:
        self._running = False
        self._presenter.show_goodbye()
        break
      except KeyboardInterrupt:
        self._presenter.console.print()
        continue

      if not user_input:
        continue

      if user_input.startswith("/"):
        await self._handle_command(user_input)
      else:
        await self._handle_prompt(user_input)

    logger.info("repl_stopped")

  async def _handle_command(self, raw: str) -> None:
    """Handle a /-prefixed CLI command.

    Args:
      raw: The full command string (e.g. "/exit").
    """
    ctx = CommandContext(orchestrator=self._orchestrator, renderer=self._renderer)

    try:
      await self._router.dispatch(ctx, raw)
    except UnknownCommandError as exc:
      self._presenter.show_unknown_command(exc.command)
    except CLIError as exc:
      self._presenter.error(str(exc))
    except Exception as exc:
      logger.error("command_failed", error=str(exc))
      self._presenter.error(f"Unexpected error: {exc}")

  async def _handle_prompt(self, prompt: str) -> None:
    """Execute a plain-text prompt through the orchestrator.

    Args:
      prompt: User's natural language input.
    """
    try:
      stream = self._orchestrator.execute(prompt)
      self._current_task = asyncio.create_task(
        self._renderer.render_stream(stream)
      )
      await self._current_task
    except asyncio.CancelledError:
      self._presenter.warning("[Cancelled]")
    except OrchestratorError as exc:
      self._presenter.error(str(exc))
    except CLIError as exc:
      self._presenter.error(str(exc))
    except Exception as exc:
      logger.error("unexpected_error", error=str(exc))
      self._presenter.error(f"Unexpected error: {exc}")
    finally:
      self._current_task = None

  async def interrupt(self) -> None:
    """Interrupt the current execution (called on Ctrl+C).

    Cancels the running render task and clears output.
    """
    if self._current_task and not self._current_task.done():
      self._current_task.cancel()
      self._renderer.clear_current_output()
      try:
        await self._current_task
      except asyncio.CancelledError:
        pass
      self._presenter.warning("[Cancelled]")
