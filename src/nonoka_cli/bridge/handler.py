"""Handle a single chat request in the nonoka-cli --server mode."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from importlib import metadata
from pathlib import Path
from typing import Any

import structlog
from nonoka.core.errors import RuntimeTerminatedError
from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.events import translate_stream_event
from nonoka_cli.bridge.protocol import (
  BRIDGE_CAPABILITIES,
  BRIDGE_PROTOCOL_VERSION,
  ChatMessage,
  ChatRequest,
  DebugEvent,
  ErrorEvent,
  FinishEvent,
  OutboundMessage,
  ProtocolAckEvent,
  SessionInitEvent,
  encode_outbound_message,
)
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.core.task_state import TaskStateService
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy
from nonoka_cli.sessions.manager import project_event_db_path, project_session_db_path
from nonoka_cli.utils.errors import SessionNotFoundError
from nonoka_cli.utils.trace_logger import TraceLogger

logger = structlog.get_logger("nonoka_cli.bridge.handler")


def _package_version(distribution: str) -> str:
  """Resolve an installed distribution version without breaking the bridge."""
  try:
    return metadata.version(distribution)
  except metadata.PackageNotFoundError:
    return "unknown"


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
    self._task_state_service: TaskStateService | None = None
    self._debug_enabled = os.environ.get("NONOKA_DEBUG", "").lower() in {"1", "true", "yes"}

  async def send(self, msg: OutboundMessage) -> None:
    """Send a single outbound message."""
    await self._send(msg)

  async def _emit_debug(
    self,
    message: str,
    payload: dict[str, Any] | None = None,
    level: str = "info",
  ) -> None:
    """Emit a debug event when NONOKA_DEBUG is enabled."""
    if not self._debug_enabled:
      return
    await self._send(
      DebugEvent(
        level=level,  # type: ignore[arg-type]
        message=message,
        payload=payload,
      )
    )

  @property
  def orchestrator(self) -> Orchestrator | None:
    """Current orchestrator instance, if any."""
    return self._orchestrator

  async def handle(self, msg: ChatRequest) -> None:
    """Handle a chat request end-to-end."""
    if not await self._negotiate_protocol(msg):
      return
    try:
      await self._ensure_orchestrator(msg)
    except Exception as exc:
      logger.error("orchestrator_init_failed", error=str(exc))
      await self._send(ErrorEvent(message=f"Initialization failed: {exc}"))
      return

    if self._orchestrator is None:
      await self._send(ErrorEvent(message="Orchestrator not available"))
      return

    # Provider explicitly requested a brand-new nonoka session (e.g. /new).
    if msg.new_session:
      new_id = await self._orchestrator.new_session()
      self._session_id = new_id
      self._session_init_sent = False
      logger.info("new_session_created", session_id=new_id)

    # Use provided session_id or the orchestrator's current session.
    await self._apply_session(msg.session_id)

    # Let the provider know the session id on the first successful response.
    if not self._session_init_sent and self._session_id:
      await self._send(SessionInitEvent(session_id=self._session_id))
      self._session_init_sent = True

    trace_logger = TraceLogger(request_id=msg.request_id)
    trace_logger.log_request(
      session_id=self._session_id,
      cwd=str(self._working_dir),
      message_count=len(msg.messages),
      roles=[m.role for m in msg.messages],
      tools=[t.name for t in (msg.tools or [])],
    )

    await self._emit_debug(
      "chat_request_received",
      payload={
        "session_id": self._session_id,
        "working_dir": str(self._working_dir),
        "message_count": len(msg.messages),
        "roles": [m.role for m in msg.messages],
        "tool_names": [t.name for t in (msg.tools or [])],
      },
    )

    # When the host forwards tool definitions, use the external-tool path so
    # the host can execute the tools and handle HITL itself.
    external_tools = self._build_external_tools(msg)
    external_mcp_servers = msg.external_mcp_servers
    external_skills = msg.external_skills
    config = getattr(self._orchestrator, "config", None)
    tool_output = getattr(config, "tool_output", None)
    output_policy = ToolOutputPolicy.from_config(
      tool_output.model_dump() if tool_output is not None else None
    )
    tool_results = self._extract_tool_results(msg, output_policy)
    host_system_prompt = self._extract_host_system_prompt(msg)

    if msg.purpose == "title":
      prompt = self._extract_prompt(msg)
      if not prompt:
        await self._send(ErrorEvent(message="No title prompt found in chat request"))
        return
      stream = self._orchestrator.execute_title(
        prompt=prompt,
        working_dir=self._working_dir,
      )
    elif external_tools or external_mcp_servers or external_skills:
      if tool_results:
        # Resume after external tool execution: do not re-inject old user or
        # assistant messages. The checkpoint already contains the pending
        # assistant tool_calls; we only need the fresh tool results.
        sanitized = self._sanitize_messages_for_resume(msg)
        logger.debug(
          "resuming_external_tools",
          original_messages=len(msg.messages),
          sanitized_messages=len(sanitized),
          roles=[m.role for m in sanitized],
        )
        stream = self._orchestrator.resume_external_tools(
          session_id=self._session_id or self._orchestrator.session_id,
          results=tool_results,
          tools=external_tools or [],
          working_dir=self._working_dir,
          host_system_prompt=host_system_prompt,
          external_mcp_servers=external_mcp_servers,
          external_skills=external_skills,
        )
      else:
        prompt = self._extract_prompt(msg)
        if not prompt:
          await self._send(ErrorEvent(message="No user message found in chat request"))
          return
        stream = self._orchestrator.execute_with_external_tools(
          prompt=prompt,
          tools=external_tools or [],
          working_dir=self._working_dir,
          host_system_prompt=host_system_prompt,
          external_mcp_servers=external_mcp_servers,
          external_skills=external_skills,
        )
    else:
      # If the provider sent tool-approval-response parts, resume the paused turn.
      approvals = self._extract_approvals(msg)
      if approvals:
        stream = self._orchestrator.resume_approval(
          session_id=self._session_id or self._orchestrator.session_id,
          approvals=approvals,
          working_dir=self._working_dir,
        )
      else:
        prompt = self._extract_prompt(msg)
        if not prompt:
          await self._send(ErrorEvent(message="No user message found in chat request"))
          return
        stream = self._orchestrator.execute(prompt, working_dir=self._working_dir)

    await self._emit_debug(
      "stream_prepared",
      payload={
        "mode": "external_tools"
        if (external_tools or external_mcp_servers or external_skills)
        else "local",
        "is_resume": bool(tool_results),
        "has_approvals": bool(self._extract_approvals(msg))
        if not (external_tools or external_mcp_servers or external_skills)
        else False,
        "session_id": self._session_id,
      },
    )

    await self._consume_stream(stream, trace_logger)

  async def _negotiate_protocol(self, msg: ChatRequest) -> bool:
    """Verify the provider contract before creating or resuming a session."""
    contract = msg.protocol
    if contract is None:
      await self._send(
        ErrorEvent(
          message="Provider did not declare a bridge protocol contract.",
          code="protocol_contract_required",
          retryable=False,
          details={"supported_version": BRIDGE_PROTOCOL_VERSION},
        )
      )
      return False

    requested_major = contract.version.split(".", 1)[0]
    supported_major = BRIDGE_PROTOCOL_VERSION.split(".", 1)[0]
    missing = sorted(set(contract.required_capabilities) - BRIDGE_CAPABILITIES)
    if requested_major != supported_major or missing:
      await self._send(
        ErrorEvent(
          message="Bridge protocol is incompatible with the provider request.",
          code="protocol_incompatible",
          retryable=False,
          details={
            "requested_version": contract.version,
            "supported_version": BRIDGE_PROTOCOL_VERSION,
            "missing_capabilities": missing,
            "supported_capabilities": sorted(BRIDGE_CAPABILITIES),
          },
        )
      )
      return False

    await self._send(
      ProtocolAckEvent(
        capabilities=sorted(BRIDGE_CAPABILITIES),
        cli_version=_package_version("nonoka-cli"),
        framework_version=_package_version("nonoka"),
      )
    )
    return True

  async def _ensure_orchestrator(self, msg: ChatRequest) -> None:
    """Initialize the orchestrator on first request."""
    if self._orchestrator is not None:
      if self._has_generation_options(msg):
        self._orchestrator.set_generation_options(
          max_turns=msg.max_turns,
          temperature=msg.temperature,
          timeout_seconds=msg.timeout_seconds,
          wall_timeout_seconds=msg.wall_timeout_seconds,
          tool_budget=msg.tool_budget,
          max_context_bytes=msg.max_context_bytes,
          max_external_result_bytes=msg.max_external_result_bytes,
          require_workspace_mutation=msg.require_workspace_mutation,
          require_observed_effect=msg.require_observed_effect,
          require_focused_verification=msg.require_focused_verification,
          verification_enforcement=msg.verification_enforcement,
          max_completion_corrections=msg.max_completion_corrections,
        )
      return

    self._working_dir = Path(msg.cwd or ".").resolve()

    self._orchestrator = Orchestrator(
      db_path=project_session_db_path(self._working_dir),
      event_db_path=project_event_db_path(self._working_dir),
    )
    await self._orchestrator.initialize(config_path=self._config_path)
    if self._has_generation_options(msg):
      self._orchestrator.set_generation_options(
        max_turns=msg.max_turns,
        temperature=msg.temperature,
        timeout_seconds=msg.timeout_seconds,
        wall_timeout_seconds=msg.wall_timeout_seconds,
        tool_budget=msg.tool_budget,
        max_context_bytes=msg.max_context_bytes,
        max_external_result_bytes=msg.max_external_result_bytes,
        require_workspace_mutation=msg.require_workspace_mutation,
        require_observed_effect=msg.require_observed_effect,
        require_focused_verification=msg.require_focused_verification,
        verification_enforcement=msg.verification_enforcement,
        max_completion_corrections=msg.max_completion_corrections,
      )

    self._task_state_service = TaskStateService(
      tasks_dir=self._orchestrator.config.task_state.tasks_dir,
      enabled=self._orchestrator.config.task_state.enabled,
      base_dir=self._working_dir,
    )
    if self._model:
      await self._orchestrator.switch_model(self._model)
    elif msg.model:
      await self._orchestrator.switch_model(msg.model)

    self._session_id = self._orchestrator.session_id

  @staticmethod
  def _has_generation_options(msg: ChatRequest) -> bool:
    """Return whether this request carries a non-default benchmark override."""
    return any(
      value is not None
      for value in (
        msg.max_turns,
        msg.temperature,
        msg.timeout_seconds,
        msg.wall_timeout_seconds,
        msg.tool_budget,
        msg.max_context_bytes,
        msg.max_external_result_bytes,
      )
    ) or (
      msg.require_workspace_mutation
      or msg.require_observed_effect
      or msg.require_focused_verification
      or msg.max_completion_corrections != 1
    )

  async def _apply_session(self, session_id: str | None) -> None:
    """Switch orchestrator session if the provider sent a different one."""
    if self._orchestrator is None:
      self._session_id = None
      return

    if not session_id:
      self._session_id = self._orchestrator.session_id
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

  @staticmethod
  def _extract_approvals(msg: ChatRequest) -> dict[str, dict[str, Any]] | None:
    """Parse tool-approval-response parts from incoming tool messages.

    The Vercel AI SDK / OpenCode sends approval decisions as parts inside a
    ``role="tool"`` message.  We map ``toolCallId`` to a decision dict.
    """
    approvals: dict[str, dict[str, Any]] = {}
    for m in msg.messages:
      if m.role != "tool" or not m.content:
        continue
      try:
        parts = json.loads(m.content)
      except json.JSONDecodeError:
        continue
      if not isinstance(parts, list):
        continue
      for part in parts:
        if not isinstance(part, dict):
          continue
        if part.get("type") != "tool-approval-response":
          continue
        tool_call_id = part.get("toolCallId") or part.get("tool_call_id")
        if not tool_call_id:
          continue
        decision: dict[str, Any] = {"approved": bool(part.get("approved", False))}
        modified = part.get("modifiedArgs") or part.get("modified_args")
        if modified is not None:
          decision["modified_args"] = modified
        reason = part.get("reason")
        if reason is not None:
          decision["reason"] = reason
        approvals[str(tool_call_id)] = decision

    return approvals if approvals else None

  @staticmethod
  def _build_external_tools(msg: ChatRequest) -> list[Any]:
    """Convert incoming external tool definitions into nonoka Capabilities."""
    if not msg.tools:
      return []
    return [
      AgentFactory.create_external_tool_capability(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
      )
      for tool in msg.tools
    ]

  @staticmethod
  def _extract_pending_tool_call_ids(msg: ChatRequest) -> set[str]:
    """Collect tool_call_ids declared by the most recent assistant message."""
    pending: set[str] = set()
    for m in reversed(msg.messages):
      if m.role == "assistant" and m.tool_calls:
        for tc in m.tool_calls:
          pending.add(tc.id)
        break
    return pending

  @staticmethod
  def _extract_host_system_prompt(msg: ChatRequest) -> str | None:
    """Return the first inbound system message, if any.

    OpenCode forwards its agent prompt as a system message. We pass it through
    as a fallback when the user has not configured a custom system_prompt in
    nonoka.yaml.
    """
    for m in msg.messages:
      if m.role == "system":
        return m.content
    return None

  @staticmethod
  def _sanitize_messages_for_resume(msg: ChatRequest) -> list[ChatMessage]:
    """Drop non-tool messages when resuming after external tool execution.

    The nonoka checkpoint already stores the assistant message that emitted the
    pending tool_calls and any earlier history. Re-injecting user/assistant
    messages from the provider would duplicate context and can reorder the
    conversation. We keep only role='tool' messages (the fresh results) plus the
    host system prompt if it was forwarded.
    """
    return [m for m in msg.messages if m.role == "tool"]

  @staticmethod
  def _extract_tool_results(
    msg: ChatRequest,
    output_policy: ToolOutputPolicy | None = None,
  ) -> dict[str, Any] | None:
    """Parse plain tool results from incoming role='tool' messages.

    OpenCode returns tool results as ``role='tool'`` messages with a
    ``tool_call_id``. We skip approval-response parts (handled separately)
    and collect the latest result for each tool_call_id. Results whose id
    does not match a pending tool_call from the latest assistant message are
    dropped to avoid misalignment.
    """
    pending_ids = ChatRequestHandler._extract_pending_tool_call_ids(msg)
    tool_names: dict[str, str] = {}
    for message in reversed(msg.messages):
      if message.role == "assistant" and message.tool_calls:
        tool_names = {tc.id: tc.name for tc in message.tool_calls}
        break
    results: dict[str, Any] = {}
    for m in msg.messages:
      if m.role != "tool" or not m.tool_call_id or (not m.content and m.result is None):
        continue
      # Skip approval-response payloads.
      try:
        parts = json.loads(m.content)
      except json.JSONDecodeError:
        parts = None
      if isinstance(parts, list) and any(
        isinstance(part, dict) and part.get("type") == "tool-approval-response" for part in parts
      ):
        continue
      if pending_ids and m.tool_call_id not in pending_ids:
        logger.warning(
          "tool_result_id_mismatch",
          tool_call_id=m.tool_call_id,
          pending_ids=list(pending_ids),
        )
        continue
      value = m.result if m.result is not None else m.content
      if output_policy is not None and output_policy.enabled:
        value = output_policy.apply_external_receipt(
          tool_names.get(m.tool_call_id, ""), value, m.tool_call_id
        )
      results[m.tool_call_id] = value

    return results if results else None

  async def _consume_stream(
    self,
    stream: AsyncIterator[StreamEvent],
    trace_logger: TraceLogger | None = None,
  ) -> None:
    """Translate nonoka StreamEvents into bridge events."""
    try:
      async for event in stream:
        if trace_logger is not None:
          trace_logger.log_stream_event(
            session_id=self._session_id,
            event_type=event.type,
            data=self._summarize_stream_event(event),
          )
          trace = event.data.get("trace") if event.data else None
          completed_run = event.type == "error" or not (
            event.data.get("requires_external_execution") or event.data.get("requires_approval")
          )
          if completed_run and isinstance(trace, dict):
            trace_logger.log_execution_trace(self._session_id, trace)
        self._sync_task_state(event)
        for outbound in translate_stream_event(event):
          await self._send(outbound)
    except RuntimeTerminatedError as exc:
      termination = exc.termination.model_dump(mode="json")
      logger.error(
        "stream_runtime_terminated",
        reason=termination.get("reason"),
        diagnostics=termination.get("diagnostics"),
      )
      await self._send(
        ErrorEvent(
          message=exc.termination.message,
          code=exc.termination.reason.value,
          retryable=False,
          details={"termination": termination},
        )
      )
      await self._send(
        FinishEvent(
          finish_reason="error",
          termination=termination,
        )
      )
    except Exception as exc:
      logger.error("stream_consumption_failed", error=str(exc))
      await self._send(ErrorEvent(message=f"Stream failed: {exc}"))
      await self._send(FinishEvent(finish_reason="error"))

  def _sync_task_state(self, event: StreamEvent) -> None:
    """Mirror ``todowrite`` calls into the local task-state file."""
    if (
      self._task_state_service is None
      or not self._task_state_service.enabled
      or event.type != "tool_call_start"
    ):
      return

    for tc in event.data.get("tool_calls") or []:
      func = tc.get("function", {})
      if func.get("name") != "todowrite":
        continue
      args = func.get("arguments", "{}")
      if isinstance(args, str):
        try:
          args = json.loads(args)
        except json.JSONDecodeError:
          continue
      if not isinstance(args, dict):
        continue
      todos = args.get("todos")
      if not isinstance(todos, list):
        continue
      if self._orchestrator is not None:
        session_id = self._session_id or self._orchestrator.session_id
      else:
        session_id = self._session_id or "unknown"
      self._task_state_service.sync_from_todowrite(
        session_id=session_id,
        todos=todos,
      )

  @staticmethod
  def _summarize_stream_event(event: StreamEvent) -> dict[str, Any]:
    """Return a compact, trace-friendly summary of a StreamEvent."""
    data = event.data or {}
    summary: dict[str, Any] = {}
    if event.type == "content_delta":
      content = data.get("content", "")
      summary["len"] = len(content)
      summary["has_newline"] = "\n" in content
    elif event.type == "tool_call_start":
      summary["tool_calls"] = [
        {
          "id": tc.get("id") or tc.get("tool_call_id"),
          "name": tc.get("function", {}).get("name"),
        }
        for tc in (data.get("tool_calls") or [])
      ]
    elif event.type == "tool_call_result":
      summary["tool_call_id"] = data.get("tool_call_id")
      summary["name"] = data.get("name")
      summary["is_error"] = bool(data.get("is_error", False))
    elif event.type == "approval_request":
      summary["tool_call_id"] = data.get("tool_call_id")
      summary["tool_name"] = data.get("tool_name")
    elif event.type == "final":
      summary["success"] = bool(data.get("success", False))
      summary["requires_external_execution"] = bool(data.get("requires_external_execution", False))
      summary["requires_approval"] = bool(data.get("requires_approval", False))
    elif event.type == "error":
      summary["error"] = data.get("error")
      summary["error_type"] = data.get("error_type")
    return summary

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
