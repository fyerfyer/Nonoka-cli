"""Handle a single chat request in the nonoka-cli --server mode."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.events import translate_stream_event
from nonoka_cli.bridge.protocol import (
  ApprovalResponse,
  ChatRequest,
  ErrorEvent,
  FinishEvent,
  OutboundMessage,
  SessionInitEvent,
  encode_outbound_message,
)
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.utils.errors import SessionNotFoundError

logger = structlog.get_logger("nonoka_cli.bridge.handler")


class ChatRequestHandler:
  """Process one ``ChatRequest`` and stream the result back.

  Responsibilities:
  - Lazily initialize an ``Orchestrator`` for the request's working directory.
  - Extract the latest user message as the prompt.
  - Switch sessions when the provider supplies a ``session_id``.
  - Translate nonoka ``StreamEvent`` objects into outbound NDJSON messages.
  """

  def __init__(
    self,
    send: Any,
    config_path: Path | str | None = None,
    model: str | None = None,
  ):
    """Args:
      send: Async callable accepting an outbound message and writing it to stdout.
      config_path: Optional explicit path to the nonoka config file.
      model: Optional model override.
    """
    self._send = send
    self._config_path = config_path
    self._model = model
    self._orchestrator: Orchestrator | None = None
    self._session_id: str | None = None
    self._session_init_sent = False
    self._working_dir: Path = Path.cwd()

  async def send(self, msg: OutboundMessage) -> None:
    """Send a single outbound message."""
    await self._send(msg)

  @property
  def orchestrator(self) -> Orchestrator | None:
    """Current orchestrator instance, if any."""
    return self._orchestrator

  async def handle(self, msg: ChatRequest) -> None:
    """Handle a chat request end-to-end."""
    try:
      await self._ensure_orchestrator(msg)
    except Exception as exc:
      logger.error("orchestrator_init_failed", error=str(exc))
      await self._send(ErrorEvent(message=f"Initialization failed: {exc}"))
      return

    if self._orchestrator is None:
      await self._send(ErrorEvent(message="Orchestrator not available"))
      return

    # Use provided session_id or the orchestrator's current session.
    await self._apply_session(msg.session_id)

    # Let the provider know the session id on the first successful response.
    if not self._session_init_sent and self._session_id:
      await self._send(SessionInitEvent(session_id=self._session_id))
      self._session_init_sent = True

    prompt = self._extract_prompt(msg)
    if not prompt:
      await self._send(ErrorEvent(message="No user message found in chat request"))
      return

    stream = self._orchestrator.execute(prompt, working_dir=self._working_dir)
    await self._consume_stream(stream)

  async def handle_approval(self, msg: ApprovalResponse) -> None:
    """Process a user approval decision.

    Currently a no-op placeholder; approval flow will be wired up once the
    nonoka backend exposes an approval callback mechanism.
    """
    logger.warning(
      "approval_response_ignored",
      approval_id=msg.id,
      approved=msg.approved,
    )

  async def _ensure_orchestrator(self, msg: ChatRequest) -> None:
    """Initialize the orchestrator on first request."""
    if self._orchestrator is not None:
      return

    self._working_dir = Path(msg.cwd or ".").resolve()

    self._orchestrator = Orchestrator()
    await self._orchestrator.initialize(config_path=self._config_path)
    if self._model:
      await self._orchestrator.switch_model(self._model)
    elif msg.model:
      await self._orchestrator.switch_model(msg.model)

    self._session_id = self._orchestrator.session_id

  async def _apply_session(self, session_id: str | None) -> None:
    """Switch orchestrator session if the provider sent a different one."""
    if not session_id or self._orchestrator is None:
      self._session_id = self._orchestrator.session_id if self._orchestrator else None
      return

    if session_id != self._orchestrator.session_id:
      try:
        await self._orchestrator.switch_session(session_id)
      except SessionNotFoundError:
        logger.warning(
          "session_not_found_starting_new",
          session_id=session_id,
        )
        # If the provider supplied an unknown session, start a fresh one rather
        # than failing the turn. The provider will receive the new session_id
        # via session_init on the next response.
      except Exception as exc:
        logger.warning("session_switch_failed", error=str(exc))

    self._session_id = self._orchestrator.session_id

  @staticmethod
  def _extract_prompt(msg: ChatRequest) -> str:
    """Return the content of the last user message, if any."""
    for m in reversed(msg.messages):
      if m.role == "user":
        return m.content
    return ""

  async def _consume_stream(self, stream: AsyncIterator[StreamEvent]) -> None:
    """Translate nonoka StreamEvents into bridge events."""
    try:
      async for event in stream:
        for outbound in translate_stream_event(event):
          await self._send(outbound)
    except Exception as exc:
      logger.error("stream_consumption_failed", error=str(exc))
      await self._send(ErrorEvent(message=f"Stream failed: {exc}"))
      await self._send(FinishEvent(finish_reason="error"))

  def reset_session_init(self) -> None:
    """Reset the session-init flag so the next response emits it again.

    Used when the provider resets the conversation (e.g. ``/new``).
    """
    self._session_init_sent = False
    self._session_id = None

  async def shutdown(self) -> None:
    """Gracefully shut down the orchestrator."""
    if self._orchestrator is not None:
      try:
        await self._orchestrator.shutdown()
      except Exception as exc:
        logger.error("orchestrator_shutdown_failed", error=str(exc))
      finally:
        self._orchestrator = None


def build_sender(stdout: Any) -> Any:
  """Build an async sender that writes NDJSON to *stdout* and drains."""

  async def _send(msg: OutboundMessage) -> None:
    try:
      line = encode_outbound_message(msg) + "\n"
      stdout.write(line.encode("utf-8"))
      await stdout.drain()
    except Exception as exc:
      logger.error("send_failed", error=str(exc))

  return _send
