"""REPL — Read-Eval-Print Loop for nonoka-cli.

Provides interactive terminal session with command parsing,
interrupt handling, and prompt execution.
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.ui.renderer import Renderer
from nonoka_cli.utils.errors import CLIError, OrchestratorError

logger = structlog.get_logger("nonoka_cli.shell")

PROMPT = "nonoka> "


class REPL:
  """Interactive REPL for nonoka-cli.

  Features:
  - Read user input line by line
  - Route /-prefixed commands to handlers
  - Pass plain text to orchestrator for Agent execution
  - Handle Ctrl+C (interrupt) and Ctrl+D (exit)
  """

  def __init__(
    self,
    orchestrator: Orchestrator,
    renderer: Renderer | None = None,
  ):
    self._orchestrator = orchestrator
    self._renderer = renderer or Renderer()
    self._running = False
    self._current_task: asyncio.Task | None = None

  async def run(self) -> None:
    """Start the REPL main loop.

    Reads from stdin, dispatches commands, and executes prompts
    via the orchestrator until the user exits.
    """
    self._running = True
    logger.info("repl_started")

    while self._running:
      try:
        user_input = await self._read_input()
      except EOFError:
        self._running = False
        print("\nGoodbye!")
        break
      except KeyboardInterrupt:
        print("\n")
        continue

      if not user_input:
        continue

      if user_input.startswith("/"):
        await self._handle_command(user_input)
      else:
        await self._handle_prompt(user_input)

    logger.info("repl_stopped")

  async def _read_input(self) -> str:
    """Read a line of input from stdin asynchronously.

    Returns:
      Stripped user input string.

    Raises:
      EOFError: On Ctrl+D (empty read).
    """
    loop = asyncio.get_event_loop()
    # Use loop.run_in_executor so input() does not block the event loop
    raw = await loop.run_in_executor(None, input, PROMPT)
    if raw is None:
      raise EOFError()
    return raw.strip()

  async def _handle_command(self, raw: str) -> None:
    """Handle a /-prefixed CLI command.

    Args:
      raw: The full command string (e.g. "/exit").
    """
    parts = raw[1:].split()
    if not parts:
      return

    cmd = parts[0].lower()
    args = parts[1:]

    match cmd:
      case "exit" | "quit":
        self._running = False
        print("Goodbye!")

      case "new":
        session_id = self._orchestrator.new_session()
        print(f"New session: {session_id}")

      case "help":
        self._print_help()

      case _:
        print(f"Unknown command: /{cmd}. Type /help for available commands.")

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
      print("\n[Cancelled]")
    except OrchestratorError as exc:
      print(f"\n[Error] {exc}")
    except CLIError as exc:
      print(f"\n[Error] {exc}")
    except Exception as exc:
      logger.error("unexpected_error", error=str(exc))
      print(f"\n[Unexpected Error] {exc}")
    finally:
      self._current_task = None

  def _print_help(self) -> None:
    """Print available commands."""
    print("""
Available commands:
  /exit         Exit the CLI
  /new          Start a new session
  /help         Show this help message

Any other text is sent to the AI assistant.
""")

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
      print("\n[Cancelled]")
