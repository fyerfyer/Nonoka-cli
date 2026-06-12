"""Terminal UI presenter for the REPL shell.

Provides rich-based formatting for help, command feedback, errors, and
prompts. Keeps presentation logic separate from the renderer that handles
Agent StreamEvents.
"""

from __future__ import annotations

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.shell.commands import CommandInfo, CommandRegistry
from nonoka_cli.ui.console import get_console


class UIPresenter:
  """Presents nonoka-cli shell output using rich."""

  def __init__(self, console=None):
    """Args:
      console: rich.Console instance. Uses global console if None.
    """
    self.console = console or get_console()

  # ------------------------------------------------------------------ #
  # General output
  # ------------------------------------------------------------------ #

  def success(self, message: str) -> None:
    """Show a success message."""
    self.console.print(f"[green]✓[/green] {message}")

  def error(self, message: str) -> None:
    """Show an error message in a styled panel."""
    self.console.print(Panel(message, title="[bold red]Error[/bold red]", border_style="red"))

  def warning(self, message: str) -> None:
    """Show a warning message."""
    self.console.print(f"[yellow]⚠[/yellow] {message}")

  def info(self, message: str) -> None:
    """Show an informational message."""
    self.console.print(f"[dim]{message}[/dim]")

  # ------------------------------------------------------------------ #
  # Lifecycle / banners
  # ------------------------------------------------------------------ #

  def show_banner(self, model: str, config_path: str | None = None) -> None:
    """Show the startup banner."""
    title = Text("nonoka-cli", style="bold cyan")
    subtitle = Text("Terminal frontend for the Nonoka Agent framework", style="dim")
    model_info = f"Model: [bold]{model}[/bold]"
    if config_path:
      model_info += f"  |  Config: [dim]{config_path}[/dim]"

    self.console.print()
    self.console.print(Panel(
      f"{subtitle}\n{model_info}",
      title=title,
      border_style="cyan",
      box=ROUNDED,
    ))
    self.console.print("[dim]Type /help for commands, /exit to quit.[/dim]\n")

  def show_goodbye(self) -> None:
    """Show the goodbye message."""
    self.console.print("\n[dim]Goodbye![/dim]")

  # ------------------------------------------------------------------ #
  # Commands
  # ------------------------------------------------------------------ #

  def show_new_session(self, session_id: str) -> None:
    """Show new session confirmation."""
    self.console.print(f"[green]New session[/green] [dim]{session_id}[/dim]")

  def show_model_switched(self, model: str, session_id: str) -> None:
    """Show model switch confirmation."""
    self.console.print(
      f"[green]Model switched[/green] to [bold]{model}[/bold]. "
      f"Session context preserved: [dim]{session_id}[/dim]"
    )

  def show_current_model(self, model: str) -> None:
    """Show the current active model."""
    self.console.print(f"Current model: [bold]{model}[/bold]")
    self.console.print("[dim]Usage:[/dim] /model <model>")

  def show_config_reloaded(self, config: CLIConfig) -> None:
    """Show config reload confirmation."""
    self.console.print(
      f"[green]Config reloaded[/green]. "
      f"model=[bold]{config.model}[/bold], "
      f"system_prompt_length=[dim]{len(config.system_prompt)}[/dim]"
    )

  def show_config_opened(self, path: str, editor: str) -> None:
    """Show config open confirmation."""
    self.console.print(
      f"[dim]Opened config in {editor}:[/dim] [cyan]{path}[/cyan]"
    )

  # ------------------------------------------------------------------ #
  # Help
  # ------------------------------------------------------------------ #

  def show_help(self, registry: CommandRegistry) -> None:
    """Show the full help table."""
    table = Table(
      title="Available Commands",
      box=ROUNDED,
      border_style="cyan",
      show_header=True,
      header_style="bold",
      expand=True,
    )
    table.add_column("Command", style="green", no_wrap=True)
    table.add_column("Usage", style="yellow", no_wrap=True)
    table.add_column("Description", style="white", ratio=1)

    for info in registry.all():
      usage = info.usage or ""
      aliases = f" (aliases: {', '.join(info.aliases)})" if info.aliases else ""
      table.add_row(f"/{info.name}{aliases}", usage, info.description)

    self.console.print()
    self.console.print(table)
    self.console.print("\n[dim]Any other text is sent to the AI assistant.[/dim]\n")

  def show_command_help(self, info: CommandInfo) -> None:
    """Show help for a single command."""
    usage = f" {info.usage}" if info.usage else ""
    aliases = f" (aliases: {', '.join(info.aliases)})" if info.aliases else ""

    self.console.print()
    self.console.print(
      f"[bold green]/{info.name}[/bold green]"
      f"[yellow]{usage}[/yellow]"
      f"[dim]{aliases}[/dim]"
    )
    self.console.print(f"  {info.description}\n")

  def show_unknown_command(self, command: str) -> None:
    """Show unknown command message."""
    self.console.print(
      f"[red]Unknown command:[/red] /{command}. "
      "Type [bold]/help[/bold] for available commands."
    )

  # ------------------------------------------------------------------ #
  # Prompt
  # ------------------------------------------------------------------ #

  def prompt_text(self) -> str:
    """Return the styled prompt string.

    The actual input reading is still done by the REPL so it can run in
    an executor without blocking the event loop.
    """
    return "[bold green]nonoka[/bold green][dim]>[/dim] "
