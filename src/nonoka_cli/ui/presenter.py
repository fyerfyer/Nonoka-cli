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
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.shell.commands import CommandInfo, CommandRegistry
from nonoka_cli.sessions.models import SessionInfo
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
  # Sessions
  # ------------------------------------------------------------------ #

  def show_session_info(self, info: SessionInfo) -> None:
    """Show current session details."""
    table = Table(
      title="Current Session",
      box=ROUNDED,
      border_style="cyan",
      show_header=True,
      header_style="bold",
    )
    table.add_column("Field", style="green", no_wrap=True)
    table.add_column("Value", style="white", ratio=1)

    name = info.name or "(unnamed)"
    table.add_row("Session ID", info.session_id)
    table.add_row("Name", name)
    table.add_row("Model", info.model)
    table.add_row("Created", str(info.created_at))
    table.add_row("Last active", str(info.last_active))
    table.add_row("Messages", str(info.message_count))

    self.console.print()
    self.console.print(table)
    self.console.print()

  def show_session_list(self, sessions: list[SessionInfo]) -> None:
    """Show a table of all sessions ordered by last activity."""
    table = Table(
      title="Sessions",
      box=ROUNDED,
      border_style="cyan",
      show_header=True,
      header_style="bold",
      expand=True,
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Model", style="yellow", no_wrap=True)
    table.add_column("Created", style="dim", no_wrap=True)
    table.add_column("Last active", style="dim", no_wrap=True)
    table.add_column("Messages", style="cyan", justify="right", no_wrap=True)

    for info in sessions:
      short_id = info.session_id[:8]
      name = info.name or "(unnamed)"
      table.add_row(
        short_id,
        name,
        info.model,
        str(info.created_at),
        str(info.last_active),
        str(info.message_count),
      )

    self.console.print()
    self.console.print(table)
    self.console.print()

  def show_session_switched(self, session_id: str) -> None:
    """Show session switch confirmation."""
    self.success(f"Switched to session [bold]{session_id[:8]}[/bold]")

  def show_session_renamed(self, name: str) -> None:
    """Show session rename confirmation."""
    self.success(f"Session renamed to [bold]'{name}'[/bold]")

  def show_session_deleted(self, session_id: str) -> None:
    """Show session deletion confirmation."""
    self.success(f"Session [bold]{session_id[:8]}[/bold] deleted")

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
  # MCP
  # ------------------------------------------------------------------ #

  def show_mcp_list(self, statuses: dict[str, MCPStatus]) -> None:
    """Show a table of all MCP server statuses."""
    table = Table(
      title="MCP Servers",
      box=ROUNDED,
      border_style="cyan",
      show_header=True,
      header_style="bold",
      expand=True,
    )
    table.add_column("Name", style="green", no_wrap=True)
    table.add_column("Status", style="yellow", no_wrap=True)
    table.add_column("Transport", style="dim", no_wrap=True)
    table.add_column("Tools", style="cyan", justify="right", no_wrap=True)
    table.add_column("Restarts", style="dim", justify="right", no_wrap=True)
    table.add_column("Last ping", style="dim", no_wrap=True)
    table.add_column("Error", style="red", ratio=1)

    if not statuses:
      table.add_row("(none)", "", "", "", "", "", "")

    for name, status in statuses.items():
      status_style = {
        "connected": "[green]connected[/green]",
        "connecting": "[yellow]connecting[/yellow]",
        "restarting": "[yellow]restarting[/yellow]",
        "error": "[red]error[/red]",
        "stopped": "[dim]stopped[/dim]",
      }.get(status.status, status.status)
      last_ping = str(status.last_ping) if status.last_ping else "-"
      error = status.error or ""
      table.add_row(
        name,
        status_style,
        status.transport,
        str(status.tool_count),
        str(status.restart_count),
        last_ping,
        error,
      )

    self.console.print()
    self.console.print(table)
    self.console.print()

  def show_mcp_restarted(self, status: MCPStatus) -> None:
    """Show MCP restart confirmation."""
    self.success(
      f"MCP server [bold]{status.name}[/bold] restarted. "
      f"Status: [green]{status.status}[/green], "
      f"Tools: [cyan]{status.tool_count}[/cyan]"
    )

  def show_mcp_added(self, status: MCPStatus) -> None:
    """Show MCP add confirmation."""
    self.success(
      f"MCP server [bold]{status.name}[/bold] added and connected. "
      f"Tools: [cyan]{status.tool_count}[/cyan]"
    )
